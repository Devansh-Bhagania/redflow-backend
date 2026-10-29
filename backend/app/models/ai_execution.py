import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, JSON, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrgScopedMixin, TimestampMixin, UUIDPKMixin


class AiExecution(Base, UUIDPKMixin, OrgScopedMixin, TimestampMixin):
    __tablename__ = "ai_executions"

    agent_type: Mapped[str] = mapped_column(String(30), nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Populated only when this run came from a workflow AI_AGENT node — NULL
    # for every legacy/sandbox call site, which is most of them. Together
    # these answer "which workflow, which node, which run" without joining
    # through the conversation (a node can run before any message exists).
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("workflows.id"), nullable=True)
    workflow_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("workflow_versions.id"), nullable=True)
    workflow_execution_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("workflow_executions.id"), nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="RUNNING")
    finish_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tools_available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=0)


class AiToolCall(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "ai_tool_calls"

    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ai_executions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SUCCESS")
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="SAFE")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
