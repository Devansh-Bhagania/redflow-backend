import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.quotation import Quotation, QuotationItem
from app.schemas.quotation import QuotationCreate
from app.services import customer_service, pricing_service, product_service


async def create_quotation(
    db: AsyncSession, organization_id: uuid.UUID, payload: QuotationCreate, created_by: str
) -> Quotation:
    if not payload.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A quotation needs at least one item")

    customer = await customer_service.get_customer(db, organization_id, payload.customer_id)

    subtotal = Decimal("0")
    tax = Decimal("0")
    items: list[QuotationItem] = []

    for line in payload.items:
        variant = await product_service.get_variant(db, organization_id, line.product_variant_id)
        product = await db.get(Product, variant.product_id)
        unit_price = await pricing_service.calculate_customer_price(db, customer, variant, line.quantity)
        line_total = unit_price * line.quantity
        subtotal += line_total
        tax += line_total * (product.tax_rate / Decimal("100"))
        items.append(
            QuotationItem(
                product_variant_id=variant.id,
                quantity=line.quantity,
                unit_price=unit_price,
                discount=Decimal("0"),
                line_total=line_total,
            )
        )

    quotation = Quotation(
        organization_id=organization_id,
        quote_number=f"Q{uuid.uuid4().hex[:8].upper()}",
        customer_id=payload.customer_id,
        status="DRAFT",
        subtotal=subtotal,
        tax=tax,
        shipping=Decimal("0"),
        total=subtotal + tax,
        valid_until=payload.valid_until,
        terms=payload.terms,
        created_by=created_by,
        items=items,
    )
    db.add(quotation)
    await db.commit()
    await db.refresh(quotation)
    return quotation


async def get_quotation(db: AsyncSession, organization_id: uuid.UUID, quotation_id: uuid.UUID) -> Quotation:
    result = await db.execute(
        select(Quotation).where(Quotation.organization_id == organization_id, Quotation.id == quotation_id)
    )
    quotation = result.scalar_one_or_none()
    if not quotation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quotation not found")
    return quotation


async def get_quotation_by_number(db: AsyncSession, organization_id: uuid.UUID, quote_number: str) -> Quotation:
    result = await db.execute(
        select(Quotation).where(
            Quotation.organization_id == organization_id, Quotation.quote_number == quote_number
        )
    )
    quotation = result.scalar_one_or_none()
    if not quotation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quotation not found")
    return quotation


async def list_quotations(db: AsyncSession, organization_id: uuid.UUID) -> list[Quotation]:
    result = await db.execute(select(Quotation).where(Quotation.organization_id == organization_id))
    return list(result.scalars().all())


async def list_quotations_for_customer(
    db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID
) -> list[Quotation]:
    result = await db.execute(
        select(Quotation)
        .where(Quotation.organization_id == organization_id, Quotation.customer_id == customer_id)
        .order_by(Quotation.created_at.desc())
    )
    return list(result.scalars().all())


async def send_quotation(db: AsyncSession, organization_id: uuid.UUID, quotation_id: uuid.UUID) -> Quotation:
    quotation = await get_quotation(db, organization_id, quotation_id)
    if quotation.status != "DRAFT":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Cannot send a quotation in status {quotation.status}")
    quotation.status = "SENT"
    db.add(quotation)
    await db.commit()
    await db.refresh(quotation)
    return quotation


async def accept_quotation(db: AsyncSession, organization_id: uuid.UUID, quotation_id: uuid.UUID) -> Quotation:
    quotation = await get_quotation(db, organization_id, quotation_id)
    if quotation.status not in ("SENT", "VIEWED"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Cannot accept a quotation in status {quotation.status}")
    quotation.status = "ACCEPTED"
    db.add(quotation)
    await db.commit()
    await db.refresh(quotation)
    return quotation
