import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.schemas.product import ProductCreate, ProductOut
from app.services import product_service

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=list[ProductOut])
async def list_products(
    ctx: TenantContext = Depends(require_permission("products.read")), db: AsyncSession = Depends(get_db)
):
    return await product_service.list_products(db, ctx.organization_id)


@router.post("", response_model=ProductOut, status_code=201)
async def create_product(
    payload: ProductCreate,
    ctx: TenantContext = Depends(require_permission("products.create")),
    db: AsyncSession = Depends(get_db),
):
    return await product_service.create_product(db, ctx.organization_id, payload)


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(
    product_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("products.read")),
    db: AsyncSession = Depends(get_db),
):
    return await product_service.get_product(db, ctx.organization_id, product_id)
