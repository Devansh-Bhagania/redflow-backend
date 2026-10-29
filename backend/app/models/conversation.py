import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, OrgScopedMixin, TimestampMixin, UUIDPKMixin

CHANNELS = ("WHATSAPP", "EMAIL", "WEB", "SMS", "INSTAGRAM", "MESSENGER")
AGENT_TYPES = ("SALES", "SUPPORT", "REORDERING", "FOLLOWUP", "INVENTORY", "COLLECTIONS", "COMPANY")


class Conversation(Base, UUIDPKMixin, OrgScopedMixin, TimestampMixin):
    __tablename__ = "conversations"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")  # OPEN | CLOSED
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    agent_type: Mapped[str] = mapped_column(String(20), nullable=False, default="SALES")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once a workflow-driven agent (see app/models/agent.py) takes
    # ownership of this conversation. NULL means the legacy agent_type path
    # above still handles every inbound message on this conversation — see
    # app.services.conversation_router. Deliberately not workflow_id/
    # workflow_version_id columns: WorkflowExecution already carries both,
    # so this one FK resolves the whole chain without duplicating state that
    # could drift out of sync with it.
    workflow_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workflow_executions.id"), nullable=True
    )
    # Structured shopping-session state (category, filters, search_results,
    # selected_product, quantity) — persisted server-side and fed into every
    # agent invocation, not reconstructed by re-reading the raw transcript.
    # See app/agents/search_state.py.
    agent_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", order_by="Message.created_at", lazy="selectin"
    )


class Message(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # INBOUND | OUTBOUND
    sender: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(String(4000), nullable=False)
    message_type: Mapped[str] = mapped_column(String(20), nullable=False, default="TEXT")
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tool_call: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
