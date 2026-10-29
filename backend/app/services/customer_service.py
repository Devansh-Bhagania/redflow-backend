import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.schemas.customer import CustomerCreate


async def create_customer(db: AsyncSession, organization_id: uuid.UUID, payload: CustomerCreate) -> Customer:
    existing = await db.execute(
        select(Customer).where(
            Customer.organization_id == organization_id, Customer.customer_code == payload.customer_code
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "customer_code already in use")

    customer = Customer(organization_id=organization_id, **payload.model_dump())
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer


async def list_customers(db: AsyncSession, organization_id: uuid.UUID) -> list[Customer]:
    result = await db.execute(select(Customer).where(Customer.organization_id == organization_id))
    return list(result.scalars().all())


async def get_customer(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
    result = await db.execute(
        select(Customer).where(Customer.organization_id == organization_id, Customer.id == customer_id)
    )
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return customer
