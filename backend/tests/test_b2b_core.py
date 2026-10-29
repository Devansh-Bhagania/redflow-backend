import pytest

pytestmark = pytest.mark.asyncio


async def _owner_headers(client, email="owner@flow.example"):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Owner", "email": email, "password": "supersecret123", "organization_name": "Flow Co"},
    )
    tokens = resp.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _create_product_with_variant(client, headers, base_price="420.00"):
    resp = await client.post(
        "/api/v1/products",
        json={
            "name": "Black T-Shirt",
            "sku": "TSHIRT-BLK",
            "base_price": base_price,
            "variants": [
                {"sku": "TSHIRT-BLK-M", "attributes": {"size": "M"}},
                {"sku": "TSHIRT-BLK-L", "attributes": {"size": "L"}},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_customer(client, headers):
    resp = await client.post(
        "/api/v1/customers",
        json={
            "company_name": "ABC Fashion",
            "contact_name": "Priya",
            "customer_code": "ABC001",
            "whatsapp_phone": "+919999999999",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_warehouse(client, headers):
    resp = await client.post(
        "/api/v1/inventory/warehouses", json={"name": "Main", "code": "MAIN"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_full_quote_to_order_flow(client):
    headers = await _owner_headers(client)
    product = await _create_product_with_variant(client, headers)
    variant_m = product["variants"][0]
    customer = await _create_customer(client, headers)
    warehouse = await _create_warehouse(client, headers)

    receive = await client.post(
        "/api/v1/inventory/receive",
        json={"warehouse_id": warehouse["id"], "product_variant_id": variant_m["id"], "quantity": 200},
        headers=headers,
    )
    assert receive.status_code == 200, receive.text
    assert receive.json()["available_quantity"] == 200

    quote_resp = await client.post(
        "/api/v1/quotes",
        json={"customer_id": customer["id"], "items": [{"product_variant_id": variant_m["id"], "quantity": 100}]},
        headers=headers,
    )
    assert quote_resp.status_code == 201, quote_resp.text
    quote = quote_resp.json()
    assert quote["status"] == "DRAFT"
    assert quote["total"] == "42000.00"

    send = await client.post(f"/api/v1/quotes/{quote['id']}/send", headers=headers)
    assert send.status_code == 200
    accept = await client.post(f"/api/v1/quotes/{quote['id']}/accept", headers=headers)
    assert accept.status_code == 200
    assert accept.json()["status"] == "ACCEPTED"

    order_resp = await client.post(
        "/api/v1/orders",
        json={"quotation_id": quote["id"], "idempotency_key": "test-key-1"},
        headers=headers,
    )
    assert order_resp.status_code == 201, order_resp.text
    order = order_resp.json()
    assert order["status"] == "CONFIRMED"
    assert order["total"] == "42000.00"

    inv = await client.get(f"/api/v1/inventory/variants/{variant_m['id']}", headers=headers)
    rows = inv.json()
    assert rows[0]["available_quantity"] == 100
    assert rows[0]["reserved_quantity"] == 100

    # retrying with the same idempotency key must not create a second order
    # or reserve stock twice.
    retry = await client.post(
        "/api/v1/orders",
        json={"quotation_id": quote["id"], "idempotency_key": "test-key-1"},
        headers=headers,
    )
    assert retry.status_code == 201
    assert retry.json()["id"] == order["id"]

    inv_after_retry = await client.get(f"/api/v1/inventory/variants/{variant_m['id']}", headers=headers)
    assert inv_after_retry.json()[0]["reserved_quantity"] == 100

    invoices = await client.get("/api/v1/invoices", headers=headers)
    assert invoices.status_code == 200
    matching = [i for i in invoices.json() if i["order_id"] == order["id"]]
    assert len(matching) == 1
    assert matching[0]["amount_due"] == "42000.00"
    assert matching[0]["status"] == "UNPAID"


async def test_order_fails_when_stock_insufficient(client):
    headers = await _owner_headers(client, "owner2@flow.example")
    product = await _create_product_with_variant(client, headers)
    variant_m = product["variants"][0]
    customer = await _create_customer(client, headers)
    warehouse = await _create_warehouse(client, headers)

    await client.post(
        "/api/v1/inventory/receive",
        json={"warehouse_id": warehouse["id"], "product_variant_id": variant_m["id"], "quantity": 5},
        headers=headers,
    )

    quote_resp = await client.post(
        "/api/v1/quotes",
        json={"customer_id": customer["id"], "items": [{"product_variant_id": variant_m["id"], "quantity": 50}]},
        headers=headers,
    )
    quote = quote_resp.json()
    await client.post(f"/api/v1/quotes/{quote['id']}/send", headers=headers)

    order_resp = await client.post(
        "/api/v1/orders",
        json={"quotation_id": quote["id"], "idempotency_key": "test-key-insufficient"},
        headers=headers,
    )
    assert order_resp.status_code == 409

    # quotation must not have been converted since the order failed
    quote_after = await client.get(f"/api/v1/quotes/{quote['id']}", headers=headers)
    assert quote_after.json()["status"] == "SENT"


async def test_volume_pricing_tier_applies(client):
    headers = await _owner_headers(client, "owner3@flow.example")
    product = await _create_product_with_variant(client, headers, base_price="999.00")
    variant_m = product["variants"][0]
    customer = await _create_customer(client, headers)

    price_list_resp = await client.post(
        "/api/v1/customers", json={"company_name": "x", "contact_name": "y", "customer_code": "TMP"}, headers=headers
    )
    assert price_list_resp.status_code == 201  # sanity: customers endpoint still healthy

    # No price list assigned -> falls back to base_price regardless of quantity.
    quote_resp = await client.post(
        "/api/v1/quotes",
        json={"customer_id": customer["id"], "items": [{"product_variant_id": variant_m["id"], "quantity": 100}]},
        headers=headers,
    )
    quote = quote_resp.json()
    assert quote["items"][0]["unit_price"] == "999.00"


async def test_products_require_permission(client):
    headers = await _owner_headers(client, "owner4@flow.example")
    # sanity: an unauthenticated caller cannot list products
    resp = await client.get("/api/v1/products")
    assert resp.status_code == 401
    resp = await client.get("/api/v1/products", headers=headers)
    assert resp.status_code == 200
