"""Temporal activities — the only place workflow nodes touch the database or
the outside world. The workflow definition (graph_workflow.py) stays pure
and deterministic; all IO happens here.
"""

import ipaddress
import socket
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from temporalio import activity

from app.core.db import SessionLocal
from app.integrations.email import EmailProvider
from app.integrations.whatsapp import WhatsAppProvider
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.organization import Organization
from app.models.workflow import Workflow, WorkflowExecution, WorkflowExecutionStep, WorkflowVersion
from app.services import conversation_service, integration_service, notification_service, quotation_service
from app.workflows.context import render_template

_PROVIDERS = {"WHATSAPP": WhatsAppProvider, "EMAIL": EmailProvider}

HTTP_TIMEOUT_SECONDS = 20
# Response bodies land in the workflow context, which is persisted as JSON on
# every execution row and replayed by Temporal. An unbounded response would
# bloat both.
MAX_HTTP_RESPONSE_BYTES = 64 * 1024


async def _quote_responded(db, context: dict) -> bool:
    quotation = await quotation_service.get_quotation(db, uuid.UUID(context["organization_id"]), uuid.UUID(context["quotation_id"]))
    if quotation.status != "SENT":
        return True

    # .first(), not .scalar_one_or_none(): a customer can legitimately have
    # more than one WhatsApp conversation row (an earlier CLOSED thread, or a
    # duplicate created by two webhooks racing), and scalar_one_or_none()
    # raises MultipleResultsFound on all of them — which failed this activity
    # and left the follow-up workflow retrying forever.
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.organization_id == quotation.organization_id,
            Conversation.customer_id == quotation.customer_id,
            Conversation.channel == "WHATSAPP",
        )
        .order_by(Conversation.created_at.desc())
    )
    conversations = list(result.scalars().all())
    if not conversations:
        return False

    # "Did they reply at all" spans every thread they have on this channel —
    # answering on an older thread is still a response to the quote.
    reply = await db.execute(
        select(Message.id)
        .where(
            Message.conversation_id.in_([c.id for c in conversations]),
            Message.direction == "INBOUND",
            Message.created_at > quotation.updated_at,
        )
        .limit(1)
    )
    return reply.first() is not None


async def _send_quote_followup(db, context: dict) -> dict:
    organization_id = uuid.UUID(context["organization_id"])
    quotation = await quotation_service.get_quotation(db, organization_id, uuid.UUID(context["quotation_id"]))
    if quotation.status != "SENT":
        return {"sent": False, "reason": f"quote is already {quotation.status}"}

    connection = await integration_service.get_whatsapp_connection_for_org(db, organization_id)
    if not connection:
        return {"sent": False, "reason": "no WhatsApp connection"}

    customer = await db.get(Customer, quotation.customer_id)
    conversation = await conversation_service.get_or_create_conversation(db, organization_id, customer.id, "WHATSAPP")

    text = f"Hi {customer.contact_name}, just following up on quote {quotation.quote_number} (₹{quotation.total}). Would you like to go ahead?"
    credentials = integration_service.get_credentials(connection)
    external_id = await WhatsAppProvider().send(to=customer.whatsapp_phone, text=text, credentials=credentials)

    await conversation_service.record_outbound_message(
        db, conversation, sender=connection.external_id, recipient=customer.whatsapp_phone,
        content=text, external_message_id=external_id, ai_generated=False,
    )
    await db.commit()
    return {"sent": True}


async def _notify_team_quote_stalled(db, context: dict) -> dict:
    organization_id = uuid.UUID(context["organization_id"])
    quotation = await quotation_service.get_quotation(db, organization_id, uuid.UUID(context["quotation_id"]))
    await notification_service.create_notification(
        db, organization_id,
        title="Quote needs follow-up",
        body=f"Quote {quotation.quote_number} (₹{quotation.total}) has had no customer response after two follow-ups.",
    )
    return {"notified": True}


