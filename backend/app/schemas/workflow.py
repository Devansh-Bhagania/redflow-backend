import uuid
from datetime import datetime

from pydantic import BaseModel


class WorkflowCreate(BaseModel):
    name: str
    trigger_event: str
    definition: dict


class WorkflowUpdate(BaseModel):
    name: str | None = None
    trigger_event: str | None = None
    definition: dict | None = None


class WorkflowVersionOut(BaseModel):
    id: uuid.UUID
    version: int
    definition: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class DryRunRequest(BaseModel):
    definition: dict
    context: dict = {}
    # node_id -> branch to take. Lets a user walk a specific path instead of
    # only ever seeing the default one.
    decisions: dict[str, str] = {}


class DryRunStep(BaseModel):
    node_id: str
    node_type: str
    detail: str
    branch: str | None = None
    depth: int = 0


class DryRunOut(BaseModel):
    status: str
    steps: list[DryRunStep]
    context: dict
    simulated_seconds: int
    error: str | None = None


class ResumeRequest(BaseModel):
    node_id: str
    payload: dict = {}


class WorkflowChatRequest(BaseModel):
    message: str


class WorkflowChatOut(BaseModel):
    messages: list[dict]
    # The definition the agent is proposing, if this turn produced one. The
    # stored workflow is untouched until the user applies it.
    proposal: dict | None = None
    proposal_error: str | None = None


class WorkflowOut(BaseModel):
    id: uuid.UUID
    name: str
    trigger_event: str
    status: str
    agent_id: uuid.UUID | None = None
    definition: dict = {}

    model_config = {"from_attributes": True}


class DefaultWorkflowOut(BaseModel):
    trigger_event: str
    name: str
    definition: dict


class WorkflowExecutionOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID | None
    temporal_workflow_id: str
    status: str
    context: dict
    started_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}
