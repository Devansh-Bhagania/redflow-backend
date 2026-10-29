"""The Sales Agent's tool registry.

Every tool the AI can call is declared here with a name, description, JSON
schema, and risk level. The AI never touches the database or business logic
directly — it can only invoke these, and each one is a thin wrapper around a
domain service that already enforces the business rules (pricing, inventory,
tenant isolation).
"""

TOOLS = [
    {
        "name": "search_products",
        "description": "Search the product catalog by name or SKU. Returns matching products with their variants.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search text, e.g. a product name"}},
            "required": ["query"],
        },
        "risk_level": "SAFE",
    },
    {
        "name": "check_inventory",
        "description": "Check total available stock across all warehouses for a product variant.",
        "input_schema": {
            "type": "object",
            "properties": {"product_variant_id": {"type": "string", "description": "UUID of the product variant"}},
            "required": ["product_variant_id"],
        },
        "risk_level": "SAFE",
    },
    {
        "name": "calculate_price",
        "description": "Calculate this customer's unit price for a product variant at a given quantity, using their price list and volume tiers. Never state a price you haven't gotten from this tool.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_variant_id": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 1},
            },
            "required": ["product_variant_id", "quantity"],
        },
        "risk_level": "SAFE",
    },
    {
        "name": "create_quote",
        "description": "Create and send a quotation to the customer for specific line items. Prices are calculated automatically from the catalog and the customer's pricing tier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_variant_id": {"type": "string"},
                            "quantity": {"type": "integer", "minimum": 1},
                        },
                        "required": ["product_variant_id", "quantity"],
                    },
                }
            },
            "required": ["items"],
        },
        "risk_level": "SAFE",
    },
    {
        "name": "accept_quote",
        "description": "Mark a previously sent quotation as accepted. Only call this once the customer has clearly confirmed they want to go ahead with that specific quote.",
        "input_schema": {
            "type": "object",
            "properties": {"quote_number": {"type": "string"}},
            "required": ["quote_number"],
        },
        "risk_level": "SAFE",
    },
    {
        "name": "create_order",
        "description": "Convert an accepted quotation into a confirmed order and reserve inventory for it.",
        "input_schema": {
            "type": "object",
            "properties": {"quote_number": {"type": "string"}},
            "required": ["quote_number"],
        },
        "risk_level": "SENSITIVE",
    },
    {
        "name": "update_search_context",
        "description": (
            "Records what the customer is currently shopping for so it's remembered across the rest of this "
            "conversation — the category, specific filters (material, size, style, color, ...), which product "
            "they've picked, and the quantity they want. Call this whenever any of that changes: after a search "
            "narrows things down, when the customer names a product (PRODUCT_SELECTION), or states a quantity "
            "(QUANTITY_UPDATE) like '20 pieces'. Only include the fields that are new or changed — they're merged "
            "into what's already tracked, not replacing it. Set clear_filters=true when the customer switches to a "
            "materially different search (e.g. a new category) and old filters like size/material no longer apply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "e.g. 'shirt'. Omit if unchanged."},
                "filters": {
                    "type": "object",
                    "description": "Attribute key/value pairs, e.g. {\"material\": \"cotton\", \"size\": \"S\"}.",
                },
                "clear_filters": {"type": "boolean", "description": "True to drop all previously tracked filters."},
                "selected_product_id": {"type": "string", "description": "Product variant UUID the customer picked."},
                "quantity": {"type": "integer", "minimum": 1},
            },
        },
        "risk_level": "SAFE",
    },
]

GET_ORDER_STATUS = {
    "name": "get_order_status",
    "description": "Look up the status and items of one of this customer's own orders by order number.",
    "input_schema": {
        "type": "object",
        "properties": {"order_number": {"type": "string"}},
        "required": ["order_number"],
    },
    "risk_level": "SAFE",
}

GET_PURCHASE_HISTORY = {
    "name": "get_purchase_history",
    "description": "List this customer's past orders (most recent first), so you can suggest a reorder of items they've bought before.",
    "input_schema": {"type": "object", "properties": {}},
    "risk_level": "SAFE",
}

