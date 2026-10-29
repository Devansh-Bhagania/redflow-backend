import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models.organization import Organization
from app.models.rbac import OrganizationMember, Role
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "org"
    return f"{base}-{uuid.uuid4().hex[:6]}"


async def _issue_tokens(db: AsyncSession, user: User, org_id: uuid.UUID, role_name: str) -> TokenResponse:
    access_token = create_access_token(user_id=str(user.id), organization_id=str(org_id), role=role_name)
    refresh_token = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(refresh_token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    await db.commit()
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


async def register(db: AsyncSession, payload: RegisterRequest) -> TokenResponse:
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    owner_role = (
        await db.execute(select(Role).where(Role.name == "OWNER", Role.organization_id.is_(None)))
    ).scalar_one_or_none()
    if not owner_role:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "System roles not seeded")

    user = User(email=payload.email, hashed_password=hash_password(payload.password), full_name=payload.full_name)
    org = Organization(name=payload.organization_name, slug=_slugify(payload.organization_name))
    db.add_all([user, org])
    await db.flush()

    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role_id=owner_role.id, status="ACTIVE"))
    await db.commit()

    return await _issue_tokens(db, user, org.id, owner_role.name)


async def login(db: AsyncSession, payload: LoginRequest) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.hashed_password) or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    membership = (
        await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.user_id == user.id, OrganizationMember.status == "ACTIVE"
            )
        )
    ).scalars().first()
    if not membership:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User has no active organization")

    return await _issue_tokens(db, user, membership.organization_id, membership.role.name)


async def refresh(db: AsyncSession, refresh_token: str) -> TokenResponse:
    token_hash = hash_token(refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    expires_at = stored.expires_at.replace(tzinfo=timezone.utc) if stored and stored.expires_at.tzinfo is None else (stored.expires_at if stored else None)
    if not stored or stored.revoked_at or expires_at < now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    # rotate: revoke the old token, issue a new pair
    stored.revoked_at = now

    user = await db.get(User, stored.user_id)
    membership = (
        await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.user_id == user.id, OrganizationMember.status == "ACTIVE"
            )
        )
    ).scalars().first()
    if not user or not user.is_active or not membership:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    return await _issue_tokens(db, user, membership.organization_id, membership.role.name)


async def logout(db: AsyncSession, refresh_token: str) -> None:
    token_hash = hash_token(refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    if stored and not stored.revoked_at:
        stored.revoked_at = datetime.now(timezone.utc)
        await db.commit()