async def _run_ai_agent_step(db, context: dict, agent_type: str) -> dict:
    """Runs a built-in AI agent as a workflow step: it looks at the customer's
    conversation history and tools and produces a reply, which is sent out
    over that conversation's channel — a proactive turn, not a response to a
    fresh inbound message."""
    from app.agents.runtime import run_agent  # deferred: avoids a circular import at module load time

    organization_id = uuid.UUID(context["organization_id"])
    customer_id = uuid.UUID(context["customer_id"])

    organization = await db.get(Organization, organization_id)
    customer = await db.get(Customer, customer_id)
    channel = "WHATSAPP" if customer.whatsapp_phone else "EMAIL"
    conversation = await conversation_service.get_or_create_conversation(db, organization_id, customer_id, channel)
    # re-fetch by id: a freshly-created conversation's `messages` (lazy="selectin")
    # isn't loaded yet, and run_agent reads it — get_conversation's SELECT triggers
    # the eager load, same pattern app/workers/tasks.py relies on.
    conversation = await conversation_service.get_conversation(db, organization_id, conversation.id)

    reply = await run_agent(db, organization, customer, conversation, agent_type)

    recipient = customer.whatsapp_phone if channel == "WHATSAPP" else customer.email
    connection = await integration_service.get_connection_for_org(db, organization_id, channel.lower())
    provider_cls = _PROVIDERS.get(channel)
    if not (recipient and connection and provider_cls):
        return {"agent_type": agent_type, "reply": reply, "sent": False}

    # Persist before sending — same reasoning as workers/tasks.py: this
    # activity is retried by Temporal, and recording only on a successful send
    # meant a provider outage discarded the reply and re-ran the whole agent.
    message = await conversation_service.record_outbound_message(
        db, conversation, sender=connection.external_id, recipient=recipient, content=reply,
        ai_generated=True,
    )
    await db.commit()

    credentials = integration_service.get_credentials(connection)
    external_id = await provider_cls().send(to=recipient, text=reply, credentials=credentials)

    message.external_message_id = external_id
    db.add(message)
    await db.commit()
    return {"agent_type": agent_type, "reply": reply, "sent": True}


ACTIONS = {
    "send_quote_followup": _send_quote_followup,
    "notify_team_quote_stalled": _notify_team_quote_stalled,
}
CONDITIONS = {
    "quote_responded": _quote_responded,
}


@activity.defn
async def execute_action(action: str, context: dict) -> dict:
    handler = ACTIONS.get(action)
    if not handler:
        raise ValueError(f"Unknown workflow action: {action}")
    async with SessionLocal() as db:
        return await handler(db, context)


@activity.defn
async def run_ai_agent_node(agent_type: str, context: dict) -> dict:
    async with SessionLocal() as db:
        return await _run_ai_agent_step(db, context, agent_type)


async def _load_conversation_actors(db, context: dict):
    """Every context-consuming node beyond ACTION/CONDITION needs the same
    trio. Requires organization_id/customer_id/conversation_id in the
    context — present whenever a workflow was started for a conversation
    (see conversation_router.start_conversation_workflow); absent for the
    older quotation-only triggers, which never reach these node types."""
    organization_id = uuid.UUID(context["organization_id"])
    customer = await db.get(Customer, uuid.UUID(context["customer_id"]))
    conversation = await conversation_service.get_conversation(db, organization_id, uuid.UUID(context["conversation_id"]))
    organization = await db.get(Organization, organization_id)
    return organization, customer, conversation


@activity.defn
async def run_inline_ai_agent_node(node_id: str, config: dict, context: dict) -> dict:
    """The AI_AGENT branch for a node with an inline config (name/
    system_prompt/tools/...) rather than a legacy config.agent_type preset.
    Returns a result for the graph to act on — it never sends anything; see
    node_runtime.run_agent_node's docstring."""
    from app.agents.node_runtime import run_agent_node  # deferred: mirrors the circular-import workaround elsewhere

    async with SessionLocal() as db:
        organization, customer, conversation = await _load_conversation_actors(db, context)
        seed_source = config.get("seed") or context.get("trigger_message")
        seed_text = render_template(seed_source, context) if seed_source else None

        result = await run_agent_node(
            db, organization, customer, conversation,
            node_id=node_id,
            name=config["name"],
            system_prompt=config["system_prompt"],
            tool_names=config.get("tools", []),
            provider=config.get("provider"),
            model=config.get("model"),
            memory=config.get("memory"),
            output_mode=config.get("output_mode", "text"),
            output_schema=config.get("output_schema"),
            seed_text=seed_text,
            workflow_id=context.get("workflow_id") and uuid.UUID(context["workflow_id"]),
            workflow_version_id=context.get("workflow_version_id") and uuid.UUID(context["workflow_version_id"]),
            workflow_execution_id=uuid.UUID(context["execution_id"]) if context.get("execution_id") else None,
        )
        return {
            "text": result.text,
            "structured_output": result.structured_output,
            "tool_calls": result.tool_calls,
            "status": result.status,
        }


