import uuid
from decimal import Decimal

from pydantic import BaseModel


class OrderFromQuotationRequest(BaseModel):
    quotation_id: uuid.UUID
    idempotency_key: str


class OrderItemOut(BaseModel):
    id: uuid.UUID
    product_variant_id: uuid.UUID
    quantity: int
    unit_price: Decimal
    line_total: Decimal

    model_config = {"from_attributes": True}


class OrderOut(BaseModel):
    id: uuid.UUID
    order_number: str
    customer_id: uuid.UUID
    quotation_id: uuid.UUID | None
    status: str
    subtotal: Decimal
    tax: Decimal
    shipping: Decimal
    total: Decimal
    items: list[OrderItemOut]

    model_config = {"from_attributes": True}
