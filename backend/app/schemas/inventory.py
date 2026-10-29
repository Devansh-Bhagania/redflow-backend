import uuid

from pydantic import BaseModel


class WarehouseCreate(BaseModel):
    name: str
    code: str


class WarehouseOut(BaseModel):
    id: uuid.UUID
    name: str
    code: str

    model_config = {"from_attributes": True}


class StockReceiveRequest(BaseModel):
    warehouse_id: uuid.UUID
    product_variant_id: uuid.UUID
    quantity: int


class InventoryOut(BaseModel):
    id: uuid.UUID
    warehouse_id: uuid.UUID
    product_variant_id: uuid.UUID
    available_quantity: int
    reserved_quantity: int
    incoming_quantity: int
    damaged_quantity: int
    reorder_level: int

    model_config = {"from_attributes": True}
