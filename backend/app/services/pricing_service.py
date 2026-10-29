import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.pricing import PriceListItem
from app.models.product import Product, ProductVariant


async def calculate_customer_price(
    db: AsyncSession, customer: Customer, variant: ProductVariant, quantity: int
) -> Decimal:
    """The single source of truth for what a customer pays per unit.

    Resolution order: best matching volume tier on the customer's assigned
    price list, then the variant's price override, then the product's base
    price. The AI layer calls this — it never invents a price.
    """
    if customer.price_list_id:
        result = await db.execute(
            select(PriceListItem)
            .where(
                PriceListItem.price_list_id == customer.price_list_id,
                PriceListItem.product_variant_id == variant.id,
                PriceListItem.min_quantity <= quantity,
            )
            .order_by(PriceListItem.min_quantity.desc())
            .limit(1)
        )
        item = result.scalar_one_or_none()
        if item:
            return item.unit_price

    if variant.price_override is not None:
        return variant.price_override

    product = await db.get(Product, variant.product_id)
    return product.base_price
