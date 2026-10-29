from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, get_current_context
from app.models.organization import Organization
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    OrganizationOut,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    return await auth_service.register(db, payload)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    return await auth_service.login(db, payload)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    return await auth_service.refresh(db, payload.refresh_token)


@router.post("/logout", status_code=204)
async def logout(payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    await auth_service.logout(db, payload.refresh_token)


@router.get("/me", response_model=MeResponse)
async def me(ctx: TenantContext = Depends(get_current_context), db: AsyncSession = Depends(get_db)):
    org = await db.get(Organization, ctx.organization_id)
    return MeResponse(
        user=UserOut.model_validate(ctx.user),
        organization=OrganizationOut.model_validate(org),
        role=ctx.membership.role.name,
        permissions=[p.code for p in ctx.membership.role.permissions],
    )
