from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _response(text):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=5, output_tokens=5),
    )


async def _owner_headers(client, email="owner@sandbox.example"):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Owner", "email": email, "password": "supersecret123", "organization_name": "Sandbox Co"},
    )
    tokens = resp.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.asyncio
async def test_sandbox_get_creates_a_sandbox_customer_and_empty_conversation(client):
    headers = await _owner_headers(client)
    resp = await client.get("/api/v1/agents/sandbox", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["messages"] == []

    customers = await client.get("/api/v1/customers", headers=headers)
    codes = {c["customer_code"] for c in customers.json()}
    assert "SANDBOX" in codes


@pytest.mark.asyncio
async def test_sandbox_message_runs_the_chosen_agent_and_records_both_sides(client, monkeypatch):
    headers = await _owner_headers(client, "owner2@sandbox.example")
    mock_create = AsyncMock(return_value=_response("Hi, how can I help?"))
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    resp = await client.post(
        "/api/v1/agents/sandbox/messages", json={"agent_type": "SUPPORT", "content": "hello"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    conversation = resp.json()
    assert len(conversation["messages"]) == 2
    assert conversation["messages"][0]["direction"] == "INBOUND"
    assert conversation["messages"][0]["content"] == "hello"
    assert conversation["messages"][1]["direction"] == "OUTBOUND"
    assert conversation["messages"][1]["content"] == "Hi, how can I help?"
    assert conversation["messages"][1]["ai_generated"] is True

    summary = await client.get("/api/v1/analytics/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["ai_executions_total"] == 1


@pytest.mark.asyncio
async def test_sandbox_reset_clears_message_history(client, monkeypatch):
    headers = await _owner_headers(client, "owner3@sandbox.example")
    mock_create = AsyncMock(return_value=_response("noted"))
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    await client.post("/api/v1/agents/sandbox/messages", json={"agent_type": "SALES", "content": "hi"}, headers=headers)
    reset = await client.post("/api/v1/agents/sandbox/reset", headers=headers)
    assert reset.status_code == 200
    assert reset.json()["messages"] == []


@pytest.mark.asyncio
async def test_sandbox_reset_clears_persisted_search_state_too(client, monkeypatch):
    """Message history alone isn't the whole session — the persisted search
    state (category/filters/selected product) must be wiped as well, or the
    next turn's system prompt still reflects the old conversation."""
    headers = await _owner_headers(client, "owner-state-reset@sandbox.example")
    mock_create = AsyncMock(
        side_effect=[
            SimpleNamespace(
                content=[SimpleNamespace(type="tool_use", id="t1", name="update_search_context", input={"category": "shirt"})],
                stop_reason="tool_use",
                usage=SimpleNamespace(input_tokens=5, output_tokens=5),
            ),
            _response("Got it, shirts it is."),
        ]
    )
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    await client.post("/api/v1/agents/sandbox/messages", json={"agent_type": "SALES", "content": "shirts"}, headers=headers)
    sandbox = await client.get("/api/v1/agents/sandbox", headers=headers)
    assert sandbox.json()["agent_state"]["category"] == "shirt"

    reset = await client.post("/api/v1/agents/sandbox/reset", headers=headers)
    assert reset.status_code == 200
    assert reset.json()["agent_state"] == {}


@pytest.mark.asyncio
async def test_sandbox_can_target_a_real_customer_instead_of_the_fake_one(client, monkeypatch):
    headers = await _owner_headers(client, "owner-real@sandbox.example")
    customer = (
        await client.post(
            "/api/v1/customers",
            json={"company_name": "Real Co", "contact_name": "Priya", "customer_code": "REAL1", "whatsapp_phone": "+91888"},
            headers=headers,
        )
    ).json()

    mock_create = AsyncMock(return_value=_response("Hi Real Co, how can I help?"))
    monkeypatch.setattr("anthropic.resources.messages.messages.AsyncMessages.create", mock_create)

    resp = await client.post(
        "/api/v1/agents/sandbox/messages",
        json={"agent_type": "SALES", "content": "hi", "customer_id": customer["id"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["customer_id"] == customer["id"]

    # The fake Sandbox Customer's conversation stays untouched.
    default_sandbox = await client.get("/api/v1/agents/sandbox", headers=headers)
    assert default_sandbox.json()["customer_id"] != customer["id"]
    assert default_sandbox.json()["messages"] == []


@pytest.mark.asyncio
async def test_sandbox_rejects_a_customer_id_from_another_org(client, monkeypatch):
    headers_a = await _owner_headers(client, "owner-a@sandbox.example")
    other_customer = (
        await client.post(
            "/api/v1/customers",
            json={"company_name": "Org A Co", "contact_name": "X", "customer_code": "A1", "whatsapp_phone": "+91777"},
            headers=headers_a,
        )
    ).json()

    headers_b = await _owner_headers(client, "owner-b@sandbox.example")
    resp = await client.post(
        "/api/v1/agents/sandbox/messages",
        json={"agent_type": "SALES", "content": "hi", "customer_id": other_customer["id"]},
        headers=headers_b,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sandbox_endpoints_require_auth(client):
    resp = await client.post("/api/v1/agents/sandbox/messages", json={"agent_type": "SALES", "content": "hi"})
    assert resp.status_code == 401


def test_support_role_can_view_but_not_manage_agents():
    from app.core.permissions import DEFAULT_ROLE_PERMISSIONS

    assert "agents.read" in DEFAULT_ROLE_PERMISSIONS["SUPPORT"]
    assert "agents.manage" not in DEFAULT_ROLE_PERMISSIONS["SUPPORT"]