SEARCH_KNOWLEDGE_BASE = {
    "name": "search_knowledge_base",
    "description": "Search this organization's published knowledge base (policies, FAQs, how-tos) for an answer. Use before saying you don't know.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Search text, e.g. the customer's question"}},
        "required": ["query"],
    },
    "risk_level": "SAFE",
}

LIST_QUOTES_FOR_CUSTOMER = {
    "name": "list_quotes_for_customer",
    "description": "List this customer's quotations (most recent first), including status, to check whether a sent quote still needs a follow-up.",
    "input_schema": {"type": "object", "properties": {}},
    "risk_level": "SAFE",
}

CHECK_LOW_STOCK = {
    "name": "check_low_stock",
    "description": "List product variants whose available stock has dropped to or below their reorder level, across the whole catalog.",
    "input_schema": {"type": "object", "properties": {}},
    "risk_level": "SAFE",
}

GET_OUTSTANDING_BALANCE = {
    "name": "get_outstanding_balance",
    "description": "List this customer's unpaid or partially paid invoices, with amount due and due date.",
    "input_schema": {"type": "object", "properties": {}},
    "risk_level": "SAFE",
}

RECORD_PAYMENT = {
    "name": "record_payment",
    "description": "Record a payment the customer says they've made against one of their invoices. Only call this once the customer has clearly stated an amount and which invoice it's for.",
    "input_schema": {
        "type": "object",
        "properties": {
            "invoice_number": {"type": "string"},
            "amount": {"type": "number", "exclusiveMinimum": 0},
        },
        "required": ["invoice_number", "amount"],
    },
    "risk_level": "SENSITIVE",
}

def compact_tools(tools: list[dict]) -> list[dict]:
    """A leaner copy of `tools` for small local models: first sentence of the
    description only, no per-property descriptions. Keeps name/type/required
    constraints intact — everything the model needs to call the tool
    correctly, none of the prose aimed at a human reader. The original
    TOOLS/*_TOOLS lists (and whatever `tools` was passed in) are untouched."""
    compacted = []
    for tool in tools:
        schema = tool["input_schema"]
        properties = {
            name: {k: v for k, v in prop.items() if k != "description"}
            for name, prop in schema.get("properties", {}).items()
        }
        compacted.append(
            {
                **tool,
                "description": tool["description"].split(". ")[0].rstrip(". ") + ".",
                "input_schema": {**schema, "properties": properties},
            }
        )
    return compacted


_BY_NAME = {t["name"]: t for t in TOOLS} | {
    "get_order_status": GET_ORDER_STATUS,
    "get_purchase_history": GET_PURCHASE_HISTORY,
    "search_knowledge_base": SEARCH_KNOWLEDGE_BASE,
    "list_quotes_for_customer": LIST_QUOTES_FOR_CUSTOMER,
    "check_low_stock": CHECK_LOW_STOCK,
    "get_outstanding_balance": GET_OUTSTANDING_BALANCE,
    "record_payment": RECORD_PAYMENT,
}

# Public alias — a workflow AI_AGENT/TOOL node names tools by string and needs
# to resolve them back to schemas, same lookup _BY_NAME already does
# internally for the fixed preset lists below.
TOOLS_BY_NAME = _BY_NAME

# The Support Agent answers questions about the catalog and this customer's own
# orders — it never quotes or sells.
SUPPORT_TOOLS = [
    _BY_NAME[n] for n in ("search_products", "check_inventory", "get_order_status", "search_knowledge_base")
]

# The Reordering Agent reuses the Sales Agent's quoting tools, plus purchase
# history so it can suggest what the customer bought before.
REORDERING_TOOLS = [
    _BY_NAME[n]
    for n in (
        "get_purchase_history",
        "search_products",
        "check_inventory",
        "calculate_price",
        "create_quote",
        "update_search_context",
    )
]

# The Follow-up Agent picks up when a customer replies to a quiet-quote nudge
# (the Temporal workflow sends the nudge; this agent handles the reply) — it
# checks quote status and can push a confirmed quote through to an order.
FOLLOWUP_TOOLS = [_BY_NAME[n] for n in ("list_quotes_for_customer", "accept_quote", "create_order")]

# The Inventory Agent answers stock/availability questions and can flag what's
# at or below its reorder point.
INVENTORY_TOOLS = [_BY_NAME[n] for n in ("search_products", "check_inventory", "check_low_stock")]

