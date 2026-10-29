import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.schemas.inventory import (
    InventoryOut,
    StockReceiveRequest,
    WarehouseCreate,
    WarehouseOut,
)
from app.services import inventory_service

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("/warehouses", response_model=list[WarehouseOut])
async def list_warehouses(
    ctx: TenantContext = Depends(require_permission("inventory.read")), db: AsyncSession = Depends(get_db)
):
    return await inventory_service.list_warehouses(db, ctx.organization_id)


@router.post("/warehouses", response_model=WarehouseOut, status_code=201)
async def create_warehouse(
    payload: WarehouseCreate,
    ctx: TenantContext = Depends(require_permission("inventory.update")),
    db: AsyncSession = Depends(get_db),
):
    return await inventory_service.create_warehouse(db, ctx.organization_id, payload)


@router.post("/receive", response_model=InventoryOut)
async def receive_stock(
    payload: StockReceiveRequest,
    ctx: TenantContext = Depends(require_permission("inventory.update")),
    db: AsyncSession = Depends(get_db),
):
    return await inventory_service.receive_stock(
        db, ctx.organization_id, payload.warehouse_id, payload.product_variant_id, payload.quantity
    )


@router.get("/variants/{variant_id}", response_model=list[InventoryOut])
async def get_variant_inventory(
    variant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("inventory.read")),
    db: AsyncSession = Depends(get_db),
):
    return await inventory_service.list_inventory_for_variant(db, ctx.organization_id, variant_id)