@activity.defn
async def run_tool_node(node_id: str, tool_name: str, input_config: dict, context: dict) -> dict:
    """A single named tool, no LLM. Used when the workflow already knows
    exactly which tool to call — search results feeding a SWITCH, a lookup
    the graph needs before deciding what to do next."""
    from app.agents.tool_executor import DISPATCH

    handler = DISPATCH.get(tool_name)
    if handler is None:
        raise ValueError(f"Unknown tool: {tool_name}")

    async with SessionLocal() as db:
        organization_id = uuid.UUID(context["organization_id"])
        customer_id = uuid.UUID(context["customer_id"])
        conversation_id = uuid.UUID(context["conversation_id"])
        rendered_input = render_template(input_config, context)
        try:
            result = await handler(db, organization_id, customer_id, conversation_id, rendered_input)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            return {"error": str(exc)}
        return result


@activity.defn
async def send_message_node(text_template: str, context: dict) -> dict:
    """Renders `text_template` against the context and sends it on the
    conversation's channel. A WEB/BUILDER-channel conversation (the sandbox,
    or a channel with no connected provider) still records the message —
    same behavior the sandbox and builder chat already rely on — it just
    doesn't call an external API."""
    async with SessionLocal() as db:
        organization, customer, conversation = await _load_conversation_actors(db, context)
        text = render_template(text_template, context)
        if not isinstance(text, str):
            text = str(text)

        connection = await integration_service.get_connection_for_org(db, organization.id, conversation.channel.lower())
        provider_cls = _PROVIDERS.get(conversation.channel)
        recipient = customer.whatsapp_phone if conversation.channel == "WHATSAPP" else customer.email

        external_id = None
        sent = False
        if recipient and connection and provider_cls:
            credentials = integration_service.get_credentials(connection)
            external_id = await provider_cls().send(to=recipient, text=text, credentials=credentials)
            sent = True

        sender = connection.external_id if connection else "workflow"
        message = await conversation_service.record_outbound_message(
            db, conversation, sender=sender, recipient=recipient or "n/a", content=text,
            external_message_id=external_id, ai_generated=True,
        )
        await db.commit()
        return {"sent": sent, "message_id": str(message.id), "text": text}


@activity.defn
async def evaluate_condition(check: str, context: dict) -> bool:
    handler = CONDITIONS.get(check)
    if not handler:
        raise ValueError(f"Unknown workflow condition: {check}")
    async with SessionLocal() as db:
        return await handler(db, context)


