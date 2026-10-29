import json
import logging
import time
import uuid
from decimal import Decimal

import anthropic
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.openai_compatible_client import OpenAICompatibleClient
from app.agents.search_state import render_search_state
from app.agents.tool_executor import DISPATCH
from app.agents.tool_selector import select_tools
from app.agents.tools import (
    COLLECTIONS_TOOLS,
    COMPANY_TOOLS,
    FOLLOWUP_TOOLS,
    INVENTORY_TOOLS,
    REORDERING_TOOLS,
    SUPPORT_TOOLS,
    TOOLS,
    WORKFLOW_BUILDER_TOOLS,
    compact_tools,
)
from app.core.config import settings
from app.models.ai_execution import AiExecution, AiToolCall
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.organization import Organization

logger = logging.getLogger(__name__)

MODEL = "claude-opus-5"
DEFAULT_GROQ_MODEL = "meta-llama/llama-prompt-guard-2-22m"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
MAX_TOOL_ITERATIONS = 6
# Local/self-hosted providers run whatever the operator loaded — often a small
# model that gets overwhelmed by the full tool registry + system prompt on a
# short/ambiguous turn (see the "Cotton" case this was built for: ~8K input
# tokens, 15+ tool schemas, ~0 output tokens back). The hosted Anthropic API
# is always a full-size Claude model and keeps the unrestricted behavior.
SMALL_MODEL_PROVIDERS = ("lmstudio", "groq")
MAX_TOOLS_PER_LOCAL_MODEL_TURN = 5
# One extra attempt with a smaller, compact-schema tool set before giving up —
# not another 5 retries of the same oversized request that just failed.
MAX_EMPTY_RESPONSE_RETRIES = 1
# Used only when the API call itself failed (network error, bad response, etc.)
FALLBACK_REPLY = "I'm having trouble processing that right now — please try again in a moment."
# Used when every retry produced a non-substantive completion: the model is
# reachable and responding, it just isn't giving a usable answer. A real dead
# end ("I'll connect you with our sales team") would be a lie — nothing in
# this codebase actually performs that handoff — so keep the conversation
# moving instead of promising an escalation that never happens.
NO_ANSWER_REPLY = "Sorry, could you tell me a bit more about what you're looking for? I want to make sure I get you the right details."


_CONTEXT_RULE = (
    "- Read the full conversation so far, not just the latest message — combine details the customer has given "
    "across earlier turns (item, size, quantity, etc.) instead of responding as if only the newest message exists.\n"
)

# There's no native button/quick-reply UI on this channel (WhatsApp text or
# the web sandbox both render plain text) — the closest thing to a tappable
# option is a short numbered list the customer can reply to with just "1" or
# "2". Applies whenever a turn ends on a decision point; a plain factual
# answer ("your order is Shipped") doesn't need one.
_OPTIONS_RULE = (
    "- When your reply leaves the customer with a choice to make (which product, which size, whether to confirm "
    "something, what to do next), end with 2-4 short concrete options as a numbered list they can reply to with "
    "just the number — not an open-ended question like \"what would you like?\". Only list options that are real "
    "given what you've already found via tools (e.g. actual product names from a search), never invented ones.\n"
    "- When the customer replies with just a number or \"option N\", match it against the numbered list YOU sent "
    "most recently and act on that specific item immediately (e.g. record it via update_search_context, or "
    "whatever the list was offering) — do not re-send the same list again as if the reply hadn't answered it.\n"
)


def _client_and_model(provider: str | None = None, model: str | None = None) -> tuple[object, str]:
    """`provider`/`model` override the global settings — used by workflow
    AI_AGENT nodes, which may pin either independently of the deployment's
    default. Every legacy call site passes neither and gets the old
    settings-only behavior unchanged."""
    provider = provider or settings.ai_provider
    if provider == "groq":
        client = OpenAICompatibleClient(base_url=GROQ_BASE_URL, api_key=settings.ai_api_key)
        return client, model or settings.ai_model or DEFAULT_GROQ_MODEL
    if provider == "lmstudio":
        # Local inference can be slow, especially without GPU acceleration —
        # give it much more room than a hosted API before timing out.
        client = OpenAICompatibleClient(base_url=settings.ai_base_url, api_key=settings.ai_api_key or "lm-studio", timeout=300)
        resolved_model = model or settings.ai_model
        if not resolved_model:
            raise ValueError("AI_MODEL must be set to the exact model id LM Studio reports at GET /v1/models")
        return client, resolved_model
    return anthropic.AsyncAnthropic(api_key=settings.ai_api_key or None), model or settings.ai_model or MODEL


