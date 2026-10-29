"""Seed vast demo data into an existing organization.

Run once from backend/: .venv/Scripts/python.exe scripts/seed_demo_data.py
Not idempotent — reruns will hit unique-constraint conflicts (customer_code,
warehouse code) on the rows already created.
"""
import asyncio
import random
import sys
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import SessionLocal
from app.schemas.customer import CustomerCreate
from app.schemas.inventory import WarehouseCreate
from app.schemas.knowledge import KnowledgeBaseArticleCreate
from app.schemas.product import ProductCreate, VariantCreate
from app.schemas.quotation import QuotationCreate, QuotationItemCreate
from app.services import (
    conversation_service,
    customer_service,
    inventory_service,
    invoice_service,
    knowledge_service,
    order_service,
    product_service,
    quotation_service,
)

ORG_ID = uuid.UUID("580bf184-afca-45a4-9a9c-ecf2f91a3b5b")  # LookHub Corp.

random.seed(42)

WAREHOUSES = [("Mumbai DC", "MUM"), ("Delhi DC", "DEL"), ("Bangalore DC", "BLR")]

BASE_PRODUCTS = [
    ("Cotton T-Shirt", "TS", 350, ["S", "M", "L", "XL", "XXL"]),
    ("Premium Hoodie", "HD", 950, ["S", "M", "L", "XL"]),
    ("Slim Fit Jeans", "JN", 1200, ["28", "30", "32", "34", "36"]),
    ("Denim Jacket", "DJ", 1800, ["S", "M", "L", "XL"]),
    ("Formal Shirt", "FS", 650, ["38", "40", "42", "44"]),
    ("Printed Kurta", "KU", 550, ["S", "M", "L", "XL"]),
    ("Silk Saree", "SR", 2200, ["Free Size"]),
    ("Canvas Tote Bag", "TB", 400, ["Standard"]),
    ("Leather Wallet", "WL", 750, ["Standard"]),
    ("Running Shoes", "RS", 1600, ["7", "8", "9", "10", "11"]),
    ("Woolen Sweater", "SW", 900, ["S", "M", "L", "XL"]),
    ("Cargo Shorts", "CS", 700, ["30", "32", "34", "36"]),
    ("Baseball Cap", "CP", 250, ["Standard"]),
    ("Cotton Socks (Pack of 3)", "SK", 150, ["Standard"]),
    ("Track Pants", "TP", 600, ["S", "M", "L", "XL"]),
]
STYLE_PREFIXES = ["Urban", "Classic", "Elite"]

COMPANY_PREFIXES = [
    "Shree", "Om", "Balaji", "National", "United", "Metro", "Prime", "Royal", "Sunrise",
    "Global", "City", "Star", "Apex", "Golden", "Silver", "Pioneer", "Modern", "Universal",
    "Everest", "Horizon", "Vista", "Trident", "Zenith", "Ashoka", "Kohinoor",
]
COMPANY_SUFFIXES = [
    "Textiles", "Garments", "Traders", "Wholesale", "Enterprises", "Fashion House",
    "Apparel", "Retail Corp", "Distributors", "Mart", "Collections", "Emporium",
]
FIRST_NAMES = [
    "Priya", "Raj", "Amit", "Sneha", "Vikram", "Anita", "Rahul", "Pooja", "Karan", "Neha",
    "Arjun", "Divya", "Rohan", "Kavya", "Suresh", "Meera", "Ajay", "Ritu", "Vivek", "Shreya",
]
LAST_NAMES = [
    "Sharma", "Verma", "Patel", "Gupta", "Singh", "Reddy", "Nair", "Iyer", "Menon", "Joshi",
    "Rao", "Kapoor", "Malhotra", "Chawla", "Bhatia",
]
CUSTOMER_TYPES = ("RETAILER", "DISTRIBUTOR", "DEALER", "RESELLER", "CORPORATE", "OTHER")

KB_ARTICLES = [
    ("Return Policy", "policy", "Unopened stock can be returned within 15 days of delivery for a full refund. Custom/bulk orders over 500 units are non-returnable."),
    ("Shipping & Lead Times", "logistics", "Standard orders ship within 3-5 business days. Bulk orders (1000+ units) require a 10-14 day lead time — confirm at quote stage."),
    ("Minimum Order Quantity", "policy", "MOQ is 50 units per SKU for retailers, 200 units per SKU for distributors. Sample orders below MOQ are billed at 1.5x list price."),
    ("Payment Terms", "billing", "New accounts are DUE_ON_RECEIPT. Net-30 terms are available to distributors and corporate accounts after 3 completed orders on time."),
    ("Volume Discount Tiers", "pricing", "10%+ off at 100 units, 18%+ off at 500 units, 25%+ off at 1000+ units per SKU. Tiers are set per customer's assigned price list."),
    ("Defective Goods Policy", "policy", "Report defects within 48 hours of delivery with photos. We replace or credit the invoice — no restocking fee for genuine manufacturing defects."),
    ("GST Invoicing", "billing", "All invoices include GST at the product's configured tax_rate. GSTIN must be on file before the first order ships."),
    ("Warehouse Locations", "logistics", "We ship from Mumbai, Delhi, and Bangalore distribution centers. Orders are fulfilled from the nearest warehouse with sufficient stock."),
    ("Sample Requests", "sales", "One free sample per SKU per quarter for distributor accounts. Retailers can request samples at cost, credited against the first bulk order."),
    ("Account Onboarding", "sales", "New accounts need a GSTIN, a signed credit application (for Net-30), and a primary WhatsApp contact number for order updates."),
]

