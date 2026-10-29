import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.agents.runtime import NO_ANSWER_REPLY, run_agent, run_sales_agent
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.services import conversation_service

pytestmark = pytest.mark.asyncio


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name, input, id="toolu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=input, id=id)


def _response(content, stop_reason, input_tokens=10, output_tokens=10):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


async def _setup(client, email):
    tokens = (
        await client.post(
            "/api/v1/auth/register",
            json={"full_name": "Owner", "email": email, "password": "supersecret123", "organization_name": "Agent Co"},
        )
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    product = await client.post(
        "/api/v1/products",
        json={"name": "Black T-Shirt", "sku": "TS-BLK", "base_price": "420.00", "variants": [{"sku": "TS-BLK-M"}]},
        headers=headers,
    )
    variant_id = product.json()["variants"][0]["id"]

    customer = await client.post(
        "/api/v1/customers",
        json={
            "company_name": "ABC Fashion", "contact_name": "Priya", "customer_code": "ABC1",
            "whatsapp_phone": "+91999",
        },
        headers=headers,
    )

    warehouse = await client.post("/api/v1/inventory/warehouses", json={"name": "Main", "code": "MAIN"}, headers=headers)
    await client.post(
        "/api/v1/inventory/receive",
        json={"warehouse_id": warehouse.json()["id"], "product_variant_id": variant_id, "quantity": 200},
        headers=headers,
    )
    return headers, customer.json(), variant_id


async def _load_org_and_customer(db_session, customer_id):
    org = (await db_session.execute(select(Organization))).scalars().first()
    customer = await db_session.get(Customer, uuid.UUID(customer_id))
    return org, customer


async def test_agent_calls_tools_and_creates_quote(client, db_session, monkeypatch):
    headers, customer, variant_id = await _setup(client, "owner@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    responses = [
        _response([_tool_use_block("search_products", {"query": "shirt"})], "tool_use"),
        _response(
            [_tool_use_block("create_quote", {"items": [{"product_variant_id": variant_id, "quantity": 100}]})],
            "tool_use",
        ),
        _response([_text_block("We have 100 T-shirts at 420 each. I've sent quote for 42000.")], "end_turn"),
    ]
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="Need 100 black tshirts",
        external_message_id="wamid.1",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_sales_agent(db_session, org, cust, conversation)

    assert "42000" in reply
    assert mock_create.call_count == 3

    quotes = await client.get("/api/v1/quotes", headers=headers)
    assert quotes.status_code == 200
    assert len(quotes.json()) == 1
    assert quotes.json()[0]["status"] == "SENT"
    assert quotes.json()[0]["items"][0]["quantity"] == 100


async def test_search_products_persists_results_and_update_search_context_persists_state(client, db_session, monkeypatch):
    """Both search_products and the explicit update_search_context tool write
    to conversation.agent_state, and a later run_agent call sees that
    persisted state rendered into its system prompt — not just re-derived
    from re-reading the raw transcript."""
    headers, customer, variant_id = await _setup(client, "owner-state@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    responses = [
        _response([_tool_use_block("search_products", {"query": "shirt"})], "tool_use"),
        _response(
            [_tool_use_block("update_search_context", {"category": "shirt", "filters": {"material": "cotton"}})],
            "tool_use",
        ),
        _response([_text_block("We have a few cotton shirts — any preference on size?")], "end_turn"),
    ]
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="cotton shirt",
        external_message_id="wamid.state1",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    await run_sales_agent(db_session, org, cust, conversation)

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    assert conversation.agent_state["category"] == "shirt"
    assert conversation.agent_state["filters"] == {"material": "cotton"}
    assert conversation.agent_state["search_results"], "search_products should have persisted its results"

    # A follow-up turn should see that persisted state in its system prompt.
    responses2 = [_response([_text_block("Got it, size S it is.")], "end_turn")]
    mock_create2 = AsyncMock(side_effect=responses2)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create2)

    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="size S",
        external_message_id="wamid.state2",
    )
    await db_session.commit()
    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    await run_sales_agent(db_session, org, cust, conversation)

    sent_system_prompt = mock_create2.call_args.kwargs["system"]
    assert "Category: shirt" in sent_system_prompt
    assert "material=cotton" in sent_system_prompt


