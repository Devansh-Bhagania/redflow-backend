import pytest

pytestmark = pytest.mark.asyncio


async def _register(client, email="owner@acme.example"):
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Owner Person",
            "email": email,
            "password": "supersecret123",
            "organization_name": "Acme Wholesale",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_register_and_me(client):
    tokens = await _register(client)
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "OWNER"
    assert "customers.read" in body["permissions"]
    assert body["organization"]["name"] == "Acme Wholesale"


async def test_duplicate_email_rejected(client):
    await _register(client)
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Dup",
            "email": "owner@acme.example",
            "password": "supersecret123",
            "organization_name": "Other Org",
        },
    )
    assert resp.status_code == 409


async def test_login_wrong_password(client):
    await _register(client)
    resp = await client.post("/api/v1/auth/login", json={"email": "owner@acme.example", "password": "wrong"})
    assert resp.status_code == 401


async def test_no_token_rejected(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_refresh_rotates_and_old_token_dies(client):
    tokens = await _register(client)
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    new_tokens = resp.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401

    still_good = await client.post("/api/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]})
    assert still_good.status_code == 200


async def test_logout_revokes_refresh_token(client):
    tokens = await _register(client)
    resp = await client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 204
    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401
