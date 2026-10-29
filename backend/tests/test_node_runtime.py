"""app.agents.node_runtime.run_agent_node — the loop an inline AI_AGENT node
actually executes. Stubs the model client the same way test_workflow_builder
does; no real provider is called.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.agents.node_runtime import run_agent_node
from app.models.ai_execution import AiExecution
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.organization import Organization


class _StubResponse:
    def __init__(self, content, input_tokens=10, output_tokens=5):
        self.content = content
        self.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        self.stop_reason = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool_use(name, input, id="tu-1"):
    return SimpleNamespace(type="tool_use", name=name, input=input, id=id)


def _stub(monkeypatch, turns):
    it = iter(turns)

    class _Messages:
        async def create(self, **kwargs):
            return _StubResponse(next(it))

    class _Client:
        messages = _Messages()

    # node_runtime imports _client_and_model from app.agents.runtime and calls
    # it positionally (provider, model) — accept and ignore both.
    monkeypatch.setattr("app.agents.node_runtime._client_and_model", lambda *a, **k: (_Client(), "stub-model"))


async def _seed(db_session):
    org = Organization(name="Acme", slug="acme-node-runtime")
    db_session.add(org)
    await db_session.flush()
    customer = Customer(organization_id=org.id, company_name="Buyer", contact_name="Buyer", customer_code="C1", customer_type="OTHER")
    conversation = Conversation(organization_id=org.id, customer_id=None, channel="WEB")
    db_session.add(customer)
    await db_session.flush()
    conversation.customer_id = customer.id
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(org)
    await db_session.refresh(customer)
    await db_session.refresh(conversation)
    return org, customer, conversation


@pytest.fixture
async def db_session(session_maker):
    async with session_maker() as session:
        yield session


async def test_text_mode_returns_plain_text(db_session, monkeypatch):
    org, customer, conversation = await _seed(db_session)
    _stub(monkeypatch, [[_text("What size would you like?")]])

    result = await run_agent_node(
        db_session, org, customer, conversation,
        node_id="ask", name="Sizer", system_prompt="Ask for a size.",
        tool_names=[], output_mode="text", seed_text="I want socks",
    )
    assert result.text == "What size would you like?"
    assert result.structured_output is None
    assert result.status == "SUCCESS"


async def test_structured_mode_parses_json_and_retries_once_on_garbage(db_session, monkeypatch):
    org, customer, conversation = await _seed(db_session)
    _stub(monkeypatch, [[_text("sure, cotton socks")], [_text('{"selected_product_id": "p1", "needs_clarification": false}')]])

    result = await run_agent_node(
        db_session, org, customer, conversation,
        node_id="discovery", name="Discovery", system_prompt="Find the product.",
        tool_names=[], output_mode="structured",
        output_schema={"selected_product_id": "string|null", "needs_clarification": "boolean"},
        seed_text="cotton socks",
    )
    assert result.structured_output == {"selected_product_id": "p1", "needs_clarification": False}


async def test_only_the_nodes_own_tools_are_offered_to_the_model(db_session, monkeypatch):
    """The core guardrail this architecture exists for: a node with two tools
    configured must never see the platform's full registry."""
    org, customer, conversation = await _seed(db_session)
    seen_tool_counts = []

    class _Messages:
        async def create(self, **kwargs):
            seen_tool_counts.append(len(kwargs["tools"]))
            return _StubResponse([_text("ok")])

    class _Client:
        messages = _Messages()

    monkeypatch.setattr("app.agents.node_runtime._client_and_model", lambda *a, **k: (_Client(), "stub-model"))

    await run_agent_node(
        db_session, org, customer, conversation,
        node_id="n", name="Narrow", system_prompt="p",
        tool_names=["search_products", "check_inventory"],
        seed_text="hi",
    )
    assert seen_tool_counts == [2]


async def test_tool_call_is_executed_and_audited_with_node_id(db_session, monkeypatch):
    org, customer, conversation = await _seed(db_session)
    _stub(
        monkeypatch,
        [
            [_tool_use("search_products", {"query": "socks"})],
            [_text("Found some cotton socks.")],
        ],
    )

    result = await run_agent_node(
        db_session, org, customer, conversation,
        node_id="discovery", name="Discovery", system_prompt="Find products.",
        tool_names=["search_products"], seed_text="cotton socks",
    )
    assert result.tool_calls == ["search_products"]
    assert result.text == "Found some cotton socks."

    execution = (await db_session.execute(
        select(AiExecution).where(AiExecution.node_id == "discovery")
    )).scalar_one()
    assert execution.status == "SUCCESS"
    assert execution.tools_available == 1


async def test_does_not_send_anything_it_only_returns_a_result(db_session, monkeypatch):
    """The architectural point of this module: it must never touch
    conversation_service.record_outbound_message or a channel provider."""
    org, customer, conversation = await _seed(db_session)
    _stub(monkeypatch, [[_text("reply text")]])

    await run_agent_node(
        db_session, org, customer, conversation,
        node_id="n", name="N", system_prompt="p", tool_names=[], seed_text="hi",
    )
    assert conversation.messages == []
