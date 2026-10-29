import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message
from app.models.customer import Customer


async def find_or_create_customer_by_whatsapp(
    db: AsyncSession, organization_id: uuid.UUID, phone: str, profile_name: str | None
) -> Customer:
    result = await db.execute(
        select(Customer).where(Customer.organization_id == organization_id, Customer.whatsapp_phone == phone)
    )
    customer = result.scalar_one_or_none()
    if customer:
        return customer

    customer = Customer(
        organization_id=organization_id,
        company_name=profile_name or phone,
        contact_name=profile_name or phone,
        whatsapp_phone=phone,
        customer_code=f"WA-{phone}",
        customer_type="OTHER",
        status="LEAD",
    )
    db.add(customer)
    await db.flush()
    return customer


async def get_or_create_conversation(
    db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, channel: str
) -> Conversation:
    # .first() with a stable order, not .scalar_one_or_none(): two inbound
    # webhooks for the same customer can race and each create an OPEN
    # conversation. scalar_one_or_none() then raises MultipleResultsFound on
    # every later call — one race permanently wedging the customer's thread.
    # Oldest-first keeps everyone converging on the same surviving row.
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.organization_id == organization_id,
            Conversation.customer_id == customer_id,
            Conversation.channel == channel,
            Conversation.status == "OPEN",
        )
        .order_by(Conversation.created_at.asc())
    )
    conversation = result.scalars().first()
    if conversation:
        return conversation

    conversation = Conversation(organization_id=organization_id, customer_id=customer_id, channel=channel)
    db.add(conversation)
    await db.flush()
    return conversation


async def record_inbound_message(
    db: AsyncSession,
    conversation: Conversation,
    *,
    sender: str,
    recipient: str,
    content: str,
    external_message_id: str,
) -> Message | None:
    """Returns None if this external_message_id was already recorded — the
    caller (a webhook handler) may retry-deliver the same event."""
    try:
        async with db.begin_nested():
            message = Message(
                conversation_id=conversation.id,
                direction="INBOUND",
                sender=sender,
                recipient=recipient,
                content=content,
                external_message_id=external_message_id,
            )
            db.add(message)
            await db.flush()
    except IntegrityError:
        return None

    conversation.last_message_at = datetime.now(timezone.utc)
    db.add(conversation)
    return message


async def record_outbound_message(
    db: AsyncSession, conversation: Conversation, *, sender: str, recipient: str, content: str,
    external_message_id: str | None = None, ai_generated: bool = False,
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        direction="OUTBOUND",
        sender=sender,
        recipient=recipient,
        content=content,
        external_message_id=external_message_id,
        ai_generated=ai_generated,
    )
    db.add(message)
    conversation.last_message_at = datetime.now(timezone.utc)
    db.add(conversation)
    return message


async def list_conversations(db: AsyncSession, organization_id: uuid.UUID) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.organization_id == organization_id)
        .order_by(Conversation.last_message_at.desc().nulls_last())
    )
    return list(result.scalars().all())


async def get_conversation(db: AsyncSession, organization_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    result = await db.execute(
        select(Conversation).where(
            Conversation.organization_id == organization_id, Conversation.id == conversation_id
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


async def update_conversation(
    db: AsyncSession, conversation: Conversation, *, status_: str | None, ai_enabled: bool | None,
    assigned_user_id: uuid.UUID | None, agent_type: str | None = None,
) -> Conversation:
    if status_ is not None:
        conversation.status = status_
    if ai_enabled is not None:
        conversation.ai_enabled = ai_enabled
    if assigned_user_id is not None:
        conversation.assigned_user_id = assigned_user_id
    if agent_type is not None:
        conversation.agent_type = agent_type
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation
