"""Canonical permission catalog and default role -> permission mapping.

This is the single source of truth seeded into the `permissions` / `roles` /
`role_permissions` tables by the initial migration.
"""

PERMISSIONS: dict[str, str] = {
    "customers.read": "View customers",
    "customers.create": "Create customers",
    "customers.update": "Edit customers",
    "products.read": "View products",
    "products.create": "Create products",
    "products.update": "Edit products",
    "inventory.read": "View inventory",
    "inventory.update": "Adjust inventory",
    "quotes.read": "View quotations",
    "quotes.create": "Create quotations",
    "quotes.approve": "Approve quotations",
    "orders.read": "View orders",
    "orders.create": "Create orders",
    "orders.cancel": "Cancel orders",
    "conversations.read": "View conversations and messages",
    "conversations.manage": "Send messages, assign, and configure AI on conversations",
    "agents.read": "View AI agents",
    "agents.manage": "Manage AI agents",
    "workflows.read": "View workflows",
    "workflows.manage": "Manage workflows",
    "knowledge_base.read": "View knowledge base articles",
    "knowledge_base.manage": "Create and edit knowledge base articles",
    "invoices.read": "View invoices",
    "integrations.read": "View integrations",
    "integrations.manage": "Manage integrations",
    "analytics.read": "View analytics",
    "audit_logs.read": "View audit logs",
    "team.manage": "Manage team members and roles",
}

# ordering matters only for readability; membership is what counts
_ALL = list(PERMISSIONS.keys())
_READ_ONLY = [p for p in _ALL if p.endswith(".read")]

DEFAULT_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "OWNER": _ALL,
    "ADMIN": _ALL,
    "MANAGER": [p for p in _ALL if p != "team.manage"],
    "SALES": [
        "customers.read", "customers.create", "customers.update",
        "products.read", "inventory.read",
        "quotes.read", "quotes.create",
        "orders.read", "orders.create",
        "conversations.read", "conversations.manage",
        "agents.read", "analytics.read", "knowledge_base.read",
    ],
    "SUPPORT": [
        "customers.read", "products.read", "inventory.read",
        "orders.read", "quotes.read", "agents.read",
        "conversations.read", "conversations.manage", "knowledge_base.read",
    ],
    "FINANCE": [
        "customers.read", "orders.read", "quotes.read",
        "quotes.approve", "analytics.read", "audit_logs.read", "invoices.read",
    ],
    "INVENTORY": ["products.read", "products.create", "products.update", "inventory.read", "inventory.update"],
    "VIEWER": _READ_ONLY,
}
