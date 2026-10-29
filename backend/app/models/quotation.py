import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, OrgScopedMixin, TimestampMixin, UUIDPKMixin

QUOTATION_STATUSES = ("DRAFT", "PENDING_APPROVAL", "SENT", "VIEWED", "ACCEPTED", "REJECTED", "EXPIRED", "CONVERTED")


class Quotation(Base, UUIDPKMixin, OrgScopedMixin, TimestampMixin):
    __tablename__ = "quotations"

    quote_number: Mapped[str] = mapped_column(String(30), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    tax: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    shipping: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terms: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_by: Mapped[str] = mapped_column(String(50), nullable=False, default="SYSTEM")  # user id or "AI_SALES_AGENT"

    items: Mapped[list["QuotationItem"]] = relationship(back_populates="quotation", lazy="selectin")


class QuotationItem(Base, UUIDPKMixin):
    __tablename__ = "quotation_items"

    quotation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_variant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("product_variants.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    discount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    quotation: Mapped["Quotation"] = relationship(back_populates="items")