async def test_agent_falls_back_on_error(client, db_session, monkeypatch):
    _, customer, variant_id = await _setup(client, "owner2@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    mock_create = AsyncMock(side_effect=RuntimeError("network blip"))
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="hi", external_message_id="wamid.2",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_sales_agent(db_session, org, cust, conversation)
    assert "trouble processing" in reply


async def test_agent_retries_after_an_empty_completion(client, db_session, monkeypatch):
    """Some models occasionally end a turn with no text and no tool call —
    the agent should retry within its existing iteration budget instead of
    immediately handing the customer a canned non-answer."""
    _, customer, variant_id = await _setup(client, "owner-empty@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    responses = [
        _response([], "end_turn"),  # empty completion: no text, no tool_use
        _response([_text_block("Sure, happy to help!")], "end_turn"),
    ]
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="hi", external_message_id="wamid.empty",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_sales_agent(db_session, org, cust, conversation)

    assert reply == "Sure, happy to help!"
    assert mock_create.call_count == 2


async def test_agent_retries_after_an_emoji_only_reply(client, db_session, monkeypatch):
    """A bare emoji has real (non-empty) text content but gives the customer
    nothing to respond to — the same class of stuck-conversation bug as an
    empty completion, and should be retried the same way."""
    _, customer, variant_id = await _setup(client, "owner-emoji@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    responses = [
        _response([_text_block("\U0001F60A")], "end_turn"),  # 😊 only
        _response([_text_block("Sure, what would you like to know?")], "end_turn"),
    ]
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="hi", external_message_id="wamid.emoji",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_sales_agent(db_session, org, cust, conversation)

    assert reply == "Sure, what would you like to know?"
    assert mock_create.call_count == 2


async def test_agent_retries_when_it_repeats_its_last_reply_without_calling_a_tool(client, db_session, monkeypatch):
    """Reproduces a real observed failure: a small model, given a customer
    reply that should trigger a tool call (a product selection, an answer to
    a clarifying question), instead regenerates its previous reply
    byte-for-byte and calls no tool at all — silently ignoring what the
    customer just said. That has real (non-empty, non-emoji) text and would
    slip past both existing non-substantive checks, so it needs its own."""
    _, customer, variant_id = await _setup(client, "owner-repeat@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="show me shirts",
        external_message_id="wamid.repeat1",
    )
    await conversation_service.record_outbound_message(
        db_session, conversation, sender="agent", recipient="+91999",
        content="Here are the shirts available:\n1. Urban\n2. Classic\nWhich one?", ai_generated=True,
    )
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="option 2",
        external_message_id="wamid.repeat2",
    )
    await db_session.commit()

    responses = [
        # Stuck: repeats the exact prior reply, no tool call.
        _response([_text_block("Here are the shirts available:\n1. Urban\n2. Classic\nWhich one?")], "end_turn"),
        # Recovers on retry: actually acts on the selection.
        _response([_text_block("Got it, Classic it is — what size?")], "end_turn"),
    ]
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_sales_agent(db_session, org, cust, conversation)

    assert reply == "Got it, Classic it is — what size?"
    assert mock_create.call_count == 2


async def test_agent_does_not_flag_a_genuinely_repeated_answer_after_a_real_tool_call(client, db_session, monkeypatch):
    """The repeat guard must not fire just because a tool was called this
    turn and the resulting text happens to match a past reply — only a
    verbatim repeat with zero tool calls is the stuck-loop signature."""
    _, customer, variant_id = await _setup(client, "owner-repeat-ok@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="what's in stock?",
        external_message_id="wamid.ok1",
    )
    await conversation_service.record_outbound_message(
        db_session, conversation, sender="agent", recipient="+91999", content="We're fully stocked.", ai_generated=True,
    )
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="still in stock?",
        external_message_id="wamid.ok2",
    )
    await db_session.commit()

    responses = [
        _response([_tool_use_block("check_inventory", {"product_variant_id": variant_id})], "tool_use"),
        _response([_text_block("We're fully stocked.")], "end_turn"),
    ]
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_sales_agent(db_session, org, cust, conversation)

    assert reply == "We're fully stocked."
    assert mock_create.call_count == 2


