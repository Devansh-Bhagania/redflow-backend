import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrgScopedMixin, TimestampMixin, UUIDPKMixin


class Workflow(Base, UUIDPKMixin, OrgScopedMixin, TimestampMixin):
    __tablename__ = "workflows"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_event: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")  # DRAFT | PUBLISHED
    # Nullable: a workflow doesn't have to belong to a conversational Agent —
    # the pre-existing quote.sent follow-up flow is a bare Workflow with no
    # agent_id, and that keeps working unchanged. Set when the workflow is one
    # of an Agent's conversation flows (see app/models/agent.py); conversation
    # routing looks up "this agent's PUBLISHED workflow" through this column.
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True)


class WorkflowVersion(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "workflow_versions"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)


class WorkflowExecution(Base, UUIDPKMixin, OrgScopedMixin, TimestampMixin):
    __tablename__ = "workflow_executions"

    workflow_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("workflows.id"), nullable=True)
    workflow_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("workflow_versions.id"), nullable=True)
    temporal_workflow_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="RUNNING")  # RUNNING | COMPLETED | FAILED
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowExecutionStep(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "workflow_execution_steps"

    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflow_executions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    node_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="COMPLETED")
