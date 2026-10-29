"""Runs one AI_AGENT workflow node.

Deliberately separate from app.agents.runtime.run_agent rather than a
refactor of it: run_agent's retry/small-model-shrink loop is what backs every
tested customer-facing agent today, and the risk of a subtle regression there
outweighs the duplication of a loop shape. What genuinely generalizes —
_client_and_model, _run_tool, history/substantiveness helpers — is reused
directly; the control flow here is new and independently reasoned, on
purpose, so it can't destabilize the legacy path.

Contract that matters most: this function returns a result. It never sends
anything anywhere. The workflow interpreter decides what happens to
`AgentResult.text` — SEND_MESSAGE, a SWITCH on `structured_output`, or nothing
at all. See app/workflows/schema.py's module docstring for the AI_AGENT entry.
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import (
    MAX_TOOL_ITERATIONS,
    _client_and_model,
    _history_to_messages,
    _is_substantive,
    _run_tool,
)
from app.agents.tools import TOOLS_BY_NAME
from app.core.config import settings
from app.models.ai_execution import AiExecution
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.organization import Organization

logger = logging.getLogger(__name__)

# Kept much smaller than the model's context window on purpose — an inline
# node's structured-retry path (below) burns iterations fast, and a node is
# meant to do one focused task, not carry a whole conversation's tool budget.
NODE_MAX_ITERATIONS = MAX_TOOL_ITERATIONS
# One extra turn instructing the model to emit valid JSON when output_mode is
# "structured" and the first attempt didn't parse. Not unbounded retry: a
# model that can't produce JSON on the second try isn't going to on the tenth.
MAX_STRUCTURED_RETRIES = 1

FALLBACK_TEXT = "I'm having trouble processing that right now."


@dataclass
class AgentResult:
    text: str
    structured_output: dict | None
    tool_calls: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str | None = None
    status: str = "SUCCESS"  # SUCCESS | FAILED


def _windowed(messages: list[dict], window: int | None) -> list[dict]:
    if not window:
        return messages
    return messages[-window:]


def _build_messages(
    memory: dict, conversation: Conversation, seed_text: str | None
) -> list[dict]:
    scope = memory.get("scope", "conversation")
    if scope == "conversation":
        messages = _windowed(_history_to_messages(conversation.messages), memory.get("window"))
        if messages:
            return messages
        # A brand-new conversation has no history yet; fall through to the
        # seed so the node still has something to respond to.
    if scope == "none":
        return [{"role": "user", "content": seed_text or "Proceed."}]
    # "workflow_execution", or "conversation" with nothing to show yet: no
    # prior chat, just whatever the workflow handed this node as input.
    return [{"role": "user", "content": seed_text or "Proceed."}]


def _structured_instruction(output_schema: dict) -> str:
    return (
        "\n\nRespond with ONLY a single JSON object matching this shape, no prose before or after it, "
        f"no markdown fences: {json.dumps(output_schema)}"
    )


def _parse_structured(text: str) -> dict | None:
    candidate = text.strip()
    # Models routinely wrap JSON in a fenced block despite being told not to;
    # stripping fences here is cheaper than a second model round trip for the
    # common case, and the real retry path below still exists for the rest.
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.startswith("json"):
            candidate = candidate[4:]
    try:
        parsed = json.loads(candidate.strip())
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def run_agent_node(
    db: AsyncSession,
    organization: Organization,
    customer: Customer,
    conversation: Conversation,
    *,
    node_id: str,
    name: str,
    system_prompt: str,
    tool_names: list[str],
    provider: str | None = None,
    model: str | None = None,
    memory: dict | None = None,
    output_mode: str = "text",
    output_schema: dict | None = None,
    seed_text: str | None = None,
    workflow_id: uuid.UUID | None = None,
    workflow_version_id: uuid.UUID | None = None,
    workflow_execution_id: uuid.UUID | None = None,
) -> AgentResult:
    memory = memory or {"scope": "conversation"}
    tools = [TOOLS_BY_NAME[n] for n in tool_names if n in TOOLS_BY_NAME]
    client, resolved_model = _client_and_model(provider, model)
    messages = _build_messages(memory, conversation, seed_text)
    system = system_prompt + (_structured_instruction(output_schema) if output_mode == "structured" and output_schema else "")

    execution = AiExecution(
        organization_id=organization.id,
        agent_type=name[:30],
        conversation_id=conversation.id,
        model=resolved_model,
        provider=provider or settings.ai_provider,
        workflow_id=workflow_id,
        workflow_version_id=workflow_version_id,
        workflow_execution_id=workflow_execution_id,
        node_id=node_id,
        tools_available=len(tools),
        status="RUNNING",
    )
    db.add(execution)
    # Committed up front for the same reason run_agent does it: a failing tool
    # call rolls the session back, and this row must survive that to keep
    # every AiToolCall pointing at a real execution_id.
    await db.commit()

    start = time.monotonic()
    input_tokens = output_tokens = 0
    final_text = ""
    finish_reason = None
    tool_calls: list[str] = []
    structured_retries = 0
    any_rollback = False

    try:
        for _ in range(NODE_MAX_ITERATIONS):
            response = await client.messages.create(
                model=resolved_model, max_tokens=1024, system=system, messages=messages, tools=tools
            )
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens
            finish_reason = response.stop_reason
            turn_tool_calls = [b.name for b in response.content if b.type == "tool_use"]
            tool_calls.extend(turn_tool_calls)
            logger.info(
                "workflow_agent_node node_id=%s name=%s provider=%s model=%s tools=%d tool_calls=%s "
                "input_tokens=%d output_tokens=%d finish_reason=%s",
                node_id, name, provider or settings.ai_provider, resolved_model, len(tools), turn_tool_calls,
                response.usage.input_tokens, response.usage.output_tokens, finish_reason,
            )

            if response.stop_reason != "tool_use":
                final_text = "".join(b.text for b in response.content if b.type == "text")
                if output_mode == "structured":
                    if _parse_structured(final_text) is not None or structured_retries >= MAX_STRUCTURED_RETRIES:
                        break
                    structured_retries += 1
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": "That wasn't valid JSON. Reply again with ONLY the JSON object."})
                    continue
                if _is_substantive(final_text) or not tools:
                    break
                # No tools to fall back on and nothing substantive to show —
                # further iterations won't manufacture a better answer.
                break

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
            logger.warning("Workflow agent node %s exhausted %d iterations", node_id, NODE_MAX_ITERATIONS)

        execution.status = "SUCCESS"
    except Exception:
        logger.exception("Workflow agent node %s failed (execution_id=%s)", node_id, execution.id)
        execution.status = "FAILED"
        final_text = FALLBACK_TEXT
    finally:
        execution.latency_ms = int((time.monotonic() - start) * 1000)
        execution.input_tokens = input_tokens
        execution.output_tokens = output_tokens
        execution.finish_reason = finish_reason
        execution.retry_count = structured_retries
        execution.estimated_cost = (
            Decimal(input_tokens) / 1_000_000 * Decimal(str(settings.ai_input_price_per_million))
            + Decimal(output_tokens) / 1_000_000 * Decimal(str(settings.ai_output_price_per_million))
        )
        db.add(execution)
        await db.commit()
        if any_rollback:
            # Mirrors run_agent: a rolled-back session expires every object on
            # it, and callers touch these afterward.
            await db.refresh(organization)
            await db.refresh(customer)
            await db.refresh(conversation)

    structured_output = _parse_structured(final_text) if output_mode == "structured" else None
    return AgentResult(
        text=final_text,
        structured_output=structured_output,
        tool_calls=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        finish_reason=finish_reason,
        status=execution.status,
    )
