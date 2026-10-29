import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrgScopedMixin, UUIDPKMixin


class PriceList(Base, UUIDPKMixin, OrgScopedMixin):
    __tablename__ = "price_lists"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PriceListItem(Base, UUIDPKMixin):
    """A price tier: `unit_price` applies to orders of `min_quantity` or more
    of `product_variant_id` on this price list. Multiple rows per variant with
    increasing min_quantity implement volume discounts without a separate table."""

    __tablename__ = "price_list_items"

    price_list_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("price_lists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_variant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    min_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
