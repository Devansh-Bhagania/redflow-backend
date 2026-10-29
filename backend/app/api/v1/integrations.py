from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.schemas.integration import ConnectEmailRequest, ConnectWhatsAppRequest, IntegrationConnectionOut
from app.services import integration_service

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.post("/whatsapp/connect", response_model=IntegrationConnectionOut, status_code=201)
async def connect_whatsapp(
    payload: ConnectWhatsAppRequest,
    ctx: TenantContext = Depends(require_permission("integrations.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await integration_service.connect_whatsapp(db, ctx.organization_id, payload)


@router.post("/email/connect", response_model=IntegrationConnectionOut, status_code=201)
async def connect_email(
    payload: ConnectEmailRequest,
    ctx: TenantContext = Depends(require_permission("integrations.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await integration_service.connect_email(db, ctx.organization_id, payload)
