"""B2B core: customers, products, inventory, pricing, quotations, orders

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-16

"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_lists",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_price_lists_org", "price_lists", ["organization_id"])

    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("categories.id"), nullable=True),
    )
    op.create_index("ix_categories_org", "categories", ["organization_id"])

    op.create_table(
        "brands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
    )
    op.create_index("ix_brands_org", "brands", ["organization_id"])

    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("description", sa.String(2000), nullable=True),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("categories.id"), nullable=True),
        sa.Column("brand_id", sa.Uuid(), sa.ForeignKey("brands.id"), nullable=True),
        sa.Column("unit", sa.String(20), nullable=False, server_default="unit"),
        sa.Column("cost_price", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("base_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("tax_rate", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_products_org", "products", ["organization_id"])

    op.create_table(
        "product_variants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("price_override", sa.Numeric(14, 2), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_product_variants_product", "product_variants", ["product_id"])

    op.create_table(
        "customers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("contact_name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("whatsapp_phone", sa.String(32), nullable=True),
        sa.Column("customer_code", sa.String(50), nullable=False),
        sa.Column("customer_type", sa.String(20), nullable=False, server_default="RETAILER"),
        sa.Column("price_list_id", sa.Uuid(), sa.ForeignKey("price_lists.id"), nullable=True),
        sa.Column("credit_limit", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("payment_terms", sa.String(50), nullable=False, server_default="DUE_ON_RECEIPT"),
        sa.Column("outstanding_balance", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_customers_org", "customers", ["organization_id"])
    op.create_index("ix_customers_whatsapp", "customers", ["whatsapp_phone"])

    op.create_table(
        "warehouses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
    )
    op.create_index("ix_warehouses_org", "warehouses", ["organization_id"])

    op.create_table(
        "inventory",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_variant_id", sa.Uuid(), sa.ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("available_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("incoming_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("damaged_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reorder_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_inventory_org", "inventory", ["organization_id"])
    op.create_index("ix_inventory_warehouse", "inventory", ["warehouse_id"])
    op.create_index("ix_inventory_variant", "inventory", ["product_variant_id"])

    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("inventory_id", sa.Uuid(), sa.ForeignKey("inventory.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_inventory_movements_org", "inventory_movements", ["organization_id"])
    op.create_index("ix_inventory_movements_inventory", "inventory_movements", ["inventory_id"])

    op.create_table(
        "stock_reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("inventory_id", sa.Uuid(), sa.ForeignKey("inventory.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reference_type", sa.String(20), nullable=False),
        sa.Column("reference_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_stock_reservations_org", "stock_reservations", ["organization_id"])
    op.create_index("ix_stock_reservations_inventory", "stock_reservations", ["inventory_id"])
    op.create_index("ix_stock_reservations_reference", "stock_reservations", ["reference_id"])

    op.create_table(
        "price_list_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("price_list_id", sa.Uuid(), sa.ForeignKey("price_lists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_variant_id", sa.Uuid(), sa.ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("min_quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
    )
    op.create_index("ix_price_list_items_list", "price_list_items", ["price_list_id"])
    op.create_index("ix_price_list_items_variant", "price_list_items", ["product_variant_id"])

    op.create_table(
        "quotations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quote_number", sa.String(30), nullable=False),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("tax", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("shipping", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terms", sa.String(2000), nullable=True),
        sa.Column("created_by", sa.String(50), nullable=False, server_default="SYSTEM"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_quotations_org", "quotations", ["organization_id"])
    op.create_index("ix_quotations_customer", "quotations", ["customer_id"])

    op.create_table(
        "quotation_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("quotation_id", sa.Uuid(), sa.ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_variant_id", sa.Uuid(), sa.ForeignKey("product_variants.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("discount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("line_total", sa.Numeric(14, 2), nullable=False),
    )
    op.create_index("ix_quotation_items_quotation", "quotation_items", ["quotation_id"])

    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_number", sa.String(30), nullable=False),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quotation_id", sa.Uuid(), sa.ForeignKey("quotations.id"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("tax", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("shipping", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_orders_org", "orders", ["organization_id"])
    op.create_index("ix_orders_customer", "orders", ["customer_id"])
    op.create_index("ix_orders_idempotency", "orders", ["idempotency_key"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_variant_id", sa.Uuid(), sa.ForeignKey("product_variants.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(14, 2), nullable=False),
    )
    op.create_index("ix_order_items_order", "order_items", ["order_id"])

    op.create_table(
        "order_status_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_order_status_history_order", "order_status_history", ["order_id"])


def downgrade() -> None:
    op.drop_table("order_status_history")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("quotation_items")
    op.drop_table("quotations")
    op.drop_table("price_list_items")
    op.drop_table("stock_reservations")
    op.drop_table("inventory_movements")
    op.drop_table("inventory")
    op.drop_table("warehouses")
    op.drop_table("customers")
    op.drop_table("product_variants")
    op.drop_table("products")
    op.drop_table("brands")
    op.drop_table("categories")
    op.drop_table("price_lists")
