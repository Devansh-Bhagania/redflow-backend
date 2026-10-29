from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.schemas.analytics import AnalyticsSummary
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=AnalyticsSummary)
async def get_summary(
    ctx: TenantContext = Depends(require_permission("analytics.read")), db: AsyncSession = Depends(get_db)
):
    return await analytics_service.get_summary(db, ctx.organization_id)
