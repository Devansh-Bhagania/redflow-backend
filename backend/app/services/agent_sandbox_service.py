"""A per-organization test bed for trying any agent without touching a real
conversation. Tool calls are real (same services the REST API and live
conversations use) — you can point it at the dedicated "Sandbox Customer" for
a fully synthetic run, or at any real customer to see the agent's behavior
with that customer's actual pricing tier and purchase history. Either way it
writes to a separate WEB-channel conversation, not the customer's real
WhatsApp/Email thread."""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.organization import Organization
from app.services import conversation_service, customer_service

SANDBOX_CUSTOMER_CODE = "SANDBOX"
SANDBOX_CHANNEL = "WEB"


async def get_or_create_sandbox_customer(db: AsyncSession, organization_id: uuid.UUID) -> Customer:
    result = await db.execute(
        select(Customer).where(Customer.organization_id == organization_id, Customer.customer_code == SANDBOX_CUSTOMER_CODE)
    )
    customer = result.scalar_one_or_none()
    if customer:
        return customer

    customer = Customer(
        organization_id=organization_id,
        company_name="Sandbox Customer",
        contact_name="Sandbox",
        customer_code=SANDBOX_CUSTOMER_CODE,
        customer_type="OTHER",
    )
    db.add(customer)
    await db.flush()
    return customer


async def _resolve_customer(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID | None) -> Customer:
    if customer_id is not None:
        return await customer_service.get_customer(db, organization_id, customer_id)
    return await get_or_create_sandbox_customer(db, organization_id)


async def _get_sandbox_conversation(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID | None) -> Conversation:
    customer = await _resolve_customer(db, organization_id, customer_id)
    conversation = await conversation_service.get_or_create_conversation(db, organization_id, customer.id, SANDBOX_CHANNEL)
    await db.commit()
    return await conversation_service.get_conversation(db, organization_id, conversation.id)


async def get_sandbox_conversation(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID | None = None) -> Conversation:
    return await _get_sandbox_conversation(db, organization_id, customer_id)


async def send_sandbox_message(
    db: AsyncSession, organization_id: uuid.UUID, agent_type: str, content: str, customer_id: uuid.UUID | None = None
) -> Conversation:
    from app.agents.runtime import run_agent  # deferred: mirrors workflows/activities.py's circular-import workaround

    customer = await _resolve_customer(db, organization_id, customer_id)
    conversation = await conversation_service.get_or_create_conversation(db, organization_id, customer.id, SANDBOX_CHANNEL)
    await conversation_service.record_inbound_message(
        db, conversation, sender="sandbox-user", recipient="agent", content=content,
        external_message_id=f"sandbox-{uuid.uuid4()}",
    )
    await db.commit()
    # Same identity-mapped object as before the commit — `.messages` was
    # already loaded (selectin) by get_or_create_conversation above, so a
    # plain re-query won't pick up the inbound message just recorded. Without
    # this, run_agent() sees history ending on the *previous* assistant
    # reply, sends the model a turn with no new user message, and gets back
    # an empty/near-empty completion (or a template error, on stricter ones).
    # Refresh the whole object, not just `messages`: refresh(attribute_names=)
    # expires every other attribute (id, agent_state, ...) without reloading
    # them, and a later bare attribute access on an expired column outside an
    # awaited SQLAlchemy call raises MissingGreenlet under asyncio.
    await db.refresh(conversation)

    organization = await db.get(Organization, organization_id)
    reply = await run_agent(db, organization, customer, conversation, agent_type)

    await conversation_service.record_outbound_message(
        db, conversation, sender="agent", recipient="sandbox-user", content=reply, ai_generated=True,
    )
    await db.commit()
    # Same staleness issue as above, now for the outbound message.
    await db.refresh(conversation)
    return conversation


async def reset_sandbox(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID | None = None) -> Conversation:
    conversation = await _get_sandbox_conversation(db, organization_id, customer_id)
    await db.execute(delete(Message).where(Message.conversation_id == conversation.id))
    # Clear the persisted search state too — otherwise the next turn's system
    # prompt still shows the old session's category/filters/selected product
    # (see search_state.render_search_state) even though the visible chat is empty.
    conversation.agent_state = {}
    await db.commit()
    # Refresh the whole object, not just `messages`: refresh(attribute_names=)
    # expires every other attribute (id, agent_state, ...) without reloading
    # them, and a later bare attribute access on an expired column outside an
    # awaited SQLAlchemy call raises MissingGreenlet under asyncio.
    await db.refresh(conversation)
    return conversation
