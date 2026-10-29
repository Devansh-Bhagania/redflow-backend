import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.order import Order

NET_TERMS_DAYS = 30


async def create_invoice_for_order(db: AsyncSession, organization_id: uuid.UUID, order: Order) -> Invoice:
    invoice = Invoice(
        organization_id=organization_id,
        invoice_number=f"INV{uuid.uuid4().hex[:8].upper()}",
        order_id=order.id,
        customer_id=order.customer_id,
        amount_due=order.total,
        due_date=datetime.now(timezone.utc) + timedelta(days=NET_TERMS_DAYS),
    )
    db.add(invoice)
    await db.flush()
    return invoice


async def list_invoices(db: AsyncSession, organization_id: uuid.UUID) -> list[Invoice]:
    result = await db.execute(
        select(Invoice).where(Invoice.organization_id == organization_id).order_by(Invoice.due_date)
    )
    return list(result.scalars().all())


async def get_invoice(db: AsyncSession, organization_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
    result = await db.execute(
        select(Invoice).where(Invoice.organization_id == organization_id, Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return invoice


async def list_outstanding_for_customer(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID) -> list[Invoice]:
    result = await db.execute(
        select(Invoice)
        .where(
            Invoice.organization_id == organization_id,
            Invoice.customer_id == customer_id,
            Invoice.status != "PAID",
        )
        .order_by(Invoice.due_date)
    )
    return list(result.scalars().all())


async def get_invoice_by_number_for_customer(
    db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, invoice_number: str
) -> Invoice:
    result = await db.execute(
        select(Invoice).where(
            Invoice.organization_id == organization_id,
            Invoice.customer_id == customer_id,
            Invoice.invoice_number == invoice_number,
        )
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return invoice


async def record_payment(
    db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, invoice_number: str, amount
) -> Invoice:
    invoice = await get_invoice_by_number_for_customer(db, organization_id, customer_id, invoice_number)
    if invoice.status == "PAID":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invoice is already fully paid")

    invoice.amount_paid += amount
    invoice.status = "PAID" if invoice.amount_paid >= invoice.amount_due else "PARTIAL"
    db.add(invoice)
    await db.commit()
    await db.refresh(invoice)
    return invoice
