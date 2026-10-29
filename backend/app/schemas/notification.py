import uuid
from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: uuid.UUID
    title: str
    body: str
    read_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
