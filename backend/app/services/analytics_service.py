import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_execution import AiExecution
from app.models.customer import Customer
from app.models.order import Order
from app.models.product import Product
from app.models.quotation import Quotation
from app.schemas.analytics import AnalyticsSummary


async def get_summary(db: AsyncSession, organization_id: uuid.UUID) -> AnalyticsSummary:
    customers_total = await db.scalar(
        select(func.count()).select_from(Customer).where(Customer.organization_id == organization_id)
    )
    products_total = await db.scalar(
        select(func.count()).select_from(Product).where(Product.organization_id == organization_id)
    )
    orders_total = await db.scalar(
        select(func.count()).select_from(Order).where(Order.organization_id == organization_id)
    )
    revenue_total = await db.scalar(
        select(func.coalesce(func.sum(Order.total), 0)).where(
            Order.organization_id == organization_id, Order.status != "CANCELLED"
        )
    )
    quotes_sent = await db.scalar(
        select(func.count())
        .select_from(Quotation)
        .where(Quotation.organization_id == organization_id, Quotation.status != "DRAFT")
    )
    quotes_accepted = await db.scalar(
        select(func.count())
        .select_from(Quotation)
        .where(Quotation.organization_id == organization_id, Quotation.status.in_(("ACCEPTED", "CONVERTED")))
    )
    ai_executions_total = await db.scalar(
        select(func.count()).select_from(AiExecution).where(AiExecution.organization_id == organization_id)
    )
    ai_tokens_total = await db.scalar(
        select(func.coalesce(func.sum(AiExecution.input_tokens + AiExecution.output_tokens), 0)).where(
            AiExecution.organization_id == organization_id
        )
    )
    ai_cost_total = await db.scalar(
        select(func.coalesce(func.sum(AiExecution.estimated_cost), 0)).where(
            AiExecution.organization_id == organization_id
        )
    )

    return AnalyticsSummary(
        customers_total=customers_total,
        products_total=products_total,
        orders_total=orders_total,
        revenue_total=Decimal(revenue_total),
        quotes_sent=quotes_sent,
        quotes_accepted=quotes_accepted,
        quote_conversion_rate=(quotes_accepted / quotes_sent) if quotes_sent else 0.0,
        ai_executions_total=ai_executions_total,
        ai_tokens_total=ai_tokens_total,
        ai_cost_total=Decimal(ai_cost_total),
    )
