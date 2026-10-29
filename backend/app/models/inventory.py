import uuid

from sqlalchemy import ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrgScopedMixin, TimestampMixin, UUIDPKMixin


class Warehouse(Base, UUIDPKMixin, OrgScopedMixin):
    __tablename__ = "warehouses"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)


class Inventory(Base, UUIDPKMixin, OrgScopedMixin, TimestampMixin):
    __tablename__ = "inventory"

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_variant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    available_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incoming_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    damaged_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reorder_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class InventoryMovement(Base, UUIDPKMixin, OrgScopedMixin, TimestampMixin):
    __tablename__ = "inventory_movements"

    inventory_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("inventory.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # RECEIPT | RESERVE | RELEASE | FULFILL | ADJUSTMENT | DAMAGE
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)


class StockReservation(Base, UUIDPKMixin, OrgScopedMixin, TimestampMixin):
    __tablename__ = "stock_reservations"

    inventory_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("inventory.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_type: Mapped[str] = mapped_column(String(20), nullable=False)  # ORDER | QUOTE
    reference_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")  # ACTIVE | RELEASED | FULFILLED
