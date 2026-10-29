import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import decode_access_token
from app.models.rbac import OrganizationMember
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


class TenantContext:
    """Authenticated identity, scoped to a single organization membership.

    organization_id here comes from the verified JWT claim, never from a
    client-supplied path/query/body parameter.
    """

    def __init__(self, user: User, organization_id: uuid.UUID, membership: OrganizationMember):
        self.user = user
        self.organization_id = organization_id
        self.membership = membership

    def has_permission(self, code: str) -> bool:
        return any(p.code == code for p in self.membership.role.permissions)


async def get_current_context(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    payload = decode_access_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    user_id = payload.get("sub")
    org_id = payload.get("org_id")
    user = await db.get(User, uuid.UUID(user_id))
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")

    result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.user_id == user.id,
            OrganizationMember.organization_id == uuid.UUID(org_id),
            OrganizationMember.status == "ACTIVE",
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No active membership in this organization")

    return TenantContext(user=user, organization_id=uuid.UUID(org_id), membership=membership)


def require_permission(code: str):
    async def _check(ctx: TenantContext = Depends(get_current_context)) -> TenantContext:
        if not ctx.has_permission(code):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {code}")
        return ctx

    return _check
