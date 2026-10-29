import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class QuotationItemCreate(BaseModel):
    product_variant_id: uuid.UUID
    quantity: int


class QuotationCreate(BaseModel):
    customer_id: uuid.UUID
    items: list[QuotationItemCreate]
    valid_until: datetime | None = None
    terms: str | None = None


class QuotationItemOut(BaseModel):
    id: uuid.UUID
    product_variant_id: uuid.UUID
    quantity: int
    unit_price: Decimal
    discount: Decimal
    line_total: Decimal

    model_config = {"from_attributes": True}


class QuotationOut(BaseModel):
    id: uuid.UUID
    quote_number: str
    customer_id: uuid.UUID
    status: str
    subtotal: Decimal
    tax: Decimal
    shipping: Decimal
    total: Decimal
    valid_until: datetime | None
    terms: str | None
    items: list[QuotationItemOut]

    model_config = {"from_attributes": True}