def _sales_prompt(org: Organization, customer: Customer, conversation: Conversation) -> str:
    return (
        f"You are the AI Sales Agent for {org.name}, a wholesale business, replying to a customer over WhatsApp.\n\n"
        f"Customer: {customer.company_name} ({customer.customer_type}).\n\n"
        f"{render_search_state(conversation.agent_state)}\n\n"
        "Rules:\n"
        f"{_CONTEXT_RULE}"
        f"{_OPTIONS_RULE}"
        "- Never invent a product, price, or stock number — always use a tool to look it up.\n"
        "- search_products matches whatever words you give it against the catalog — it doesn't need an exact name "
        "or SKU. A partial description like \"shirt\" or \"cotton shirt\" is a valid search on its own; call the "
        "tool with what you have instead of asking for more detail first.\n"
        "- When the customer has given product details across several messages (item type, material, size, "
        "style, etc.), combine everything they've said so far into one search query rather than searching on "
        "only the latest message.\n"
        "- Call update_search_context whenever the category, filters, selected product, or quantity change — "
        "this is what's shown above as the persisted search state, so keep it current rather than relying on "
        "your own memory of the conversation. Use clear_filters=true when the customer switches to a materially "
        "different search (e.g. a new category) so old filters like size don't linger.\n"
        "- If a search returns no results, say so plainly and ask what to try instead — don't just repeat a "
        "generic clarifying question.\n"
        "- Call create_quote only once you know the specific items and quantities the customer wants.\n"
        "- Call accept_quote only when the customer has clearly confirmed a specific quote.\n"
        "- Call create_order only after the quote has been accepted.\n"
        "- Keep replies short and WhatsApp-appropriate: a sentence or two.\n"
        "- For anything outside products, pricing, quotes, and orders, say you're connecting them with the team."
    )


def _support_prompt(org: Organization, customer: Customer, conversation: Conversation) -> str:
    return (
        f"You are the AI Support Agent for {org.name}, a wholesale business, replying to a customer over WhatsApp.\n\n"
        f"Customer: {customer.company_name} ({customer.customer_type}).\n\n"
        "Rules:\n"
        f"{_CONTEXT_RULE}"
        f"{_OPTIONS_RULE}"
        "- Answer questions about the product catalog, stock, and this customer's own past orders — always via a tool, never from memory.\n"
        "- You cannot create quotes or orders. If the customer wants to place a new order, say you're connecting them with sales.\n"
        "- Keep replies short and WhatsApp-appropriate: a sentence or two."
    )


def _reordering_prompt(org: Organization, customer: Customer, conversation: Conversation) -> str:
    return (
        f"You are the AI Reordering Agent for {org.name}, a wholesale business, replying to a customer over WhatsApp.\n\n"
        f"Customer: {customer.company_name} ({customer.customer_type}).\n\n"
        f"{render_search_state(conversation.agent_state)}\n\n"
        "Rules:\n"
        f"{_CONTEXT_RULE}"
        f"{_OPTIONS_RULE}"
        "- Use get_purchase_history to see what this customer has bought before and suggest reordering it.\n"
        "- Never invent a product, price, or stock number — always use a tool to look it up.\n"
        "- Call update_search_context whenever the category, filters, selected product, or quantity change.\n"
        "- Call create_quote only once the customer has confirmed the specific items and quantities to reorder.\n"
        "- Keep replies short and WhatsApp-appropriate: a sentence or two."
    )


def _followup_prompt(org: Organization, customer: Customer, conversation: Conversation) -> str:
    return (
        f"You are the AI Follow-up Agent for {org.name}, a wholesale business, replying to a customer over "
        "WhatsApp who was sent a quote and later nudged because they went quiet.\n\n"
        f"Customer: {customer.company_name} ({customer.customer_type}).\n\n"
        "Rules:\n"
        f"{_CONTEXT_RULE}"
        f"{_OPTIONS_RULE}"
        "- Use list_quotes_for_customer to see which quote this is about and its current status.\n"
        "- If the customer confirms they want to go ahead, call accept_quote then create_order.\n"
        "- If they have questions or objections about the quote, address them briefly — you cannot create a new "
        "quote yourself; say you're connecting them with sales for that.\n"
        "- Keep replies short and WhatsApp-appropriate: a sentence or two."
    )


