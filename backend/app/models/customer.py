import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrgScopedMixin, TimestampMixin, UUIDPKMixin

CUSTOMER_TYPES = ("RETAILER", "DISTRIBUTOR", "DEALER", "RESELLER", "CORPORATE", "OTHER")


class Customer(Base, UUIDPKMixin, OrgScopedMixin, TimestampMixin):
    __tablename__ = "customers"

    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    whatsapp_phone: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    customer_code: Mapped[str] = mapped_column(String(50), nullable=False)
    customer_type: Mapped[str] = mapped_column(String(20), nullable=False, default="RETAILER")

    price_list_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("price_lists.id"), nullable=True
    )
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    payment_terms: Mapped[str] = mapped_column(String(50), nullable=False, default="DUE_ON_RECEIPT")
    outstanding_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
