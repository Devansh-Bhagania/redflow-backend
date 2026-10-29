"""Small-model tool overload fix: dynamic tool selection + adaptive retry.

Reproduces the failing "Cotton" case (see runtime.py's SMALL_MODEL_PROVIDERS
branch) against a mocked OpenAI-compatible client standing in for LM Studio.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.agents.runtime import MAX_EMPTY_RESPONSE_RETRIES, MAX_TOOL_ITERATIONS, NO_ANSWER_REPLY, run_sales_agent
from app.core.config import settings
from app.models.customer import Customer
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


@pytest.fixture(autouse=True)
def _use_lmstudio(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "lmstudio")
    monkeypatch.setattr(settings, "ai_model", "google/gemma-4-e4b")
    monkeypatch.setattr(settings, "ai_base_url", "http://localhost:1234/v1")


async def _setup(client, email):
    tokens = (
        await client.post(
            "/api/v1/auth/register",
            json={"full_name": "Owner", "email": email, "password": "supersecret123", "organization_name": "Small Model Co"},
        )
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    product = await client.post(
        "/api/v1/products",
        json={"name": "Cotton T-Shirt", "sku": "TS-COT", "base_price": "420.00", "variants": [{"sku": "TS-COT-M"}]},
        headers=headers,
    )
    variant_id = product.json()["variants"][0]["id"]

    customer = await client.post(
        "/api/v1/customers",
        json={"company_name": "Trident Enterprises", "contact_name": "Priya", "customer_code": "TRI1", "whatsapp_phone": "+91999"},
        headers=headers,
    )
    return headers, customer.json(), variant_id


async def _load_org_and_customer(db_session, customer_id):
    org = (await db_session.execute(select(Organization))).scalars().first()
    customer = await db_session.get(Customer, uuid.UUID(customer_id))
    return org, customer


async def _run_with_responses(db_session, org, cust, content, responses, monkeypatch):
    mock_create = AsyncMock(side_effect=responses)
    monkeypatch.setattr("app.agents.openai_compatible_client._OpenAICompatibleMessages.create", mock_create)

    conversation = await conversation_service.get_or_create_conversation(db_session, org.id, cust.id, "WHATSAPP")
    await conversation_service.record_inbound_message(
        db_session, conversation, sender="+91999", recipient="123", content=content,
        external_message_id=f"wamid.{uuid.uuid4()}",
    )
    await db_session.commit()

    conversation = await conversation_service.get_conversation(db_session, org.id, conversation.id)
    reply = await run_sales_agent(db_session, org, cust, conversation)
    return reply, mock_create


async def test_ambiguous_followup_gives_up_after_the_reduced_retry_instead_of_looping_6_times(client, db_session, monkeypatch):
    _, customer, variant_id = await _setup(client, "owner-cotton@small.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    responses = [_response([], "end_turn"), _response([], "end_turn")]
    reply, mock_create = await _run_with_responses(db_session, org, cust, "Cotton", responses, monkeypatch)

    assert reply == NO_ANSWER_REPLY
    # 1 initial attempt + MAX_EMPTY_RESPONSE_RETRIES, not all MAX_TOOL_ITERATIONS.
    assert mock_create.call_count == 1 + MAX_EMPTY_RESPONSE_RETRIES
    assert mock_create.call_count < MAX_TOOL_ITERATIONS


async def test_second_attempt_with_reduced_tools_can_still_recover(client, db_session, monkeypatch):
    responses = [_response([], "end_turn"), _response([_text_block("Sure — what material or size?")], "end_turn")]
    _, customer, variant_id = await _setup(client, "owner-recover@small.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    reply, mock_create = await _run_with_responses(db_session, org, cust, "Cotton", responses, monkeypatch)

    assert reply == "Sure — what material or size?"
    assert mock_create.call_count == 2


async def test_short_valid_answer_is_not_treated_as_a_retry_case(client, db_session, monkeypatch):
    responses = [_response([_text_block("Paris.")], "end_turn")]
    _, customer, variant_id = await _setup(client, "owner-paris@small.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    reply, mock_create = await _run_with_responses(
        db_session, org, cust, "What is the capital of France?", responses, monkeypatch
    )

    assert reply == "Paris."
    assert mock_create.call_count == 1


async def test_tool_call_still_works_with_the_reduced_tool_set(client, db_session, monkeypatch):
    responses = [
        _response([_tool_use_block("search_products", {"query": "cotton t shirt"})], "tool_use"),
        _response([_text_block("We have Cotton T-Shirts in stock — how many do you need?")], "end_turn"),
    ]
    _, customer, variant_id = await _setup(client, "owner-search@small.example")
    org, cust = await _load_org_and_customer(db_session, customer["id"])

    reply, mock_create = await _run_with_responses(db_session, org, cust, "Cotton", responses, monkeypatch)

    assert "Cotton T-Shirts" in reply
    assert mock_create.call_count == 2
    # The reduced tool set sent to the model is the small default subset, not the full 7-tool registry.
    first_call_tools = mock_create.call_args_list[0].kwargs["tools"]
    assert len(first_call_tools) <= 5
    assert {"search_products", "check_inventory", "update_search_context"} <= {t["name"] for t in first_call_tools}
