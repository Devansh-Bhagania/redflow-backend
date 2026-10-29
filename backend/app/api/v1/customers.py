import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.schemas.customer import CustomerCreate, CustomerOut
from app.services import customer_service

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=list[CustomerOut])
async def list_customers(
    ctx: TenantContext = Depends(require_permission("customers.read")), db: AsyncSession = Depends(get_db)
):
    return await customer_service.list_customers(db, ctx.organization_id)


@router.post("", response_model=CustomerOut, status_code=201)
async def create_customer(
    payload: CustomerCreate,
    ctx: TenantContext = Depends(require_permission("customers.create")),
    db: AsyncSession = Depends(get_db),
):
    return await customer_service.create_customer(db, ctx.organization_id, payload)


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(
    customer_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("customers.read")),
    db: AsyncSession = Depends(get_db),
):
    return await customer_service.get_customer(db, ctx.organization_id, customer_id)