AGENT_TYPES = ["SALES", "SUPPORT", "REORDERING", "FOLLOWUP", "INVENTORY", "COLLECTIONS"]
CONVO_SCRIPTS = {
    "SALES": [("Hi, do you have the cotton t-shirts in stock for a bulk order?", "Yes, we have good stock — how many units and which sizes do you need?")],
    "SUPPORT": [("What's the status of my last order?", "Let me check that for you — one moment.")],
    "REORDERING": [("Can I reorder what I got last time?", "Sure, looking at your last order now to requote the same items.")],
    "FOLLOWUP": [("Sorry for the delay, still deciding on that quote.", "No problem — let me know if you have any questions about it, happy to help.")],
    "INVENTORY": [("Is the denim jacket back in stock?", "Let me check current availability for you.")],
    "COLLECTIONS": [("How much do I currently owe?", "Let me pull up your outstanding balance.")],
}


def _unique_company_names(n: int) -> list[str]:
    combos = [f"{p} {s}" for p in COMPANY_PREFIXES for s in COMPANY_SUFFIXES]
    random.shuffle(combos)
    return combos[:n]


async def seed_products(db):
    variants_by_product = []
    for style in STYLE_PREFIXES:
        for name, prefix, price, sizes in BASE_PRODUCTS:
            product = await product_service.create_product(
                db, ORG_ID,
                ProductCreate(
                    name=f"{style} {name}",
                    sku=f"{style[:3].upper()}-{prefix}-{uuid.uuid4().hex[:4].upper()}",
                    base_price=Decimal(price),
                    tax_rate=Decimal("5"),
                    variants=[
                        VariantCreate(sku=f"{prefix}-{style[:3].upper()}-{size}", attributes={"size": size})
                        for size in sizes
                    ],
                ),
            )
            variants_by_product.append([v.id for v in product.variants])
    print(f"Created {len(STYLE_PREFIXES) * len(BASE_PRODUCTS)} products")
    return [v for group in variants_by_product for v in group]


async def seed_warehouses(db):
    warehouse_ids = [
        (await inventory_service.create_warehouse(db, ORG_ID, WarehouseCreate(name=n, code=c))).id
        for n, c in WAREHOUSES
    ]
    print(f"Created {len(warehouse_ids)} warehouses")
    return warehouse_ids


async def seed_stock(db, variant_ids, warehouse_ids):
    low_stock_count = 0
    for variant_id in variant_ids:
        for warehouse_id in random.sample(warehouse_ids, k=random.choice([1, 1, 2])):
            qty = random.randint(20, 500)
            inv = await inventory_service.receive_stock(db, ORG_ID, warehouse_id, variant_id, qty)
            if random.random() < 0.15:
                inv.reorder_level = qty + random.randint(10, 50)  # force below-reorder for the Inventory agent
                db.add(inv)
                await db.commit()
                low_stock_count += 1
    print(f"Stocked {len(variant_ids)} variants across {len(warehouse_ids)} warehouses ({low_stock_count} forced low-stock rows)")


async def seed_customers(db):
    customers = []
    for i, company in enumerate(_unique_company_names(60)):
        first, last = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
        customer = await customer_service.create_customer(
            db, ORG_ID,
            CustomerCreate(
                company_name=company,
                contact_name=f"{first} {last}",
                email=f"{first.lower()}.{last.lower()}{i}@example.com",
                whatsapp_phone=f"+9198{10000000 + i}",
                customer_code=f"CUST{i:04d}",
                customer_type=random.choice(CUSTOMER_TYPES),
                credit_limit=Decimal(random.choice([0, 50000, 100000, 250000])),
                payment_terms=random.choice(["DUE_ON_RECEIPT", "NET_30"]),
            ),
        )
        customers.append({"id": customer.id, "whatsapp_phone": customer.whatsapp_phone, "email": customer.email})
    print(f"Created {len(customers)} customers")
    return customers


async def seed_knowledge_base(db):
    for title, category, content in KB_ARTICLES:
        await knowledge_service.create_article(db, ORG_ID, KnowledgeBaseArticleCreate(title=title, category=category, content=content))
    print(f"Created {len(KB_ARTICLES)} knowledge base articles")


