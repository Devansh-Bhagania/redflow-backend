import logging
import uuid

from app.agents.runtime import run_agent
from app.core.db import SessionLocal
from app.integrations.whatsapp import WhatsAppProvider
from app.models.customer import Customer
from app.models.organization import Organization
from app.services import conversation_router, conversation_service, integration_service
from app.services.conversation_router import Route

logger = logging.getLogger(__name__)


async def process_inbound_message(ctx, conversation_id: str, organization_id: str) -> None:
    """Routes a freshly-received message to whichever agent owns this
    conversation and sends its reply.

    Kept off the webhook request path (see app/api/v1/webhooks.py) — an AI
    turn can take several seconds, and webhooks must return fast.
    """
    async with SessionLocal() as db:
        org_uuid = uuid.UUID(organization_id)
        conversation = await conversation_service.get_conversation(db, org_uuid, uuid.UUID(conversation_id))
        if not conversation.ai_enabled:
            return

        latest_message = conversation.messages[-1].content if conversation.messages else ""
        route = await conversation_router.route_inbound_message(db, org_uuid, conversation, latest_message)
        await db.commit()

        if route in (Route.WORKFLOW_RESUMED, Route.WORKFLOW_STARTED):
            # The workflow (SEND_MESSAGE nodes, specifically) owns replying
            # from here — nothing left for the legacy send path below to do.
            return
        if conversation.channel != "WHATSAPP":
            # The legacy agent path below only knows how to send over
            # WhatsApp (see WhatsAppProvider() call). A workflow is what
            # makes any other channel deliverable — see app/models/agent.py's
            # `channels` and app.workflows.activities.send_message_node,
            # which resolve provider from the conversation's channel
            # generically. Without a matching Agent+workflow, a non-WhatsApp
            # channel on the legacy route has nowhere to send a reply.
            return

        organization = await db.get(Organization, org_uuid)
        customer = await db.get(Customer, conversation.customer_id)

        reply_text = await run_agent(db, organization, customer, conversation, agent_type=conversation.agent_type)

        connection = await integration_service.get_whatsapp_connection_for_org(db, org_uuid)
        if not connection:
            logger.warning(
                "No WhatsApp connection for org %s — agent reply generated but not delivered "
                "(conversation=%s)", org_uuid, conversation_id,
            )
            return

        # Persisted before the send attempt, not after: the reply cost real
        # tokens and belongs in the transcript whether or not delivery works.
        # Recording it only on success meant a WhatsApp outage silently threw
        # the reply away and the retry re-ran (and re-billed) the whole agent.
        message = await conversation_service.record_outbound_message(
            db,
            conversation,
            sender=connection.external_id,
            recipient=customer.whatsapp_phone,
            content=reply_text,
            ai_generated=True,
        )
        await db.commit()

        credentials = integration_service.get_credentials(connection)
        external_id = await WhatsAppProvider().send(
            to=customer.whatsapp_phone, text=reply_text, credentials=credentials
        )

        message.external_message_id = external_id
        db.add(message)
        await db.commit()
