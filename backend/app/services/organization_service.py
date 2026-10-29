import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import OrganizationMember, Role
from app.schemas.organization import InviteMemberRequest


async def list_members(db: AsyncSession, organization_id: uuid.UUID) -> list[OrganizationMember]:
    result = await db.execute(
        select(OrganizationMember).where(OrganizationMember.organization_id == organization_id)
    )
    return list(result.scalars().all())


async def invite_member(
    db: AsyncSession, organization_id: uuid.UUID, payload: InviteMemberRequest
) -> OrganizationMember:
    role = (
        await db.execute(select(Role).where(Role.name == payload.role, Role.organization_id.is_(None)))
    ).scalar_one_or_none()
    if not role:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown role: {payload.role}")

    existing = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.invited_email == payload.email,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Already invited")

    member = OrganizationMember(
        organization_id=organization_id,
        role_id=role.id,
        invited_email=payload.email,
        status="INVITED",
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member
