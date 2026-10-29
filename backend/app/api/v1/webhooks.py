from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.queue import get_pool
from app.integrations.whatsapp import WhatsAppProvider
from app.services import conversation_service, integration_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/whatsapp")
async def verify_whatsapp_webhook(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == settings.whatsapp_verify_token:
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Verification failed")


@router.post("/whatsapp")
async def receive_whatsapp_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    raw_body = await request.body()
    payload = await request.json()

    phone_number_id = WhatsAppProvider.extract_phone_number_id(payload)
    if not phone_number_id:
        # status callbacks / non-message events we don't care about yet
        return {"status": "ignored"}

    connection = await integration_service.get_whatsapp_connection_by_phone_number_id(db, phone_number_id)
    if not connection:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown WhatsApp number")

    credentials = integration_service.get_credentials(connection)
    signature = request.headers.get("X-Hub-Signature-256")
    if not WhatsAppProvider.verify_signature(raw_body, signature, credentials["app_secret"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")

    conversations_to_process = []
    for inbound in WhatsAppProvider.parse_inbound(payload):
        customer = await conversation_service.find_or_create_customer_by_whatsapp(
            db, connection.organization_id, inbound["from"], inbound["profile_name"]
        )
        conversation = await conversation_service.get_or_create_conversation(
            db, connection.organization_id, customer.id, "WHATSAPP"
        )
        message = await conversation_service.record_inbound_message(
            db,
            conversation,
            sender=inbound["from"],
            recipient=phone_number_id,
            content=inbound["text"],
            external_message_id=inbound["external_id"],
        )
        if message and conversation.ai_enabled:
            conversations_to_process.append(conversation.id)

    await db.commit()

    # AI processing happens off the request path — a model turn can take
    # several seconds, and webhooks must return fast.
    if conversations_to_process:
        pool = await get_pool()
        for conversation_id in conversations_to_process:
            await pool.enqueue_job("process_inbound_message", str(conversation_id), str(connection.organization_id))

    return {"status": "ok"}
