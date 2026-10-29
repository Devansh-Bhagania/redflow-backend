from fastapi import APIRouter

from app.api.v1.agent_configs import router as agent_configs_router
from app.api.v1.agents import router as agents_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.auth import router as auth_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.customers import router as customers_router
from app.api.v1.integrations import router as integrations_router
from app.api.v1.inventory import router as inventory_router
from app.api.v1.invoices import router as invoices_router
from app.api.v1.knowledge_base import router as knowledge_base_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.orders import router as orders_router
from app.api.v1.organizations import router as organizations_router
from app.api.v1.products import router as products_router
from app.api.v1.quotations import router as quotations_router
from app.api.v1.webhooks import router as webhooks_router
from app.api.v1.workflows import router as workflows_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(organizations_router)
api_router.include_router(customers_router)
api_router.include_router(products_router)
api_router.include_router(inventory_router)
api_router.include_router(quotations_router)
api_router.include_router(orders_router)
api_router.include_router(conversations_router)
api_router.include_router(integrations_router)
api_router.include_router(webhooks_router)
api_router.include_router(workflows_router)
api_router.include_router(notifications_router)
api_router.include_router(knowledge_base_router)
api_router.include_router(invoices_router)
api_router.include_router(agents_router)
api_router.include_router(agent_configs_router)
api_router.include_router(analytics_router)
