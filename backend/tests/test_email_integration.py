import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.services import conversation_service

pytestmark = pytest.mark.asyncio


async def _owner_headers(client, email="owner@email-integration.example"):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Owner", "email": email, "password": "supersecret123", "organization_name": "Email Co"},
    )
    tokens = resp.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_connect_email_then_send_message(client, db_session, monkeypatch):
    headers = await _owner_headers(client)

    connected = await client.post(
        "/api/v1/integrations/email/connect",
        json={
            "from_email": "sales@emailco.example",
            "smtp_host": "smtp.example.com",
            "smtp_username": "sales@emailco.example",
            "smtp_password": "secret",
        },
        headers=headers,
    )
    assert connected.status_code == 201, connected.text

    customer = (
        await client.post(
            "/api/v1/customers",
            json={
                "company_name": "Buyer Inc", "contact_name": "Sam", "customer_code": "BUY1",
                "email": "buyer@example.com",
            },
            headers=headers,
        )
    ).json()

    org = (await db_session.execute(select(Organization))).scalars().first()
    conversation = await conversation_service.get_or_create_conversation(
        db_session, org.id, uuid.UUID(customer["id"]), "EMAIL"
    )
    await db_session.commit()

    mock_send = AsyncMock(return_value="smtp-fake-id")
    monkeypatch.setattr("app.integrations.email.EmailProvider.send", mock_send)

    resp = await client.post(
        f"/api/v1/conversations/{conversation.id}/messages", json={"content": "Your quote is ready."}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    mock_send.assert_awaited_once()
    assert mock_send.await_args.kwargs["to"] == "buyer@example.com"
    assert resp.json()["messages"][0]["recipient"] == "buyer@example.com"


async def test_second_email_connect_conflicts(client):
    headers = await _owner_headers(client, "owner2@email-integration.example")
    payload = {
        "from_email": "sales@emailco.example", "smtp_host": "smtp.example.com",
        "smtp_username": "u", "smtp_password": "p",
    }
    first = await client.post("/api/v1/integrations/email/connect", json=payload, headers=headers)
    assert first.status_code == 201

    second = await client.post("/api/v1/integrations/email/connect", json=payload, headers=headers)
    assert second.status_code == 409
