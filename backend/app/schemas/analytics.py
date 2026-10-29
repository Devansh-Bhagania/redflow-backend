from decimal import Decimal

from pydantic import BaseModel


class AnalyticsSummary(BaseModel):
    customers_total: int
    products_total: int
    orders_total: int
    revenue_total: Decimal
    quotes_sent: int
    quotes_accepted: int
    quote_conversion_rate: float
    ai_executions_total: int
    ai_tokens_total: int
    ai_cost_total: Decimal