def _inventory_prompt(org: Organization, customer: Customer, conversation: Conversation) -> str:
    return (
        f"You are the AI Inventory Agent for {org.name}, a wholesale business, replying to a customer over "
        "WhatsApp about product availability.\n\n"
        f"Customer: {customer.company_name} ({customer.customer_type}).\n\n"
        "Rules:\n"
        f"{_CONTEXT_RULE}"
        f"{_OPTIONS_RULE}"
        "- Never invent a stock number — always use a tool to look it up.\n"
        "- Use check_low_stock if asked what's running low or needs reordering.\n"
        "- You cannot create quotes or orders. If the customer wants to place an order, say you're connecting "
        "them with sales.\n"
        "- Keep replies short and WhatsApp-appropriate: a sentence or two."
    )


def _collections_prompt(org: Organization, customer: Customer, conversation: Conversation) -> str:
    return (
        f"You are the AI Collections Agent for {org.name}, a wholesale business, replying to a customer over "
        "WhatsApp about an outstanding balance.\n\n"
        f"Customer: {customer.company_name} ({customer.customer_type}).\n\n"
        "Rules:\n"
        f"{_CONTEXT_RULE}"
        f"{_OPTIONS_RULE}"
        "- Use get_outstanding_balance to see what this customer owes before saying anything about amounts.\n"
        "- Call record_payment only once the customer has clearly stated an amount and which invoice it's for.\n"
        "- Be polite and non-confrontational — this is a reminder, not a threat.\n"
        "- Keep replies short and WhatsApp-appropriate: a sentence or two."
    )


def _company_prompt(org: Organization, customer: Customer, conversation: Conversation) -> str:
    return (
        f"You are the AI Company Agent for {org.name}, a wholesale business, replying to a customer over "
        "WhatsApp with general questions about the business itself (hours, policies, shipping, returns, "
        "how things work) rather than a specific product, order, or account.\n\n"
        f"Customer: {customer.company_name} ({customer.customer_type}).\n\n"
        "Rules:\n"
        f"{_CONTEXT_RULE}"
        f"{_OPTIONS_RULE}"
        "- Always use search_knowledge_base to answer — never invent a policy or fact from memory.\n"
        "- If the knowledge base has nothing relevant, say so plainly rather than guessing.\n"
        "- For anything about a specific product, price, order, or account, say you're connecting them with "
        "the right team.\n"
        "- Keep replies short and WhatsApp-appropriate: a sentence or two."
    )


def _workflow_builder_prompt(org: Organization, customer: Customer, conversation: Conversation) -> str:
    """The one agent that isn't talking to a customer — `customer` is the
    synthetic sandbox record the shared runtime requires, and is unused here."""
    return (
        f"You are the Workflow Builder for {org.name}. You help an operator design and refine an automation "
        "workflow: a directed graph of nodes that runs when a trigger event fires.\n\n"
        "How you work:\n"
        "- Call get_workflow_definition before your first change, so you are editing what exists rather than "
        "what you imagine exists.\n"
        "- Call list_workflow_capabilities before using any node type, action, condition or agent you have not "
        "already seen in this workflow. Never invent an action or condition name — only the registered ones "
        "exist, and a definition naming anything else is rejected on save.\n"
        "- Send the COMPLETE graph to propose_workflow_definition every time, never a patch or a fragment.\n"
        "- propose_workflow_definition validates and dry-runs what you send. If it reports invalid, or the "
        "sandbox status is not COMPLETED, fix the graph and call it again. Never tell the operator a workflow "
        "is ready when the tool told you it is broken.\n"
        "- You cannot save or publish. Your proposal sits on the canvas until the operator applies it — say so "
        "plainly rather than implying the change is already live.\n\n"
        "Graph rules:\n"
        "- Exactly one TRIGGER node, and every path needs a way to reach an END node.\n"
        "- CONDITION edges are labelled true/false, HUMAN_APPROVAL approved/rejected, LOOP body/done. A SWITCH "
        "node's edges are labelled with one of its own case values, or 'default'.\n"
        "- A LOOP node needs a 'body' edge whose path leads back to the LOOP node itself, and a 'done' edge for "
        "when the list is exhausted.\n"
        "- Reference context values as {{dotted.path}} inside SET values and HTTP_REQUEST urls, headers and bodies.\n\n"
        "Explain changes in plain language — \"wait a day, then check whether they replied\", not a dump of node "
        "ids. Keep replies to a few sentences."
    )


