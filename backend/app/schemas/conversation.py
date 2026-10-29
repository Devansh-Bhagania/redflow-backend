import uuid
from datetime import datetime

from pydantic import BaseModel


class MessageOut(BaseModel):
    id: uuid.UUID
    direction: str
    sender: str
    recipient: str
    content: str
    message_type: str
    ai_generated: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    channel: str
    status: str
    assigned_user_id: uuid.UUID | None
    ai_enabled: bool
    agent_type: str
    last_message_at: datetime | None
    agent_state: dict
    workflow_execution_id: uuid.UUID | None = None
    messages: list[MessageOut]

    model_config = {"from_attributes": True}


class SendMessageRequest(BaseModel):
    content: str


class ConversationSandboxSendRequest(BaseModel):
    content: str
    # None = whichever agent actually owns the WEB channel in real traffic.
    # Set = talk to this specific agent regardless of its channel config —
    # the "select my agent and test it" path.
    agent_id: uuid.UUID | None = None


class ConversationSandboxSendOut(BaseModel):
    conversation: ConversationOut
    # Which path handled this turn — WORKFLOW_STARTED/WORKFLOW_RESUMED means a
    # real Temporal execution is driving the reply, asynchronously; poll GET
    # for it. LEGACY means the reply (if any) already landed synchronously.
    route: str


class UpdateConversationRequest(BaseModel):
    status: str | None = None
    ai_enabled: bool | None = None
    assigned_user_id: uuid.UUID | None = None
    agent_type: str | None = None
