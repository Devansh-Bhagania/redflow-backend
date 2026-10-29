import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.agent import AGENT_STATUSES


class AgentConfigCreate(BaseModel):
    name: str
    description: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    system_prompt: str | None = None
    channels: list[str] = []
    settings: dict = {}


class AgentConfigUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    system_prompt: str | None = None
    status: str | None = None
    channels: list[str] | None = None
    settings: dict | None = None


class AgentConfigOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    default_provider: str | None
    default_model: str | None
    system_prompt: str | None
    status: str
    channels: list[str]
    settings: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


VALID_STATUSES = AGENT_STATUSES