AGENTS = {
    "SALES": (_sales_prompt, TOOLS),
    "SUPPORT": (_support_prompt, SUPPORT_TOOLS),
    "REORDERING": (_reordering_prompt, REORDERING_TOOLS),
    "FOLLOWUP": (_followup_prompt, FOLLOWUP_TOOLS),
    "INVENTORY": (_inventory_prompt, INVENTORY_TOOLS),
    "COLLECTIONS": (_collections_prompt, COLLECTIONS_TOOLS),
    "COMPANY": (_company_prompt, COMPANY_TOOLS),
    "WORKFLOW_BUILDER": (_workflow_builder_prompt, WORKFLOW_BUILDER_TOOLS),
}

AGENT_DESCRIPTIONS = {
    "SALES": "Answers product/pricing questions and can quote, accept, and convert an order.",
    "SUPPORT": "Answers catalog and order-status questions. Cannot quote or order.",
    "REORDERING": "Looks at a customer's purchase history and requotes what they bought before.",
    "FOLLOWUP": "Handles a customer's reply to a quiet-quote nudge; can push a confirmed quote to an order.",
    "INVENTORY": "Answers stock/availability questions and flags what's at or below its reorder point.",
    "COLLECTIONS": "Checks a customer's outstanding balance and records a payment they report making.",
    "COMPANY": "Answers general questions about the business itself (hours, policies, shipping, returns).",
    "WORKFLOW_BUILDER": "Designs and refines automation workflows. Proposes graph changes for you to apply; cannot save or publish.",
}

# Agents that talk to an operator inside the app rather than to a customer over
# a messaging channel. They're excluded from the agent catalog and the customer
# sandbox: their tools need context the sandbox can't supply (the builder's
# tools require a workflow-pinned conversation), and the customer-channel
# prompt conventions — WhatsApp-length replies, numbered tap-through options —
# don't apply to a web UI.
INTERNAL_AGENT_TYPES = frozenset({"WORKFLOW_BUILDER"})

CUSTOMER_FACING_AGENTS = {k: v for k, v in AGENTS.items() if k not in INTERNAL_AGENT_TYPES}


def _is_substantive(text: str) -> bool:
    """Rejects replies with no actual content — emoji-only ("😊"), pure
    whitespace/punctuation, etc. — the same failure mode as an empty
    completion, just non-empty enough to slip past a truthiness check."""
    return any(c.isalnum() for c in text)


def _last_outbound_text(messages) -> str | None:
    for m in reversed(messages):
        if m.direction == "OUTBOUND":
            return m.content
    return None


def _made_no_progress(final_text: str, previous_reply: str | None, tool_called_this_run: bool) -> bool:
    """Catches a specific small-model failure mode that isn't "no text" or
    "gibberish" — the two things _is_substantive already screens for — but is
    just as stuck: the model repeats its previous reply byte-for-byte while
    calling no tool at all, ignoring whatever new thing the customer just
    said (a product selection, an answer to a clarifying question, ...).
    A tool call happening this turn, or the reply genuinely differing from
    last time, both mean real progress and aren't flagged."""
    if tool_called_this_run or previous_reply is None:
        return False
    return final_text.strip() == previous_reply.strip()


def _history_to_messages(messages) -> list[dict]:
    return [{"role": "user" if m.direction == "INBOUND" else "assistant", "content": m.content} for m in messages]


def _last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m["role"] == "user" and isinstance(m["content"], str):
            return m["content"]
    return ""


def _turn_tools(tools: list[dict], small_model: bool, messages: list[dict], max_tools: int) -> list[dict]:
    if not small_model:
        return tools
    return compact_tools(select_tools(tools, _last_user_text(messages), max_tools))


