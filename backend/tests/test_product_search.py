import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.services import product_service

pytestmark = pytest.mark.asyncio


async def _owner_headers(client, email="owner@search.example"):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Owner", "email": email, "password": "supersecret123", "organization_name": "Search Co"},
    )
    tokens = resp.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_search_matches_multi_word_natural_language_queries(client, db_session):
    headers = await _owner_headers(client)
    for name, sku in [("Urban Cotton T-Shirt", "TS-URB"), ("Urban Formal Shirt", "FS-URB")]:
        resp = await client.post(
            "/api/v1/products",
            json={"name": name, "sku": sku, "base_price": "350.00", "variants": [{"sku": f"{sku}-M"}]},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

    org = (await db_session.execute(select(Organization))).scalars().first()

    # A whole-string substring match would fail all of these — none of them
    # is a literal contiguous substring of "Urban Cotton T-Shirt".
    for query, expected_count in [
        ("shirt", 2),
        ("shirts", 2),  # plural
        ("cotton shirt", 1),
        ("cotton t-shirt", 1),
        ("formal shirt", 1),
        ("polo", 0),  # genuinely doesn't exist — correctly empty
    ]:
        results = await product_service.search_products(db_session, org.id, query)
        assert len(results) == expected_count, f"query={query!r} got {[p.name for p in results]}"
