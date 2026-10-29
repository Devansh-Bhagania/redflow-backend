import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.schemas.order import OrderFromQuotationRequest, OrderOut
from app.services import order_service

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=list[OrderOut])
async def list_orders(
    ctx: TenantContext = Depends(require_permission("orders.read")), db: AsyncSession = Depends(get_db)
):
    return await order_service.list_orders(db, ctx.organization_id)


@router.post("", response_model=OrderOut, status_code=201)
async def create_order(
    payload: OrderFromQuotationRequest,
    ctx: TenantContext = Depends(require_permission("orders.create")),
    db: AsyncSession = Depends(get_db),
):
    return await order_service.create_order_from_quotation(
        db, ctx.organization_id, payload.quotation_id, payload.idempotency_key
    )


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(
    order_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("orders.read")),
    db: AsyncSession = Depends(get_db),
):
    return await order_service.get_order(db, ctx.organization_id, order_id)
