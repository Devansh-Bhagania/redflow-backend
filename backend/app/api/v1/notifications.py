from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, get_current_context
from app.schemas.notification import NotificationOut
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    ctx: TenantContext = Depends(get_current_context), db: AsyncSession = Depends(get_db)
):
    return await notification_service.list_notifications(db, ctx.organization_id)
