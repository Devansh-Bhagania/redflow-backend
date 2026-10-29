import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderItem, OrderStatusHistory
from app.services import invoice_service, inventory_service, quotation_service


async def create_order_from_quotation(
    db: AsyncSession, organization_id: uuid.UUID, quotation_id: uuid.UUID, idempotency_key: str
) -> Order:
    existing = await db.execute(
        select(Order).where(Order.organization_id == organization_id, Order.idempotency_key == idempotency_key)
    )
    existing_order = existing.scalar_one_or_none()
    if existing_order:
        return existing_order

    quotation = await quotation_service.get_quotation(db, organization_id, quotation_id)
    if quotation.status not in ("SENT", "VIEWED", "ACCEPTED"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Cannot convert a quotation in status {quotation.status}")

    try:
        order = Order(
            organization_id=organization_id,
            order_number=f"ORD{uuid.uuid4().hex[:8].upper()}",
            customer_id=quotation.customer_id,
            quotation_id=quotation.id,
            status="CONFIRMED",
            subtotal=quotation.subtotal,
            tax=quotation.tax,
            shipping=quotation.shipping,
            total=quotation.total,
            idempotency_key=idempotency_key,
            items=[
                OrderItem(
                    product_variant_id=i.product_variant_id,
                    quantity=i.quantity,
                    unit_price=i.unit_price,
                    line_total=i.line_total,
                )
                for i in quotation.items
            ],
        )
        db.add(order)
        await db.flush()

        for item in quotation.items:
            await inventory_service.reserve(
                db, organization_id, item.product_variant_id, item.quantity, "ORDER", order.id
            )

        quotation.status = "CONVERTED"
        db.add(quotation)
        db.add(OrderStatusHistory(order_id=order.id, from_status=None, to_status="CONFIRMED"))
        await invoice_service.create_invoice_for_order(db, organization_id, order)
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    await db.refresh(order)
    return order


async def get_order(db: AsyncSession, organization_id: uuid.UUID, order_id: uuid.UUID) -> Order:
    result = await db.execute(select(Order).where(Order.organization_id == organization_id, Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return order


async def list_orders(db: AsyncSession, organization_id: uuid.UUID) -> list[Order]:
    result = await db.execute(select(Order).where(Order.organization_id == organization_id))
    return list(result.scalars().all())


async def get_order_by_number_for_customer(
    db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, order_number: str
) -> Order:
    result = await db.execute(
        select(Order).where(
            Order.organization_id == organization_id,
            Order.customer_id == customer_id,
            Order.order_number == order_number,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return order


async def list_orders_for_customer(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID) -> list[Order]:
    result = await db.execute(
        select(Order)
        .where(Order.organization_id == organization_id, Order.customer_id == customer_id)
        .order_by(Order.created_at.desc())
    )
    return list(result.scalars().all())
