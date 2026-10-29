import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, JSON, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, OrgScopedMixin, TimestampMixin, UUIDPKMixin


class Category(Base, UUIDPKMixin, OrgScopedMixin):
    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("categories.id"), nullable=True)


class Brand(Base, UUIDPKMixin, OrgScopedMixin):
    __tablename__ = "brands"

    name: Mapped[str] = mapped_column(String(255), nullable=False)


class Product(Base, UUIDPKMixin, OrgScopedMixin, TimestampMixin):
    __tablename__ = "products"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("categories.id"), nullable=True)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("brands.id"), nullable=True)
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="unit")
    cost_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    base_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")

    variants: Mapped[list["ProductVariant"]] = relationship(back_populates="product", lazy="selectin")


class ProductVariant(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "product_variants"

    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    # flexible variant attributes: size, color, material, ...
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    price_override: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")

    product: Mapped["Product"] = relationship(back_populates="variants")
