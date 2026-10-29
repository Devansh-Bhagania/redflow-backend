"""A place to talk to a workflow-driven agent as a real customer would,
without a connected WhatsApp/email provider.

Distinct from app.services.agent_sandbox_service: that one calls run_agent
directly against a chosen agent_type, bypassing routing entirely — useful for
"does this preset answer well" but it never touches conversation_router,
never starts a real Temporal execution, and never exercises WAIT_FOR_REPLY.
This one sends the message through the exact same path a real inbound
webhook would (see app.workers.tasks.process_inbound_message).

Two modes, both on the real "WEB" channel:
  agent_id given    -> talk to THIS agent specifically (route_to_agent),
                       regardless of whether WEB is even in its channels list.
                       Each agent gets its own isolated test conversation, so
                       switching agents never mixes histories.
  agent_id omitted   -> whichever ACTIVE agent actually owns WEB in real
                       traffic (route_inbound_message) — "what would a real
                       web-chat customer experience right now".
"""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.services import conversation_router, conversation_service

TEST_CHANNEL = "WEB"
_SHARED_CUSTOMER_CODE = "WEB-TEST"


def _customer_code(agent_id: uuid.UUID | None) -> str:
    return f"{_SHARED_CUSTOMER_CODE}-{agent_id.hex[:12]}" if agent_id else _SHARED_CUSTOMER_CODE


async def get_or_create_test_customer(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID | None = None
) -> Customer:
    code = _customer_code(agent_id)
    result = await db.execute(
        select(Customer).where(Customer.organization_id == organization_id, Customer.customer_code == code)
    )
    customer = result.scalar_one_or_none()
    if customer:
        return customer
    customer = Customer(
        organization_id=organization_id,
        company_name="Web Test Customer",
        contact_name="Test Customer",
        customer_code=code,
        customer_type="OTHER",
    )
    db.add(customer)
    await db.flush()
    return customer


async def _test_conversation(db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID | None) -> Conversation:
    customer = await get_or_create_test_customer(db, organization_id, agent_id)
    conversation = await conversation_service.get_or_create_conversation(db, organization_id, customer.id, TEST_CHANNEL)
    await db.commit()
    return await conversation_service.get_conversation(db, organization_id, conversation.id)


async def get_test_conversation(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID | None = None
) -> Conversation:
    return await _test_conversation(db, organization_id, agent_id)


async def send_test_message(
    db: AsyncSession, organization_id: uuid.UUID, content: str, agent_id: uuid.UUID | None = None
) -> dict:
    """Records the message and routes it exactly like a real webhook would.
    Returns immediately — if a workflow picks it up, its reply is produced by
    the Temporal worker asynchronously, same as a real send. Callers should
    poll get_test_conversation for new OUTBOUND messages."""
    conversation = await _test_conversation(db, organization_id, agent_id)
    await conversation_service.record_inbound_message(
        db, conversation, sender="web-test-user", recipient="agent", content=content,
        external_message_id=f"web-test-{uuid.uuid4()}",
    )
    await db.commit()
    await db.refresh(conversation)

    if agent_id is not None:
        route = await conversation_router.route_to_agent(db, organization_id, conversation, content, agent_id)
    else:
        route = await conversation_router.route_inbound_message(db, organization_id, conversation, content)
    await db.commit()
    await db.refresh(conversation)
    return {"conversation": conversation, "route": route.value}


async def reset_test_conversation(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID | None = None
) -> Conversation:
    """Clears history and detaches from any workflow execution so the next
    message starts fresh. Column update committed before the bulk DELETE, not
    after — see workflow_builder_service.reset_session for why that ordering
    matters (an ORM-enabled DELETE desyncs the identity map if the parent is
    touched again afterward in the same session)."""
    conversation = await _test_conversation(db, organization_id, agent_id)
    conversation.workflow_execution_id = None
    conversation.agent_state = {}
    await db.commit()
    await db.execute(delete(Message).where(Message.conversation_id == conversation.id))
    await db.commit()
    await db.refresh(conversation)
    return conversation
