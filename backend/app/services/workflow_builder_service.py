"""Chat session for designing one workflow.

Runs the WORKFLOW_BUILDER agent through the shared runtime (app.agents.runtime)
rather than a bespoke loop, so it inherits tool authorization, the AiExecution
audit trail, provider selection and retry behaviour for free. The runtime is
built around (organization, customer, conversation); a workflow has no
customer, so this reuses the same synthetic sandbox customer the agent sandbox
already creates and keys the session on a per-workflow WEB conversation.

The agent can only stage a proposal. Applying it is a separate, explicit call
that goes through workflow_service.update_workflow like any human edit.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tool_executor import PROPOSAL_KEY, WORKFLOW_ID_KEY
from app.models.conversation import Conversation, Message
from app.models.organization import Organization
from app.schemas.workflow import WorkflowUpdate
from app.services import agent_sandbox_service, conversation_service, workflow_service

AGENT_TYPE = "WORKFLOW_BUILDER"
# Its own channel so a builder session never shows up in the Inbox alongside
# real customer threads, and so listing conversations doesn't have to special-case it.
BUILDER_CHANNEL = "BUILDER"


async def _session(db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID) -> Conversation:
    """The conversation backing this workflow's builder chat, created on first use."""
    # 404s early if the workflow isn't this org's — the conversation must never
    # be pinned to a workflow the caller can't see.
    await workflow_service.get_workflow(db, organization_id, workflow_id)
    customer = await agent_sandbox_service.get_or_create_sandbox_customer(db, organization_id)

    result = await db.execute(
        select(Conversation).where(
            Conversation.organization_id == organization_id,
            Conversation.channel == BUILDER_CHANNEL,
        )
    )
    # Matched in Python rather than with a JSON-path predicate: agent_state is
    # a plain JSON column, and the operators for querying into it differ
    # between SQLite (tests) and Postgres (production). There is one builder
    # conversation per workflow, so this list is small.
    conversation = next(
        (c for c in result.scalars().all() if (c.agent_state or {}).get(WORKFLOW_ID_KEY) == str(workflow_id)),
        None,
    )
    if conversation is None:
        conversation = Conversation(
            organization_id=organization_id,
            customer_id=customer.id,
            channel=BUILDER_CHANNEL,
            status="OPEN",
            ai_enabled=True,
            agent_type=AGENT_TYPE,
            agent_state={WORKFLOW_ID_KEY: str(workflow_id)},
        )
        db.add(conversation)
        await db.flush()

    await db.commit()
    return await conversation_service.get_conversation(db, organization_id, conversation.id)


def _serialize(conversation: Conversation) -> dict:
    state = conversation.agent_state or {}
    proposal = state.get(PROPOSAL_KEY)
    return {
        "messages": [
            {
                "id": str(m.id),
                "direction": m.direction,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in conversation.messages
        ],
        "proposal": proposal.get("definition") if proposal else None,
        "proposal_summary": proposal.get("summary") if proposal else None,
    }


async def get_session(db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID) -> dict:
    return _serialize(await _session(db, organization_id, workflow_id))


async def send_message(
    db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID, content: str
) -> dict:
    from app.agents.runtime import run_agent  # deferred: mirrors the circular-import workaround elsewhere

    conversation = await _session(db, organization_id, workflow_id)
    customer = await agent_sandbox_service.get_or_create_sandbox_customer(db, organization_id)

    await conversation_service.record_inbound_message(
        db, conversation, sender="builder-user", recipient="agent", content=content,
        external_message_id=f"builder-{uuid.uuid4()}",
    )
    await db.commit()
    # Refresh the whole object, not just `messages`: the runtime reads history
    # off the identity-mapped instance, and refresh(attribute_names=) expires
    # every other column without reloading it — a later bare access outside an
    # awaited SQLAlchemy call then raises MissingGreenlet. Same reasoning as
    # agent_sandbox_service.
    await db.refresh(conversation)

    organization = await db.get(Organization, organization_id)
    reply = await run_agent(db, organization, customer, conversation, AGENT_TYPE)

    await conversation_service.record_outbound_message(
        db, conversation, sender="agent", recipient="builder-user", content=reply, ai_generated=True,
    )
    await db.commit()
    await db.refresh(conversation)
    return _serialize(conversation)


async def apply_proposal(db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID) -> dict:
    """Commits the staged proposal as a new version, then clears it."""
    conversation = await _session(db, organization_id, workflow_id)
    state = dict(conversation.agent_state or {})
    proposal = state.get(PROPOSAL_KEY)
    if not proposal:
        raise HTTPException(status.HTTP_409_CONFLICT, "There is no proposal to apply")

    # Re-validated by update_workflow. Staging happened on an earlier turn and
    # the capability registry could have changed since — trusting the staged
    # copy because it passed once is how an invalid graph reaches the database.
    workflow = await workflow_service.update_workflow(
        db, organization_id, workflow_id, WorkflowUpdate(definition=proposal["definition"])
    )

    state.pop(PROPOSAL_KEY, None)
    conversation.agent_state = state
    db.add(conversation)
    await db.commit()
    return {"id": str(workflow.id), "definition": workflow.definition}


async def discard_proposal(db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID) -> dict:
    conversation = await _session(db, organization_id, workflow_id)
    state = dict(conversation.agent_state or {})
    state.pop(PROPOSAL_KEY, None)
    conversation.agent_state = state
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return _serialize(conversation)


async def reset_session(db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID) -> dict:
    conversation = await _session(db, organization_id, workflow_id)
    # Keep the workflow pin, drop everything else — resetting the chat must not
    # orphan the session from its workflow.
    conversation.agent_state = {WORKFLOW_ID_KEY: str(workflow_id)}
    # ORM-enabled DELETE (db.execute(delete(...)) on a mapped class) syncs the
    # session: it marks every already-loaded Message in conversation.messages
    # as deleted in the identity map. Doing that *before* db.add(conversation)
    # below is what matters — add()ing (or otherwise re-touching) the parent
    # afterward makes the flush revisit those now-deleted children through the
    # relationship's cascade and raise "Instance has been deleted" on them.
    # Committing conversation's own attribute change first sidesteps that;
    # this is the same order agent_sandbox_service.reset_sandbox uses.
    await db.commit()
    await db.execute(delete(Message).where(Message.conversation_id == conversation.id))
    await db.commit()
    await db.refresh(conversation)
    return _serialize(conversation)
