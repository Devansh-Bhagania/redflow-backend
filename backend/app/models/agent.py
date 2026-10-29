"""A company-configured AI agent — data, not a Python constant.

This is deliberately a thin record: `default_provider`/`default_model`/
`system_prompt` are fallbacks a node can leave unset, and `channels` is what
conversation routing (app.services.conversation_router) checks to decide
whether a new conversation on a given channel should start this agent's
published workflow. The actual behavior lives in the agent's Workflow(s) —
see app/models/workflow.py — not on this row.
"""

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrgScopedMixin, TimestampMixin, UUIDPKMixin

# DRAFT: being configured, not offered to any channel.
# ACTIVE: eligible to own conversations on its `channels`.
# ARCHIVED: kept for history/audit; never picks up new conversations.
AGENT_STATUSES = ("DRAFT", "ACTIVE", "ARCHIVED")


class Agent(Base, UUIDPKMixin, OrgScopedMixin, TimestampMixin):
    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Falls back to settings.ai_provider / settings.ai_model when unset — see
    # app.agents.node_runtime._resolve_provider_and_model for the full
    # node > agent > global precedence.
    default_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Prepended to every AI_AGENT node's system prompt in this agent's
    # workflows — "global instructions" in the spec (tone, brand voice,
    # things every node should know regardless of its specific task).
    system_prompt: Mapped[str | None] = mapped_column(String(8000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    # Which inbound channels this agent owns, e.g. ["WHATSAPP", "WEB"]. Read by
    # conversation routing to pick an agent for a fresh conversation. Kept as a
    # plain JSON list rather than a join table — an agent typically owns a
    # handful of channels, and this list is never queried by its contents from
    # SQL, only read back in Python.
    channels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Free-form: avatar url, UI preferences, anything that isn't behavior.
    settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
