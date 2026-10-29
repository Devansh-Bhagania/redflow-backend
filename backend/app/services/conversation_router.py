"""Decides whether an inbound message goes to a running workflow or the
legacy per-conversation agent (see app/models/conversation.py's
`workflow_execution_id` vs `agent_type` columns).

This is Phase 6 of the migration: additive, not a replacement. A conversation
with no workflow ownership behaves exactly as it did before this module
existed — every existing customer keeps working unchanged.
"""

import logging
import uuid
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.services import workflow_service

logger = logging.getLogger(__name__)


class Route(str, Enum):
    WORKFLOW_RESUMED = "WORKFLOW_RESUMED"  # signalled a running execution; nothing else to do
    WORKFLOW_STARTED = "WORKFLOW_STARTED"  # a fresh execution now owns this conversation
    LEGACY = "LEGACY"  # caller should run conversation.agent_type the old way


async def _find_agent_for_channel(db: AsyncSession, organization_id: uuid.UUID, channel: str) -> Agent | None:
    result = await db.execute(
        select(Agent).where(Agent.organization_id == organization_id, Agent.status == "ACTIVE")
    )
    for agent in result.scalars().all():
        if channel in (agent.channels or []):
            return agent
    return None


async def _resume_or_report(
    db: AsyncSession, organization_id: uuid.UUID, conversation: Conversation, content: str
) -> Route | None:
    """Handles an already-owned conversation: resumes it if it's waiting,
    reports why not otherwise. Returns None if there's nothing owning the
    conversation (anymore) and the caller should go find one."""
    if not conversation.workflow_execution_id:
        return None

    execution = await workflow_service.get_execution(db, organization_id, conversation.workflow_execution_id)
    if execution.status == "RUNNING":
        resumed = await workflow_service.signal_reply(db, organization_id, execution.id, content)
        if resumed:
            return Route.WORKFLOW_RESUMED
        # RUNNING but not actually waiting on a reply (mid-step, or parked on
        # a HUMAN_APPROVAL/WEBHOOK instead) — the message arrived at a moment
        # the graph wasn't listening. Dropping it silently would lose what
        # the customer said; falling through to the legacy path at least gets
        # them an answer, at the cost of the workflow and the legacy agent
        # both having seen this one message.
        logger.warning(
            "Conversation %s has a RUNNING execution not waiting for a reply — falling back to the legacy agent",
            conversation.id,
        )
        return Route.LEGACY

    # Workflow finished (or failed) since it took ownership. Section 20's "end
    # conversation workflow ownership" branch: release the FK so the next
    # message re-evaluates from scratch, rather than "start a configured
    # follow-up workflow", which has no configuration surface yet.
    conversation.workflow_execution_id = None
    return None


async def _start_for_agent(
    db: AsyncSession, organization_id: uuid.UUID, conversation: Conversation, content: str, agent_id: uuid.UUID
) -> Route:
    """Starts this specific agent's published workflow against `conversation`.
    Assumes the caller already confirmed the conversation isn't owned by
    something else (_resume_or_report returned None)."""
    found = await workflow_service.get_published_workflow_for_agent(db, organization_id, agent_id)
    if found is None:
        return Route.LEGACY
    workflow, version_id = found

    context = {
        "customer_id": str(conversation.customer_id),
        "conversation_id": str(conversation.id),
        "channel": conversation.channel,
        "trigger_message": content,
    }
    execution = await workflow_service.start_execution(
        db, organization_id, workflow.definition, context,
        workflow_id=workflow.id, workflow_version_id=version_id,
    )
    conversation.workflow_execution_id = execution.id
    return Route.WORKFLOW_STARTED


async def route_inbound_message(
    db: AsyncSession, organization_id: uuid.UUID, conversation: Conversation, content: str
) -> Route:
    """The real-traffic path: whichever ACTIVE agent owns this conversation's
    channel. Mutates `conversation.workflow_execution_id` when it starts or
    ends workflow ownership; does not commit — the caller
    (process_inbound_message) owns the transaction boundary, same as every
    other write in that path."""
    resumed = await _resume_or_report(db, organization_id, conversation, content)
    if resumed is not None:
        return resumed

    agent = await _find_agent_for_channel(db, organization_id, conversation.channel)
    if agent is None:
        return Route.LEGACY
    return await _start_for_agent(db, organization_id, conversation, content, agent.id)


async def route_to_agent(
    db: AsyncSession, organization_id: uuid.UUID, conversation: Conversation, content: str, agent_id: uuid.UUID
) -> Route:
    """The explicit-choice path: 'run this specific agent', independent of
    which channels it's configured for. For testing an agent before it's
    live on any real channel — see app.services.conversation_sandbox_service."""
    resumed = await _resume_or_report(db, organization_id, conversation, content)
    if resumed is not None:
        return resumed
    return await _start_for_agent(db, organization_id, conversation, content, agent_id)
