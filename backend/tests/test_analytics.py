import pytest

pytestmark = pytest.mark.asyncio


async def _owner_headers(client, email="owner@analytics.example"):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Owner", "email": email, "password": "supersecret123", "organization_name": "Analytics Co"},
    )
    tokens = resp.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_analytics_summary_reflects_orders_and_quotes(client):
    headers = await _owner_headers(client)

    product = await client.post(
        "/api/v1/products",
        json={"name": "Black T-Shirt", "sku": "TS-BLK", "base_price": "420.00", "variants": [{"sku": "TS-BLK-M"}]},
        headers=headers,
    )
    variant_id = product.json()["variants"][0]["id"]

    customer = await client.post(
        "/api/v1/customers",
        json={"company_name": "ABC Fashion", "contact_name": "Priya", "customer_code": "ABC1"},
        headers=headers,
    )

    warehouse = await client.post("/api/v1/inventory/warehouses", json={"name": "Main", "code": "MAIN"}, headers=headers)
    await client.post(
        "/api/v1/inventory/receive",
        json={"warehouse_id": warehouse.json()["id"], "product_variant_id": variant_id, "quantity": 200},
        headers=headers,
    )

    quote = (
        await client.post(
            "/api/v1/quotes",
            json={"customer_id": customer.json()["id"], "items": [{"product_variant_id": variant_id, "quantity": 10}]},
            headers=headers,
        )
    ).json()
    await client.post(f"/api/v1/quotes/{quote['id']}/send", headers=headers)
    await client.post(f"/api/v1/quotes/{quote['id']}/accept", headers=headers)
    await client.post(
        "/api/v1/orders", json={"quotation_id": quote["id"], "idempotency_key": "analytics-order-1"}, headers=headers
    )

    summary = (await client.get("/api/v1/analytics/summary", headers=headers)).json()
    assert summary["customers_total"] == 1
    assert summary["products_total"] == 1
    assert summary["orders_total"] == 1
    assert summary["revenue_total"] == "4200.00"
    assert summary["quotes_sent"] == 1
    assert summary["quotes_accepted"] == 1
    assert summary["quote_conversion_rate"] == 1.0
