import uuid
from decimal import Decimal

from pydantic import BaseModel, EmailStr


class CustomerCreate(BaseModel):
    company_name: str
    contact_name: str
    email: EmailStr | None = None
    phone: str | None = None
    whatsapp_phone: str | None = None
    customer_code: str
    customer_type: str = "RETAILER"
    price_list_id: uuid.UUID | None = None
    credit_limit: Decimal = Decimal("0")
    payment_terms: str = "DUE_ON_RECEIPT"


class CustomerOut(BaseModel):
    id: uuid.UUID
    company_name: str
    contact_name: str
    email: str | None
    phone: str | None
    whatsapp_phone: str | None
    customer_code: str
    customer_type: str
    price_list_id: uuid.UUID | None
    credit_limit: Decimal
    payment_terms: str
    outstanding_balance: Decimal
    status: str

    model_config = {"from_attributes": True}
