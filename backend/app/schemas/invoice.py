import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class InvoiceOut(BaseModel):
    id: uuid.UUID
    invoice_number: str
    order_id: uuid.UUID
    customer_id: uuid.UUID
    amount_due: Decimal
    amount_paid: Decimal
    status: str
    due_date: datetime
    created_at: datetime

    model_config = {"from_attributes": True}
