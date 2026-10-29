import hashlib
import hmac
import json

import pytest

pytestmark = pytest.mark.asyncio

APP_SECRET = "test-app-secret"
PHONE_NUMBER_ID = "1234567890"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _whatsapp_payload(message_id="wamid.ABC123", text="Hi, need 200 black T-shirts", wa_id="919999999999"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "1", "phone_number_id": PHONE_NUMBER_ID},
                            "contacts": [{"profile": {"name": "Priya"}, "wa_id": wa_id}],
                            "messages": [
                                {"from": wa_id, "id": message_id, "timestamp": "1", "type": "text", "text": {"body": text}}
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


async def _owner_headers(client, email="owner@convo.example"):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Owner", "email": email, "password": "supersecret123", "organization_name": "Convo Co"},
    )
    tokens = resp.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _connect_whatsapp(client, headers):
    resp = await client.post(
        "/api/v1/integrations/whatsapp/connect",
        json={"phone_number_id": PHONE_NUMBER_ID, "access_token": "fake-token", "app_secret": APP_SECRET},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_webhook_verification_challenge(client):
    resp = await client.get(
        "/api/v1/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "dev-verify-token", "hub.challenge": "12345"},
    )
    assert resp.status_code == 200
    assert resp.text == "12345"


async def test_webhook_verification_rejects_wrong_token(client):
    resp = await client.get(
        "/api/v1/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "12345"},
    )
    assert resp.status_code == 403


async def test_inbound_message_creates_lead_conversation_and_message(client):
    headers = await _owner_headers(client)
    await _connect_whatsapp(client, headers)

    body = json.dumps(_whatsapp_payload()).encode()
    resp = await client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
    )
    assert resp.status_code == 200, resp.text

    conversations = await client.get("/api/v1/conversations", headers=headers)
    assert conversations.status_code == 200
    convs = conversations.json()
    assert len(convs) == 1
    assert convs[0]["channel"] == "WHATSAPP"
    assert len(convs[0]["messages"]) == 1
    assert convs[0]["messages"][0]["content"] == "Hi, need 200 black T-shirts"
    assert convs[0]["messages"][0]["direction"] == "INBOUND"

    customers = await client.get("/api/v1/customers", headers=headers)
    assert len(customers.json()) == 1
    assert customers.json()[0]["whatsapp_phone"] == "919999999999"


async def test_webhook_rejects_bad_signature(client):
    headers = await _owner_headers(client, "owner2@convo.example")
    await _connect_whatsapp(client, headers)

    body = json.dumps(_whatsapp_payload()).encode()
    resp = await client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert resp.status_code == 401


async def test_duplicate_webhook_delivery_is_idempotent(client):
    headers = await _owner_headers(client, "owner3@convo.example")
    await _connect_whatsapp(client, headers)

    body = json.dumps(_whatsapp_payload(message_id="wamid.DUPLICATE")).encode()
    sig = _sign(body)
    r1 = await client.post(
        "/api/v1/webhooks/whatsapp", content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig}
    )
    r2 = await client.post(
        "/api/v1/webhooks/whatsapp", content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig}
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    conversations = await client.get("/api/v1/conversations", headers=headers)
    assert len(conversations.json()[0]["messages"]) == 1


async def test_staff_can_send_outbound_message(client, monkeypatch):
    headers = await _owner_headers(client, "owner4@convo.example")
    await _connect_whatsapp(client, headers)

    body = json.dumps(_whatsapp_payload(message_id="wamid.INBOUND1")).encode()
    await client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
    )
    conversation_id = (await client.get("/api/v1/conversations", headers=headers)).json()[0]["id"]

    async def fake_send(self, *, to, text, credentials):
        return "wamid.OUTBOUND1"

    monkeypatch.setattr("app.integrations.whatsapp.WhatsAppProvider.send", fake_send)

    resp = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"content": "We have stock, ₹420/unit"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    messages = resp.json()["messages"]
    assert len(messages) == 2
    assert messages[-1]["direction"] == "OUTBOUND"
    assert messages[-1]["content"] == "We have stock, ₹420/unit"


async def test_ai_can_be_toggled_off_per_conversation(client):
    headers = await _owner_headers(client, "owner5@convo.example")
    await _connect_whatsapp(client, headers)
    body = json.dumps(_whatsapp_payload(message_id="wamid.INBOUND2")).encode()
    await client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
    )
    conversation_id = (await client.get("/api/v1/conversations", headers=headers)).json()[0]["id"]

    resp = await client.patch(
        f"/api/v1/conversations/{conversation_id}", json={"ai_enabled": False}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["ai_enabled"] is False


async def test_conversation_defaults_to_sales_agent_and_can_be_reassigned(client):
    headers = await _owner_headers(client, "owner6@convo.example")
    await _connect_whatsapp(client, headers)
    body = json.dumps(_whatsapp_payload(message_id="wamid.INBOUND3")).encode()
    await client.post(
        "/api/v1/webhooks/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
    )
    conversation = (await client.get("/api/v1/conversations", headers=headers)).json()[0]
    assert conversation["agent_type"] == "SALES"

    resp = await client.patch(
        f"/api/v1/conversations/{conversation['id']}", json={"agent_type": "SUPPORT"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["agent_type"] == "SUPPORT"