def _assert_public_url(url: str) -> str:
    """Rejects anything that isn't a public http(s) endpoint.

    HTTP_REQUEST nodes execute a URL that a user — or an AI agent writing a
    workflow definition — supplied, from inside the cluster. Without this an
    agent could be talked into fetching the cloud metadata endpoint, an
    internal admin service, or file:// paths, and writing the result into a
    context the same user can read back. Resolution happens here so a hostname
    that points at 127.0.0.1 is caught, not just a literal IP.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"HTTP_REQUEST only supports http(s) URLs, got {parsed.scheme or 'no'} scheme")
    if not parsed.hostname:
        raise ValueError("HTTP_REQUEST url has no host")

    try:
        resolved = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError(f"HTTP_REQUEST could not resolve {parsed.hostname}: {exc}")

    for family, _type, _proto, _canon, sockaddr in resolved:
        address = ipaddress.ip_address(sockaddr[0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError(
                f"HTTP_REQUEST refused: {parsed.hostname} resolves to the non-public address {address}"
            )
    return url


@activity.defn
async def http_request(config: dict, context: dict) -> dict:
    """Calls an external API and returns a JSON-serialisable summary for the
    context. Never raises on a non-2xx — the status code is data the graph can
    branch on via SWITCH, not a workflow failure."""
    url = _assert_public_url(render_template(config["url"], context))
    method = str(config.get("method", "GET")).upper()
    headers = {str(k): str(v) for k, v in render_template(config.get("headers", {}), context).items()}
    body = render_template(config.get("body"), context) if config.get("body") is not None else None

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False) as client:
        response = await client.request(method, url, headers=headers, json=body)

    raw = response.content[:MAX_HTTP_RESPONSE_BYTES]
    try:
        parsed_body = response.json() if raw else None
    except ValueError:
        parsed_body = raw.decode("utf-8", errors="replace")

    return {
        "status_code": response.status_code,
        "ok": response.is_success,
        "body": parsed_body,
        "truncated": len(response.content) > MAX_HTTP_RESPONSE_BYTES,
    }


@activity.defn
async def load_sub_workflow(organization_id: str, workflow_id: str) -> dict | None:
    """Latest published definition of another workflow in the same org.

    Org-scoped on purpose: a SUB_WORKFLOW node names a bare id, so without the
    organization_id filter one tenant could execute another tenant's graph.
    """
    async with SessionLocal() as db:
        result = await db.execute(
            select(Workflow).where(
                Workflow.organization_id == uuid.UUID(organization_id),
                Workflow.id == uuid.UUID(workflow_id),
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            return None
        version = await db.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow.id)
            .order_by(WorkflowVersion.version.desc())
        )
        latest = version.scalars().first()
        return latest.definition if latest else None


@activity.defn
async def request_human_handoff(execution_id: str, node_id: str, context: dict, reason: str | None) -> None:
    """Notifies the team a conversation is parked for a human. Mirrors
    request_human_approval; kept separate because the two node types will
    likely diverge (assigning a specific agent, SLA timers) once there's a UI
    driving the resume."""
    async with SessionLocal() as db:
        organization_id = uuid.UUID(context["organization_id"])
        body = f"Conversation {context.get('conversation_id')} needs a human"
        if reason:
            body += f": {reason}"
        await notification_service.create_notification(
            db, organization_id, title="Workflow handed off to a human", body=f"{body} (run {execution_id}, step {node_id})",
        )
        await db.commit()


@activity.defn
async def request_human_approval(execution_id: str, node_id: str, prompt: str) -> None:
    """Notifies the team that a run is parked waiting on a decision. The run
    resumes when the approval endpoint signals the workflow."""
    async with SessionLocal() as db:
        execution = await db.get(WorkflowExecution, uuid.UUID(execution_id))
        if execution is None:
            activity.logger.warning("WorkflowExecution %s vanished before approval could be requested", execution_id)
            return
        await notification_service.create_notification(
            db, execution.organization_id,
            title="Workflow needs approval",
            body=f"{prompt} (run {execution_id}, step {node_id})",
        )
        await db.commit()


@activity.defn
async def record_step(execution_id: str, node_id: str, node_type: str, status: str) -> None:
    async with SessionLocal() as db:
        db.add(
            WorkflowExecutionStep(
                execution_id=uuid.UUID(execution_id), node_id=node_id, node_type=node_type, status=status
            )
        )
        await db.commit()


@activity.defn
async def mark_execution_complete(execution_id: str, status: str) -> None:
    async with SessionLocal() as db:
        execution = await db.get(WorkflowExecution, uuid.UUID(execution_id))
        if execution is None:
            # The row was deleted while the workflow was mid-flight (a DELAY
            # node can span days). Nothing left to mark — returning instead of
            # raising avoids retrying against a row that will never come back.
            activity.logger.warning("WorkflowExecution %s no longer exists; skipping status update", execution_id)
            return
        execution.status = status
        execution.completed_at = datetime.now(timezone.utc)
        db.add(execution)
        await db.commit()
