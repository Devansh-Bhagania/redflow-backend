import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.search_state import merge_search_state
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.schemas.quotation import QuotationCreate, QuotationItemCreate
from app.workflows.schema import WorkflowDefinition
from app.services import (
    invoice_service,
    inventory_service,
    knowledge_service,
    order_service,
    pricing_service,
    product_service,
    quotation_service,
    workflow_service,
)


def _order_summary(order) -> dict:
    return {
        "order_number": order.order_number,
        "status": order.status,
        "total": str(order.total),
        "items": [
            {"product_variant_id": str(i.product_variant_id), "quantity": i.quantity, "unit_price": str(i.unit_price)}
            for i in order.items
        ],
    }


async def search_products(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    products = await product_service.search_products(db, organization_id, input["query"])
    summaries = [
        {
            "product_id": str(p.id),
            "name": p.name,
            "base_price": str(p.base_price),
            "variants": [{"variant_id": str(v.id), "sku": v.sku, "attributes": v.attributes} for v in p.variants],
        }
        for p in products[:10]
    ]

    # Persisted automatically (not left to the model to report back) so a
    # later PRODUCT_SELECTION turn like "the cotton one" can be resolved
    # against what was actually just shown, even several turns later.
    conversation = await db.get(Conversation, conversation_id)
    conversation.agent_state = merge_search_state(conversation.agent_state, {"search_results": summaries})
    db.add(conversation)
    await db.flush()

    return {"products": summaries}


async def check_inventory(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    total = await inventory_service.get_total_available(db, organization_id, uuid.UUID(input["product_variant_id"]))
    return {"available_quantity": total}


async def calculate_price(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    variant = await product_service.get_variant(db, organization_id, uuid.UUID(input["product_variant_id"]))
    customer = await db.get(Customer, customer_id)
    price = await pricing_service.calculate_customer_price(db, customer, variant, input["quantity"])
    return {"unit_price": str(price), "total": str(price * input["quantity"])}


async def create_quote(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    payload = QuotationCreate(
        customer_id=customer_id,
        items=[
            QuotationItemCreate(product_variant_id=uuid.UUID(i["product_variant_id"]), quantity=i["quantity"])
            for i in input["items"]
        ],
    )
    quotation = await quotation_service.create_quotation(db, organization_id, payload, created_by="AI_SALES_AGENT")
    quotation = await quotation_service.send_quotation(db, organization_id, quotation.id)
    await workflow_service.trigger_quote_sent_workflow(db, organization_id, quotation)
    return {
        "quote_number": quotation.quote_number,
        "total": str(quotation.total),
        "items": [
            {"product_variant_id": str(i.product_variant_id), "quantity": i.quantity, "unit_price": str(i.unit_price)}
            for i in quotation.items
        ],
    }


async def accept_quote(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    quotation = await quotation_service.get_quotation_by_number(db, organization_id, input["quote_number"])
    quotation = await quotation_service.accept_quotation(db, organization_id, quotation.id)
    return {"quote_number": quotation.quote_number, "status": quotation.status}


async def create_order(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    quotation = await quotation_service.get_quotation_by_number(db, organization_id, input["quote_number"])
    order = await order_service.create_order_from_quotation(
        db, organization_id, quotation.id, idempotency_key=f"ai-order-{quotation.quote_number}"
    )
    return {"order_number": order.order_number, "status": order.status, "total": str(order.total)}


async def get_order_status(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    order = await order_service.get_order_by_number_for_customer(db, organization_id, customer_id, input["order_number"])
    return _order_summary(order)


async def get_purchase_history(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    orders = await order_service.list_orders_for_customer(db, organization_id, customer_id)
    return {"orders": [_order_summary(o) for o in orders[:10]]}


async def search_knowledge_base(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    articles = await knowledge_service.search_articles(db, organization_id, input["query"])
    return {
        "articles": [
            {"title": a.title, "category": a.category, "content": a.content} for a in articles[:5]
        ]
    }


async def list_quotes_for_customer(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    quotations = await quotation_service.list_quotations_for_customer(db, organization_id, customer_id)
    return {
        "quotes": [
            {"quote_number": q.quote_number, "status": q.status, "total": str(q.total)} for q in quotations[:10]
        ]
    }


async def check_low_stock(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    return {"low_stock": await inventory_service.list_low_stock(db, organization_id)}


async def get_outstanding_balance(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    invoices = await invoice_service.list_outstanding_for_customer(db, organization_id, customer_id)
    return {
        "invoices": [
            {
                "invoice_number": inv.invoice_number,
                "amount_due": str(inv.amount_due),
                "amount_paid": str(inv.amount_paid),
                "status": inv.status,
                "due_date": inv.due_date.isoformat(),
            }
            for inv in invoices
        ]
    }


async def record_payment(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    invoice = await invoice_service.record_payment(
        db, organization_id, customer_id, input["invoice_number"], Decimal(str(input["amount"]))
    )
    return {"invoice_number": invoice.invoice_number, "status": invoice.status, "amount_paid": str(invoice.amount_paid)}


async def update_search_context(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    conversation = await db.get(Conversation, conversation_id)
    conversation.agent_state = merge_search_state(conversation.agent_state, input)
    db.add(conversation)
    await db.flush()
    return {"state": conversation.agent_state}


# --- Workflow Builder -------------------------------------------------------
#
# These four ignore customer_id: a workflow graph has nothing to do with a
# customer. The workflow being edited is pinned in the conversation's
# agent_state when the builder session is opened (see
# workflow_builder_service), which is also where a staged proposal is parked
# until the user applies it — the agent has no path to the stored workflow.

WORKFLOW_ID_KEY = "workflow_id"
PROPOSAL_KEY = "workflow_proposal"


async def _builder_workflow(db: AsyncSession, organization_id: uuid.UUID, conversation_id: uuid.UUID):
    conversation = await db.get(Conversation, conversation_id)
    workflow_id = (conversation.agent_state or {}).get(WORKFLOW_ID_KEY)
    if not workflow_id:
        raise ValueError("This chat is not attached to a workflow")
    return conversation, await workflow_service.get_workflow(db, organization_id, uuid.UUID(workflow_id))


async def get_workflow_definition(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    _conversation, workflow = await _builder_workflow(db, organization_id, conversation_id)
    return {
        "name": workflow.name,
        "trigger_event": workflow.trigger_event,
        "status": workflow.status,
        "definition": workflow.definition,
    }


async def list_workflow_capabilities(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    return workflow_service.list_capabilities()


async def propose_workflow_definition(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    conversation, _workflow = await _builder_workflow(db, organization_id, conversation_id)
    definition = input.get("definition")
    if not isinstance(definition, dict):
        raise ValueError("propose_workflow_definition needs a `definition` object")

    # Validate before staging. Returning the error to the model instead of
    # raising lets it correct itself on the next iteration, which is the whole
    # reason this tool reports rather than throws.
    try:
        WorkflowDefinition.model_validate(definition)
    except Exception as exc:
        return {"valid": False, "error": str(exc), "staged": False}

    trace = await workflow_service.dry_run_definition(db, organization_id, definition)

    state = dict(conversation.agent_state or {})
    state[PROPOSAL_KEY] = {"definition": definition, "summary": input.get("summary")}
    conversation.agent_state = state
    db.add(conversation)
    await db.flush()

    return {
        "valid": True,
        "staged": True,
        "sandbox_status": trace["status"],
        "sandbox_error": trace["error"],
        "steps": [f"{s['node_type']}: {s['detail']}" for s in trace["steps"]],
        "note": "Staged for the user to review. It is not saved until they apply it.",
    }


async def sandbox_run_workflow(db: AsyncSession, organization_id: uuid.UUID, customer_id: uuid.UUID, conversation_id: uuid.UUID, input: dict) -> dict:
    definition = input.get("definition")
    if not isinstance(definition, dict):
        _conversation, workflow = await _builder_workflow(db, organization_id, conversation_id)
        definition = workflow.definition

    trace = await workflow_service.dry_run_definition(
        db, organization_id, definition,
        context=input.get("context") or {},
        decisions=input.get("decisions") or {},
    )
    return {
        "status": trace["status"],
        "error": trace["error"],
        "simulated_seconds": trace["simulated_seconds"],
        "steps": [f"{s['node_type']}: {s['detail']}" for s in trace["steps"]],
    }


DISPATCH = {
    "search_products": search_products,
    "check_inventory": check_inventory,
    "calculate_price": calculate_price,
    "create_quote": create_quote,
    "accept_quote": accept_quote,
    "create_order": create_order,
    "get_order_status": get_order_status,
    "get_purchase_history": get_purchase_history,
    "search_knowledge_base": search_knowledge_base,
    "list_quotes_for_customer": list_quotes_for_customer,
    "check_low_stock": check_low_stock,
    "get_outstanding_balance": get_outstanding_balance,
    "record_payment": record_payment,
    "update_search_context": update_search_context,
    "get_workflow_definition": get_workflow_definition,
    "list_workflow_capabilities": list_workflow_capabilities,
    "propose_workflow_definition": propose_workflow_definition,
    "sandbox_run_workflow": sandbox_run_workflow,
}
