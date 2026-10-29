import uuid
from decimal import Decimal

from pydantic import BaseModel


class VariantCreate(BaseModel):
    sku: str
    attributes: dict = {}
    price_override: Decimal | None = None


class ProductCreate(BaseModel):
    name: str
    sku: str
    description: str | None = None
    category_id: uuid.UUID | None = None
    brand_id: uuid.UUID | None = None
    unit: str = "unit"
    cost_price: Decimal = Decimal("0")
    base_price: Decimal
    tax_rate: Decimal = Decimal("0")
    variants: list[VariantCreate] = []


class VariantOut(BaseModel):
    id: uuid.UUID
    sku: str
    attributes: dict
    price_override: Decimal | None
    status: str

    model_config = {"from_attributes": True}


class ProductOut(BaseModel):
    id: uuid.UUID
    name: str
    sku: str
    description: str | None
    category_id: uuid.UUID | None
    brand_id: uuid.UUID | None
    unit: str
    cost_price: Decimal
    base_price: Decimal
    tax_rate: Decimal
    status: str
    variants: list[VariantOut]

    model_config = {"from_attributes": True}
