from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.schemas.organization import InviteMemberRequest, MemberOut
from app.services import organization_service

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("/members", response_model=list[MemberOut])
async def list_members(
    ctx: TenantContext = Depends(require_permission("team.manage")),
    db: AsyncSession = Depends(get_db),
):
    members = await organization_service.list_members(db, ctx.organization_id)
    return [
        MemberOut(id=m.id, user_id=m.user_id, invited_email=m.invited_email, role=m.role.name, status=m.status)
        for m in members
    ]


@router.post("/members/invite", response_model=MemberOut, status_code=201)
async def invite_member(
    payload: InviteMemberRequest,
    ctx: TenantContext = Depends(require_permission("team.manage")),
    db: AsyncSession = Depends(get_db),
):
    m = await organization_service.invite_member(db, ctx.organization_id, payload)
    return MemberOut(id=m.id, user_id=m.user_id, invited_email=m.invited_email, role=m.role.name, status=m.status)
