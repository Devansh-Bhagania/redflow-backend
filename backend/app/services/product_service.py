import uuid

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product, ProductVariant
from app.schemas.product import ProductCreate


async def create_product(db: AsyncSession, organization_id: uuid.UUID, payload: ProductCreate) -> Product:
    data = payload.model_dump(exclude={"variants"})
    product = Product(organization_id=organization_id, **data)
    db.add(product)
    await db.flush()

    for variant_payload in payload.variants:
        db.add(ProductVariant(product_id=product.id, **variant_payload.model_dump()))

    await db.commit()
    await db.refresh(product)
    return product


async def list_products(db: AsyncSession, organization_id: uuid.UUID) -> list[Product]:
    result = await db.execute(select(Product).where(Product.organization_id == organization_id))
    return list(result.scalars().all())


async def get_product(db: AsyncSession, organization_id: uuid.UUID, product_id: uuid.UUID) -> Product:
    result = await db.execute(
        select(Product).where(Product.organization_id == organization_id, Product.id == product_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return product


async def search_products(db: AsyncSession, organization_id: uuid.UUID, query: str) -> list[Product]:
    """Matches a natural-language query like "cotton polo shirt" against the
    catalog word-by-word (each word must appear somewhere in name/sku, not as
    one contiguous phrase) — a whole-string ILIKE never matches a real product
    name unless the customer's wording happens to be an exact substring."""
    tokens = query.split()
    if not tokens:
        return []

    token_conditions = []
    for token in tokens:
        variants = {token}
        if token.lower().endswith("s") and len(token) > 3:
            variants.add(token[:-1])  # crude singular fallback: "shirts" -> "shirt"
        token_conditions.append(
            or_(*(Product.name.ilike(f"%{v}%") | Product.sku.ilike(f"%{v}%") for v in variants))
        )

    result = await db.execute(
        select(Product).where(
            Product.organization_id == organization_id,
            Product.status == "ACTIVE",
            and_(*token_conditions),
        )
    )
    return list(result.scalars().all())


async def get_variant(db: AsyncSession, organization_id: uuid.UUID, variant_id: uuid.UUID) -> ProductVariant:
    """Loads a variant while proving it belongs to the caller's org, via a join
    on its parent product — variants have no organization_id of their own."""
    result = await db.execute(
        select(ProductVariant)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(ProductVariant.id == variant_id, Product.organization_id == organization_id)
    )
    variant = result.scalar_one_or_none()
    if not variant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product variant not found")
    return variant