# The Collections Agent checks a customer's outstanding balance and records a
# payment they report making. record_payment is SENSITIVE — it changes money.
COLLECTIONS_TOOLS = [_BY_NAME[n] for n in ("get_outstanding_balance", "record_payment")]

# The Company Agent answers general questions about the business itself
# (hours, policies, shipping/returns, how-tos) from the knowledge base — not
# product/order/account specifics, which belong to the other agents.
COMPANY_TOOLS = [_BY_NAME["search_knowledge_base"]]


# ---------------------------------------------------------------------------
# Workflow Builder
#
# The odd one out: this agent edits a workflow graph rather than serving a
# customer, so its tools take no customer_id and touch no commercial data. It
# cannot write to the stored workflow at all — propose_workflow_definition
# stages a candidate that a human applies (or discards) from the canvas.
# ---------------------------------------------------------------------------

WORKFLOW_BUILDER_TOOLS = [
    {
        "name": "get_workflow_definition",
        "description": (
            "Read the workflow currently being edited: its name, trigger event, and the full node/edge "
            "definition. Call this first, before proposing any change, so your proposal is a modification of "
            "what actually exists rather than a guess."
        ),
        "input_schema": {"type": "object", "properties": {}},
        "risk_level": "SAFE",
    },
    {
        "name": "list_workflow_capabilities",
        "description": (
            "List every node type, action name, condition name, agent type and branch label a workflow "
            "definition may reference. Only these values are valid — a definition naming anything else will be "
            "rejected. Call this before proposing a graph that uses a node type or action you have not already "
            "seen in this workflow."
        ),
        "input_schema": {"type": "object", "properties": {}},
        "risk_level": "SAFE",
    },
    {
        "name": "propose_workflow_definition",
        "description": (
            "Stage a new workflow definition for the user to review. This does NOT save the workflow — the user "
            "applies or discards it from the canvas. The definition is validated and then dry-run in the "
            "sandbox, and this tool returns both results: if validation fails or the dry run does not reach an "
            "END node, fix the definition and call this again rather than telling the user it is ready."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "definition": {
                    "type": "object",
                    "description": "The complete graph: {\"nodes\": [...], \"edges\": [...]}. Always send the whole graph, not a patch.",
                    "properties": {
                        "nodes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "type": {"type": "string"},
                                    "config": {"type": "object"},
                                },
                                "required": ["id", "type"],
                            },
                        },
                        "edges": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "source": {"type": "string"},
                                    "target": {"type": "string"},
                                    "condition": {"type": ["string", "null"]},
                                },
                                "required": ["source", "target"],
                            },
                        },
                    },
                    "required": ["nodes", "edges"],
                },
                "summary": {
                    "type": "string",
                    "description": "One sentence describing what changed, shown to the user next to the diff.",
                },
            },
            "required": ["definition"],
        },
        "risk_level": "SAFE",
    },
    {
        "name": "sandbox_run_workflow",
        "description": (
            "Dry-run a definition and return the step-by-step trace. Side effects are simulated: delays are "
            "skipped, actions and agents are not executed, HTTP calls are not made. Use it to check a graph "
            "reaches an END node and that branches are wired up. Omit `definition` to run the workflow as "
            "currently stored; pass `decisions` as {node_id: branch} to walk a specific path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "definition": {"type": "object", "description": "Optional. Defaults to the stored workflow."},
                "context": {"type": "object", "description": "Optional starting context values."},
                "decisions": {
                    "type": "object",
                    "description": "Optional {node_id: branch} overrides, e.g. {\"cond1\": \"true\"}.",
                },
            },
        },
        "risk_level": "SAFE",
    },
]

# Names that exist in DISPATCH but must never be assignable to a customer- or
# workflow-node-facing AI_AGENT's config.tools: they operate on a
# workflow-pinned builder conversation (see app.agents.tool_executor's
# WORKFLOW_ID_KEY) and would raise "This chat is not attached to a workflow"
# for any other kind of node. schema.py's tool-name validator subtracts this
# set from what it accepts.
WORKFLOW_BUILDER_ONLY_TOOL_NAMES = {t["name"] for t in WORKFLOW_BUILDER_TOOLS}
