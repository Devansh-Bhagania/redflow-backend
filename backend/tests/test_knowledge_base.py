import pytest

pytestmark = pytest.mark.asyncio


async def _owner_headers(client, email="owner@kb.example"):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Owner", "email": email, "password": "supersecret123", "organization_name": "KB Co"},
    )
    tokens = resp.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_create_list_and_update_article(client):
    headers = await _owner_headers(client)

    created = await client.post(
        "/api/v1/knowledge-base",
        json={"title": "Return Policy", "content": "Returns accepted within 30 days.", "category": "policy"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    article = created.json()
    assert article["is_published"] is True

    listed = await client.get("/api/v1/knowledge-base", headers=headers)
    assert len(listed.json()) == 1

    updated = await client.patch(
        f"/api/v1/knowledge-base/{article['id']}", json={"is_published": False}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["is_published"] is False


async def test_search_only_returns_published_matches(client, db_session):
    headers = await _owner_headers(client)

    await client.post(
        "/api/v1/knowledge-base",
        json={"title": "Shipping times", "content": "We ship within 3 business days.", "category": "faq"},
        headers=headers,
    )
    draft = await client.post(
        "/api/v1/knowledge-base",
        json={"title": "Shipping delay notice", "content": "Draft only.", "is_published": False},
        headers=headers,
    )
    assert draft.status_code == 201

    from sqlalchemy import select

    from app.models.organization import Organization
    from app.services import knowledge_service

    org = (await db_session.execute(select(Organization))).scalars().first()
    results = await knowledge_service.search_articles(db_session, org.id, "shipping")
    assert [a.title for a in results] == ["Shipping times"]
