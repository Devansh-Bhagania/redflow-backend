import uuid

from pydantic import BaseModel


class AgentOut(BaseModel):
    agent_type: str
    description: str
    tools: list[str]


class SandboxMessageRequest(BaseModel):
    agent_type: str
    content: str
    customer_id: uuid.UUID | None = None  # None = the dedicated fake Sandbox Customer


class SandboxResetRequest(BaseModel):
    customer_id: uuid.UUID | None = None
