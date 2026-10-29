import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.integrations.email import EmailProvider
from app.integrations.whatsapp import WhatsAppProvider
from app.models.customer import Customer
from app.schemas.conversation import (
    ConversationOut,
    ConversationSandboxSendOut,
    ConversationSandboxSendRequest,
    SendMessageRequest,
    UpdateConversationRequest,
)
from app.services import conversation_sandbox_service, conversation_service, integration_service

router = APIRouter(prefix="/conversations", tags=["conversations"])

_PROVIDERS = {"WHATSAPP": WhatsAppProvider, "EMAIL": EmailProvider}


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    ctx: TenantContext = Depends(require_permission("conversations.read")), db: AsyncSession = Depends(get_db)
):
    return await conversation_service.list_conversations(db, ctx.organization_id)


# Registered before /{conversation_id} — otherwise "sandbox" would be parsed
# as a conversation_id (and 422, since it isn't a UUID). Same pattern as
# app/api/v1/workflows.py's /executions and /defaults.
#
# Talk to whichever agent/workflow owns the WEB channel as a real customer
# would — goes through the same routing a WhatsApp webhook uses (see
# app.services.conversation_router), unlike /agents/sandbox/messages which
# calls a chosen agent_type directly and never starts a real workflow
# execution. Requires the Temporal + arq workers to be running for a
# workflow-owned conversation to actually reply.
@router.get("/sandbox", response_model=ConversationOut)
async def get_conversation_sandbox(
    agent_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission("conversations.read")),
    db: AsyncSession = Depends(get_db),
):
    return await conversation_sandbox_service.get_test_conversation(db, ctx.organization_id, agent_id)


@router.post("/sandbox/messages", response_model=ConversationSandboxSendOut)
async def send_conversation_sandbox_message(
    payload: ConversationSandboxSendRequest,
    ctx: TenantContext = Depends(require_permission("conversations.manage")),
    db: AsyncSession = Depends(get_db),
):
    result = await conversation_sandbox_service.send_test_message(
        db, ctx.organization_id, payload.content, payload.agent_id
    )
    return ConversationSandboxSendOut(conversation=result["conversation"], route=result["route"])


@router.post("/sandbox/reset", response_model=ConversationOut)
async def reset_conversation_sandbox(
    agent_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission("conversations.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await conversation_sandbox_service.reset_test_conversation(db, ctx.organization_id, agent_id)


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("conversations.read")),
    db: AsyncSession = Depends(get_db),
):
    return await conversation_service.get_conversation(db, ctx.organization_id, conversation_id)


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: uuid.UUID,
    payload: UpdateConversationRequest,
    ctx: TenantContext = Depends(require_permission("conversations.manage")),
    db: AsyncSession = Depends(get_db),
):
    conversation = await conversation_service.get_conversation(db, ctx.organization_id, conversation_id)
    return await conversation_service.update_conversation(
        db, conversation, status_=payload.status, ai_enabled=payload.ai_enabled,
        assigned_user_id=payload.assigned_user_id, agent_type=payload.agent_type,
    )


@router.post("/{conversation_id}/messages", response_model=ConversationOut, status_code=201)
async def send_message(
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    ctx: TenantContext = Depends(require_permission("conversations.manage")),
    db: AsyncSession = Depends(get_db),
):
    conversation = await conversation_service.get_conversation(db, ctx.organization_id, conversation_id)
    provider_cls = _PROVIDERS.get(conversation.channel)
    if not provider_cls:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Sending on channel {conversation.channel} isn't supported yet")

    customer = await db.get(Customer, conversation.customer_id)
    recipient = customer.whatsapp_phone if conversation.channel == "WHATSAPP" else customer.email
    if not recipient:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Customer has no {conversation.channel.lower()} address on file")

    connection = await integration_service.get_connection_for_org(db, ctx.organization_id, conversation.channel.lower())
    if not connection:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"No {conversation.channel.lower()} integration connected for this organization")

    credentials = integration_service.get_credentials(connection)
    external_id = await provider_cls().send(to=recipient, text=payload.content, credentials=credentials)

    await conversation_service.record_outbound_message(
        db,
        conversation,
        sender=connection.external_id,
        recipient=recipient,
        content=payload.content,
        external_message_id=external_id,
    )
    await db.commit()
    await db.refresh(conversation)
    return conversation