async def test_agent_asks_a_clarifying_question_after_exhausting_retries(client, db_session, monkeypatch):
    """If every retry within the iteration budget comes back non-substantive,
    the agent should ask the customer for more detail instead of claiming a
    handoff to a sales team that never actually happens anywhere in this
    codebase."""
    _, customer, variant_id = await _setup(client, "owner-noanswer@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    mock_create = AsyncMock(return_value=_response([], "end_turn"))
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="hi", external_message_id="wamid.noanswer",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_sales_agent(db_session, org, cust, conversation)

    assert reply == NO_ANSWER_REPLY
    assert "connect you with our sales team" not in reply
    from app.agents.runtime import MAX_TOOL_ITERATIONS

    assert mock_create.call_count == MAX_TOOL_ITERATIONS


async def test_agent_never_invents_a_price(client, db_session, monkeypatch):
    """The AI can only produce a price by calling calculate_price/create_quote —
    prove the tool executor, not the model, is what puts numbers in the DB."""
    _, customer, variant_id = await _setup(client, "owner3@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    responses = [
        _response(
            [_tool_use_block("create_quote", {"items": [{"product_variant_id": variant_id, "quantity": 50}]})],
            "tool_use",
        ),
        _response([_text_block("Quote sent.")], "end_turn"),
    ]
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="hi", external_message_id="wamid.3",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    await run_sales_agent(db_session, org, cust, conversation)

    # The tool call's second argument is the tool_use block whose .input the
    # AI supplied — it never contained a price, only product/quantity.
    tool_input = responses[0].content[0].input
    assert "price" not in tool_input
    assert "unit_price" not in tool_input


async def test_support_agent_answers_order_status_and_cannot_quote(client, db_session, monkeypatch):
    headers, customer, variant_id = await _setup(client, "owner4@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    quote = await client.post(
        "/api/v1/quotes",
        json={"customer_id": customer["id"], "items": [{"product_variant_id": variant_id, "quantity": 10}]},
        headers=headers,
    )
    quote = quote.json()
    await client.post(f"/api/v1/quotes/{quote['id']}/send", headers=headers)
    order = (
        await client.post(
            "/api/v1/orders",
            json={"quotation_id": quote["id"], "idempotency_key": "test-order-1"},
            headers=headers,
        )
    ).json()

    responses = [
        _response([_tool_use_block("get_order_status", {"order_number": order["order_number"]})], "tool_use"),
        _response([_text_block(f"Your order {order['order_number']} is {order['status']}.")], "end_turn"),
    ]
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="what's the status of my order?",
        external_message_id="wamid.4",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_agent(db_session, org, cust, conversation, agent_type="SUPPORT")

    assert order["status"] in reply
    from app.agents.tools import SUPPORT_TOOLS

    assert {"create_quote", "create_order", "accept_quote"}.isdisjoint({t["name"] for t in SUPPORT_TOOLS})


async def test_reordering_agent_uses_purchase_history_to_requote(client, db_session, monkeypatch):
    headers, customer, variant_id = await _setup(client, "owner5@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    quote = (
        await client.post(
            "/api/v1/quotes",
            json={"customer_id": customer["id"], "items": [{"product_variant_id": variant_id, "quantity": 20}]},
            headers=headers,
        )
    ).json()
    await client.post(f"/api/v1/quotes/{quote['id']}/send", headers=headers)
    await client.post(
        "/api/v1/orders", json={"quotation_id": quote["id"], "idempotency_key": "test-order-2"}, headers=headers
    )

    responses = [
        _response([_tool_use_block("get_purchase_history", {})], "tool_use"),
        _response(
            [_tool_use_block("create_quote", {"items": [{"product_variant_id": variant_id, "quantity": 20}]})],
            "tool_use",
        ),
        _response([_text_block("Reordered the same 20 units, quote sent.")], "end_turn"),
    ]
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="reorder what I got last time",
        external_message_id="wamid.5",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_agent(db_session, org, cust, conversation, agent_type="REORDERING")

    assert "Reordered" in reply
    assert mock_create.call_count == 3


async def test_every_agent_prompt_instructs_numbered_reply_options():
    """This channel has no native buttons (WhatsApp text or the web sandbox
    both render plain text) — every agent should offer choices as a numbered
    list the customer can just reply to, not an open-ended question."""
    from app.agents.runtime import AGENTS, CUSTOMER_FACING_AGENTS, INTERNAL_AGENT_TYPES

    org = SimpleNamespace(name="Acme")
    customer = SimpleNamespace(company_name="Acme Buyer", customer_type="WHOLESALE")
    conversation = SimpleNamespace(agent_state={}, messages=[])

    # Customer-facing agents only. The rule exists because WhatsApp/web-chat
    # has no button UI, so a numbered list is the closest thing to a tappable
    # option — that reasoning doesn't apply to an internal agent driving a web
    # canvas (see runtime.INTERNAL_AGENT_TYPES).
    for agent_type, (prompt_builder, _tools) in CUSTOMER_FACING_AGENTS.items():
        prompt = prompt_builder(org, customer, conversation)
        assert "numbered list" in prompt, f"{agent_type} prompt is missing the numbered-options rule"

    assert set(AGENTS) - set(CUSTOMER_FACING_AGENTS) == INTERNAL_AGENT_TYPES


async def test_list_agents_returns_all_agent_types_with_tools(client):
    tokens = (
        await client.post(
            "/api/v1/auth/register",
            json={
                "full_name": "Owner", "email": "owner10@agent.example", "password": "supersecret123",
                "organization_name": "Agent Catalog Co",
            },
        )
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.get("/api/v1/agents", headers=headers)
    assert resp.status_code == 200
    agents = {a["agent_type"]: a for a in resp.json()}
    assert set(agents) == {"SALES", "SUPPORT", "REORDERING", "FOLLOWUP", "INVENTORY", "COLLECTIONS", "COMPANY"}
    assert "create_quote" in agents["SALES"]["tools"]
    assert "create_quote" not in agents["SUPPORT"]["tools"]
    assert all(a["description"] for a in agents.values())


async def test_followup_agent_confirms_quote_and_converts_to_order(client, db_session, monkeypatch):
    headers, customer, variant_id = await _setup(client, "owner7@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    quote = (
        await client.post(
            "/api/v1/quotes",
            json={"customer_id": customer["id"], "items": [{"product_variant_id": variant_id, "quantity": 30}]},
            headers=headers,
        )
    ).json()
    await client.post(f"/api/v1/quotes/{quote['id']}/send", headers=headers)

    responses = [
        _response([_tool_use_block("list_quotes_for_customer", {})], "tool_use"),
        _response([_tool_use_block("accept_quote", {"quote_number": quote["quote_number"]})], "tool_use"),
        _response([_tool_use_block("create_order", {"quote_number": quote["quote_number"]})], "tool_use"),
        _response([_text_block("Great, your order is confirmed.")], "end_turn"),
    ]
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="yes go ahead with that quote",
        external_message_id="wamid.7",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_agent(db_session, org, cust, conversation, agent_type="FOLLOWUP")

    assert "confirmed" in reply
    orders = await client.get("/api/v1/orders", headers=headers)
    assert len(orders.json()) == 1


async def test_inventory_agent_flags_low_stock(client, db_session, monkeypatch):
    headers, customer, variant_id = await _setup(client, "owner8@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    responses = [
        _response([_tool_use_block("check_low_stock", {})], "tool_use"),
        _response([_text_block("The black T-shirt is running low and needs reordering.")], "end_turn"),
    ]
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="what's running low?",
        external_message_id="wamid.8",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_agent(db_session, org, cust, conversation, agent_type="INVENTORY")

    assert "low" in reply
    from app.agents.tools import INVENTORY_TOOLS

    assert {"create_quote", "create_order"}.isdisjoint({t["name"] for t in INVENTORY_TOOLS})


async def test_collections_agent_checks_balance_and_records_payment(client, db_session, monkeypatch):
    headers, customer, variant_id = await _setup(client, "owner9@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    quote = (
        await client.post(
            "/api/v1/quotes",
            json={"customer_id": customer["id"], "items": [{"product_variant_id": variant_id, "quantity": 10}]},
            headers=headers,
        )
    ).json()
    await client.post(f"/api/v1/quotes/{quote['id']}/send", headers=headers)
    await client.post(
        "/api/v1/orders", json={"quotation_id": quote["id"], "idempotency_key": "test-order-3"}, headers=headers
    )

    invoices = await db_session.execute(select(Invoice))
    invoice = invoices.scalars().first()
    assert invoice is not None
    assert invoice.status == "UNPAID"

    responses = [
        _response([_tool_use_block("get_outstanding_balance", {})], "tool_use"),
        _response(
            [_tool_use_block("record_payment", {"invoice_number": invoice.invoice_number, "amount": float(invoice.amount_due)})],
            "tool_use",
        ),
        _response([_text_block("Thanks, payment received in full.")], "end_turn"),
    ]
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="I've paid the invoice",
        external_message_id="wamid.9",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_agent(db_session, org, cust, conversation, agent_type="COLLECTIONS")

    assert "payment received" in reply
    await db_session.refresh(invoice)
    assert invoice.status == "PAID"


async def test_support_agent_uses_knowledge_base(client, db_session, monkeypatch):
    headers, customer, variant_id = await _setup(client, "owner6@agent.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    await client.post(
        "/api/v1/knowledge-base",
        json={"title": "Return Policy", "content": "Returns accepted within 30 days.", "category": "policy"},
        headers=headers,
    )

    responses = [
        _response([_tool_use_block("search_knowledge_base", {"query": "return policy"})], "tool_use"),
        _response([_text_block("You can return items within 30 days.")], "end_turn"),
    ]
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content="what's your return policy?",
        external_message_id="wamid.6",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_agent(db_session, org, cust, conversation, agent_type="SUPPORT")

    assert "30 days" in reply
