import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import Inventory, InventoryMovement, StockReservation, Warehouse
from app.models.product import ProductVariant
from app.schemas.inventory import WarehouseCreate


async def create_warehouse(db: AsyncSession, organization_id: uuid.UUID, payload: WarehouseCreate) -> Warehouse:
    warehouse = Warehouse(organization_id=organization_id, **payload.model_dump())
    db.add(warehouse)
    await db.commit()
    await db.refresh(warehouse)
    return warehouse


async def list_warehouses(db: AsyncSession, organization_id: uuid.UUID) -> list[Warehouse]:
    result = await db.execute(select(Warehouse).where(Warehouse.organization_id == organization_id))
    return list(result.scalars().all())


async def receive_stock(
    db: AsyncSession, organization_id: uuid.UUID, warehouse_id: uuid.UUID, product_variant_id: uuid.UUID, quantity: int
) -> Inventory:
    result = await db.execute(
        select(Inventory).where(
            Inventory.organization_id == organization_id,
            Inventory.warehouse_id == warehouse_id,
            Inventory.product_variant_id == product_variant_id,
        )
    )
    inv = result.scalar_one_or_none()
    if not inv:
        inv = Inventory(
            organization_id=organization_id, warehouse_id=warehouse_id, product_variant_id=product_variant_id
        )
        db.add(inv)
        await db.flush()

    inv.available_quantity += quantity
    db.add(inv)
    db.add(
        InventoryMovement(
            organization_id=organization_id, inventory_id=inv.id, type="RECEIPT", quantity=quantity
        )
    )
    await db.commit()
    await db.refresh(inv)
    return inv


async def list_inventory_for_variant(
    db: AsyncSession, organization_id: uuid.UUID, product_variant_id: uuid.UUID
) -> list[Inventory]:
    result = await db.execute(
        select(Inventory).where(
            Inventory.organization_id == organization_id, Inventory.product_variant_id == product_variant_id
        )
    )
    return list(result.scalars().all())


async def get_total_available(db: AsyncSession, organization_id: uuid.UUID, product_variant_id: uuid.UUID) -> int:
    result = await db.execute(
        select(Inventory).where(
            Inventory.organization_id == organization_id,
            Inventory.product_variant_id == product_variant_id,
        )
    )
    return sum(row.available_quantity for row in result.scalars().all())


async def list_low_stock(db: AsyncSession, organization_id: uuid.UUID) -> list[dict]:
    result = await db.execute(
        select(Inventory, ProductVariant.sku)
        .join(ProductVariant, Inventory.product_variant_id == ProductVariant.id)
        .where(
            Inventory.organization_id == organization_id,
            Inventory.available_quantity <= Inventory.reorder_level,
        )
    )
    return [
        {
            "product_variant_id": str(inv.product_variant_id),
            "sku": sku,
            "available_quantity": inv.available_quantity,
            "reorder_level": inv.reorder_level,
        }
        for inv, sku in result.all()
    ]


async def reserve(
    db: AsyncSession,
    organization_id: uuid.UUID,
    product_variant_id: uuid.UUID,
    quantity: int,
    reference_type: str,
    reference_id: uuid.UUID,
) -> None:
    """Reserve stock for one variant, transactionally.

    ponytail: picks the first warehouse with enough standalone stock rather
    than splitting a reservation across warehouses. Add multi-warehouse
    splitting if a single-warehouse order regularly can't be fulfilled.
    """
    result = await db.execute(
        select(Inventory)
        .where(
            Inventory.organization_id == organization_id,
            Inventory.product_variant_id == product_variant_id,
            Inventory.available_quantity >= quantity,
        )
        .order_by(Inventory.available_quantity.desc())
        .limit(1)
        .with_for_update()
    )
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Insufficient stock for variant {product_variant_id}")

    inv.available_quantity -= quantity
    inv.reserved_quantity += quantity
    db.add(inv)
    db.add(
        InventoryMovement(
            organization_id=organization_id, inventory_id=inv.id, type="RESERVE", quantity=quantity,
            reference=str(reference_id),
        )
    )
    db.add(
        StockReservation(
            organization_id=organization_id,
            inventory_id=inv.id,
            quantity=quantity,
            reference_type=reference_type,
            reference_id=reference_id,
            status="ACTIVE",
        )
    )


async def release_reservations(db: AsyncSession, organization_id: uuid.UUID, reference_type: str, reference_id: uuid.UUID) -> None:
    result = await db.execute(
        select(StockReservation).where(
            StockReservation.organization_id == organization_id,
            StockReservation.reference_type == reference_type,
            StockReservation.reference_id == reference_id,
            StockReservation.status == "ACTIVE",
        )
    )
    for res in result.scalars().all():
        inv = await db.get(Inventory, res.inventory_id)
        inv.reserved_quantity -= res.quantity
        inv.available_quantity += res.quantity
        res.status = "RELEASED"
        db.add_all([inv, res])
        db.add(
            InventoryMovement(
                organization_id=organization_id, inventory_id=inv.id, type="RELEASE", quantity=res.quantity,
                reference=str(reference_id),
            )
        )