async def run_sales_agent(
    db: AsyncSession, organization: Organization, customer: Customer, conversation: Conversation
) -> str:
    return await run_agent(db, organization, customer, conversation, agent_type="SALES")


async def run_agent(
    db: AsyncSession, organization: Organization, customer: Customer, conversation: Conversation, agent_type: str
) -> str:
    prompt_builder, tools = AGENTS[agent_type]
    client, model = _client_and_model()
    system = prompt_builder(organization, customer, conversation)
    messages = _history_to_messages(conversation.messages)

    execution = AiExecution(
        organization_id=organization.id,
        agent_type=agent_type,
        conversation_id=conversation.id,
        model=model,
        status="RUNNING",
    )
    db.add(execution)
    # Committed rather than just flushed: a failing tool call rolls the session
    # back (see _run_tool), which would otherwise discard this row and leave
    # every AiToolCall pointing at a missing execution_id. Persisting it up
    # front also means a run that crashes outright still leaves a RUNNING row
    # behind instead of vanishing from the audit trail.
    await db.commit()

    start = time.monotonic()
    input_tokens = 0
    output_tokens = 0
    final_text = ""
    small_model = settings.ai_provider in SMALL_MODEL_PROVIDERS
    empty_retries = 0
    active_tools = _turn_tools(tools, small_model, messages, MAX_TOOLS_PER_LOCAL_MODEL_TURN)
    previous_reply = _last_outbound_text(conversation.messages)
    tool_called_this_run = False
    any_rollback = False

    try:
        for _ in range(MAX_TOOL_ITERATIONS):
            response = await client.messages.create(
                model=model, max_tokens=1024, system=system, messages=messages, tools=active_tools
            )
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens
            tool_calls = [b.name for b in response.content if b.type == "tool_use"]
            logger.info(
                "agent_call agent_type=%s provider=%s model=%s tools=%d selected=%s input_tokens=%d "
                "output_tokens=%d finish_reason=%s tool_calls=%s empty_retries=%d",
                agent_type, settings.ai_provider, model, len(active_tools), [t["name"] for t in active_tools],
                response.usage.input_tokens, response.usage.output_tokens, response.stop_reason, tool_calls,
                empty_retries,
            )

            if response.stop_reason != "tool_use":
                final_text = "".join(b.text for b in response.content if b.type == "text")
                stuck_repeat = _made_no_progress(final_text, previous_reply, tool_called_this_run)
                if _is_substantive(final_text) and not stuck_repeat:
                    break
                # Some models (small local ones especially) occasionally end a
                # turn with no tool call and either no text or non-substantive
                # text (e.g. a bare emoji) — leaving the customer with nothing
                # to respond to. A verbatim repeat of the last reply with no
                # tool call is the same failure in disguise: real text, but
                # the customer's new message (a selection, an answer) was
                # never actually acted on.
                logger.warning(
                    "Agent got a %s (agent_type=%s, model=%s), retrying",
                    "verbatim repeat of its last reply" if stuck_repeat else f"non-substantive completion {final_text!r}",
                    agent_type, model,
                )
                if small_model:
                    # A small model that produced nothing against the current
                    # (already reduced) tool set is unlikely to do better
                    # against the exact same request — shrink further once,
                    # then give up rather than burning the rest of the
                    # MAX_TOOL_ITERATIONS budget on repeats of a failing call.
                    empty_retries += 1
                    if empty_retries > MAX_EMPTY_RESPONSE_RETRIES:
                        logger.warning(
                            "Small model (%s) exhausted %d empty-response retr%s — bailing out early "
                            "(agent_type=%s)",
                            model, MAX_EMPTY_RESPONSE_RETRIES, "y" if MAX_EMPTY_RESPONSE_RETRIES == 1 else "ies",
                            agent_type,
                        )
                        final_text = NO_ANSWER_REPLY
                        break
                    active_tools = _turn_tools(
                        tools, small_model, messages, max(1, MAX_TOOLS_PER_LOCAL_MODEL_TURN // 2)
                    )
                # A cloud model retries with the exact same (already
                # reasonably sized) request — exhausting MAX_TOOL_ITERATIONS
                # still falls through to NO_ANSWER_REPLY below.
                continue

            tool_called_this_run = True
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_result, rolled_back = await _run_tool(
                    db, organization.id, customer.id, conversation.id, execution.id, tools, block
                )
                tool_results.append(tool_result)
                any_rollback = any_rollback or rolled_back
            messages.append({"role": "user", "content": tool_results})
        else:
            logger.warning(
                "Agent exhausted all %d iterations without a substantive reply (agent_type=%s, model=%s)",
                MAX_TOOL_ITERATIONS, agent_type, model,
            )
            final_text = NO_ANSWER_REPLY

        execution.status = "SUCCESS"
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (400, 403) and "tool" in exc.response.text.lower():
            # The overwhelmingly common cause: AI_MODEL is set to something
            # that isn't a tool-calling-capable chat model (a classifier, an
            # agentic black-box like groq/compound, etc).
            logger.error(
                "AI_MODEL=%r doesn't support tool calling on this provider — pick a tool-calling chat model "
                "(agent_type=%s, execution_id=%s). Provider response: %s",
                model, agent_type, execution.id, exc.response.text[:300],
            )
        else:
            logger.exception(
                "Agent run failed (agent_type=%s, execution_id=%s). Provider response: %s",
                agent_type, execution.id, exc.response.text[:500],
            )
        execution.status = "FAILED"
        final_text = FALLBACK_REPLY
    except Exception:
        logger.exception("Agent run failed (agent_type=%s, execution_id=%s)", agent_type, execution.id)
        execution.status = "FAILED"
        final_text = FALLBACK_REPLY
    finally:
        execution.latency_ms = int((time.monotonic() - start) * 1000)
        execution.input_tokens = input_tokens
        execution.output_tokens = output_tokens
        execution.estimated_cost = Decimal(input_tokens) / 1_000_000 * Decimal(str(settings.ai_input_price_per_million)) + Decimal(
            output_tokens
        ) / 1_000_000 * Decimal(str(settings.ai_output_price_per_million))
        db.add(execution)
        await db.commit()
        if any_rollback:
            # A tool handler's failure rolled back the whole session (see
            # _run_tool), which unconditionally expires every object in it —
            # unlike commit, that isn't affected by expire_on_commit=False.
            # Every caller of run_agent touches these objects afterward
            # (organization.id, conversation.messages, ...), so refresh here
            # once rather than making every call site guard against it.
            await db.refresh(organization)
            await db.refresh(customer)
            await db.refresh(conversation)

    return final_text or "Thanks — let me look into that."


async def _run_tool(
    db: AsyncSession,
    organization_id: uuid.UUID,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID,
    execution_id: uuid.UUID,
    tools: list,
    block,
) -> dict:
    tool_name = block.name
    handler = DISPATCH.get(tool_name)
    tool_def = next((t for t in tools if t["name"] == tool_name), None)
    risk_level = tool_def["risk_level"] if tool_def else "SAFE"

    start = time.monotonic()
    is_error = False
    rolled_back = False
    try:
        # DISPATCH is global, so without this check a model that names a tool
        # outside its agent's registry (hallucinated, or carried over from
        # another agent's prompt) would still have it executed — the SUPPORT
        # agent's "you cannot create quotes or orders" would be enforced only
        # by the prompt. Authorization belongs here, not in the instructions.
        if tool_def is None:
            raise PermissionError(f"Tool {tool_name!r} is not available to this agent")
        if not handler:
            raise ValueError(f"Unknown tool: {tool_name}")
        result = await handler(db, organization_id, customer_id, conversation_id, block.input)
    except Exception as exc:
        is_error = True
        result = {"error": str(exc)}
        # A handler that failed part-way (especially on a DB error) can leave
        # the session needing a rollback; without this every later statement
        # in the run — including the AiToolCall insert below and the final
        # commit — fails too, turning one bad tool call into a failed run.
        if not isinstance(exc, (PermissionError, ValueError)):
            try:
                await db.rollback()
                rolled_back = True
            except Exception:
                logger.exception("Rollback after a failed tool call also failed (tool=%s)", tool_name)

    db.add(
        AiToolCall(
            execution_id=execution_id,
            tool_name=tool_name,
            arguments=block.input,
            result_summary=json.dumps(result)[:2000],
            status="ERROR" if is_error else "SUCCESS",
            risk_level=risk_level,
            latency_ms=int((time.monotonic() - start) * 1000),
        )
    )

    return (
        {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result), "is_error": is_error},
        rolled_back,
    )