async def seed_quotes_orders_invoices(db, customers, variant_ids):
    quotes_sent = quotes_accepted = orders_created = invoices_paid = invoices_partial = 0
    for _ in range(100):
        customer = random.choice(customers)
        items = [
            QuotationItemCreate(product_variant_id=vid, quantity=random.randint(10, 300))
            for vid in random.sample(variant_ids, k=random.randint(1, 4))
        ]
        try:
            quotation = await quotation_service.create_quotation(
                db, ORG_ID, QuotationCreate(customer_id=customer["id"], items=items), created_by="SEED"
            )
        except Exception:
            continue  # e.g. no stock row for a picked variant; skip and move on

        outcome = random.random()
        if outcome < 0.15:
            continue  # leave as DRAFT

        quotation = await quotation_service.send_quotation(db, ORG_ID, quotation.id)
        quotes_sent += 1
        if outcome < 0.35:
            continue  # sent, no response yet

        quotation = await quotation_service.accept_quotation(db, ORG_ID, quotation.id)
        quotes_accepted += 1
        if outcome < 0.5:
            continue  # accepted, not yet converted

        try:
            order = await order_service.create_order_from_quotation(
                db, ORG_ID, quotation.id, idempotency_key=f"seed-{quotation.quote_number}"
            )
        except Exception:
            continue  # insufficient stock left after earlier seeded orders
        orders_created += 1

        invoices = await invoice_service.list_outstanding_for_customer(db, ORG_ID, customer["id"])
        invoice = next((i for i in invoices if i.order_id == order.id), None)
        if invoice and random.random() < 0.6:
            payment = invoice.amount_due if random.random() < 0.6 else invoice.amount_due / 2
            await invoice_service.record_payment(db, ORG_ID, customer["id"], invoice.invoice_number, payment)
            if payment >= invoice.amount_due:
                invoices_paid += 1
            else:
                invoices_partial += 1

    print(
        f"Quotes: {quotes_sent} sent, {quotes_accepted} accepted, {orders_created} converted to orders "
        f"({invoices_paid} invoices paid in full, {invoices_partial} partially paid)"
    )


async def seed_conversations(db, customers):
    count = 0
    for customer in random.sample(customers, k=40):
        channel = random.choice(["WHATSAPP", "EMAIL"])
        agent_type = random.choice(AGENT_TYPES)
        conversation = await conversation_service.get_or_create_conversation(db, ORG_ID, customer["id"], channel)
        conversation.agent_type = agent_type
        db.add(conversation)

        contact = customer["whatsapp_phone"] or customer["email"] or "unknown"
        for round_i, (inbound, outbound) in enumerate(CONVO_SCRIPTS[agent_type]):
            await conversation_service.record_inbound_message(
                db, conversation, sender=contact,
                recipient="business", content=inbound, external_message_id=f"seed-{customer['id']}-{round_i}",
            )
            await conversation_service.record_outbound_message(
                db, conversation, sender="business", recipient=contact,
                content=outbound, ai_generated=True,
            )
        await db.commit()
        count += 1
    print(f"Created {count} conversations with message history")


async def _existing_warehouses(db):
    from sqlalchemy import select

    from app.models.inventory import Warehouse

    result = await db.execute(select(Warehouse.id).where(Warehouse.organization_id == ORG_ID))
    return list(result.scalars().all())


async def _existing_variant_ids(db):
    from sqlalchemy import select

    from app.models.product import Product, ProductVariant

    result = await db.execute(
        select(ProductVariant.id).join(Product).where(Product.organization_id == ORG_ID)
    )
    return list(result.scalars().all())


async def _existing_customers(db):
    from sqlalchemy import select

    from app.models.customer import Customer

    result = await db.execute(
        select(Customer.id, Customer.whatsapp_phone, Customer.email).where(Customer.organization_id == ORG_ID)
    )
    return [{"id": r.id, "whatsapp_phone": r.whatsapp_phone, "email": r.email} for r in result.all()]


async def main():
    async with SessionLocal() as db:
        from sqlalchemy import func, select

        from app.models.customer import Customer
        from app.models.inventory import Warehouse
        from app.models.knowledge import KnowledgeBaseArticle
        from app.models.product import Product

        warehouse_count = await db.scalar(select(func.count()).select_from(Warehouse).where(Warehouse.organization_id == ORG_ID))
        product_count = await db.scalar(select(func.count()).select_from(Product).where(Product.organization_id == ORG_ID))
        customer_count = await db.scalar(select(func.count()).select_from(Customer).where(Customer.organization_id == ORG_ID))
        kb_count = await db.scalar(select(func.count()).select_from(KnowledgeBaseArticle).where(KnowledgeBaseArticle.organization_id == ORG_ID))

        warehouse_ids = await _existing_warehouses(db) if warehouse_count else await seed_warehouses(db)
        variant_ids = await _existing_variant_ids(db) if product_count else await seed_products(db)
        if not product_count:
            await seed_stock(db, variant_ids, warehouse_ids)
        customers = await _existing_customers(db) if customer_count else await seed_customers(db)
        if not kb_count:
            await seed_knowledge_base(db)

        await seed_quotes_orders_invoices(db, customers, variant_ids)
        await seed_conversations(db, customers)
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
