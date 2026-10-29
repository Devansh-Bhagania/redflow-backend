import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.schemas.quotation import QuotationCreate, QuotationOut
from app.services import quotation_service, workflow_service

router = APIRouter(prefix="/quotes", tags=["quotations"])


@router.get("", response_model=list[QuotationOut])
async def list_quotations(
    ctx: TenantContext = Depends(require_permission("quotes.read")), db: AsyncSession = Depends(get_db)
):
    return await quotation_service.list_quotations(db, ctx.organization_id)


@router.post("", response_model=QuotationOut, status_code=201)
async def create_quotation(
    payload: QuotationCreate,
    ctx: TenantContext = Depends(require_permission("quotes.create")),
    db: AsyncSession = Depends(get_db),
):
    return await quotation_service.create_quotation(db, ctx.organization_id, payload, created_by=str(ctx.user.id))


@router.get("/{quotation_id}", response_model=QuotationOut)
async def get_quotation(
    quotation_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("quotes.read")),
    db: AsyncSession = Depends(get_db),
):
    return await quotation_service.get_quotation(db, ctx.organization_id, quotation_id)


@router.post("/{quotation_id}/send", response_model=QuotationOut)
async def send_quotation(
    quotation_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("quotes.create")),
    db: AsyncSession = Depends(get_db),
):
    quotation = await quotation_service.send_quotation(db, ctx.organization_id, quotation_id)
    await workflow_service.trigger_quote_sent_workflow(db, ctx.organization_id, quotation)
    return quotation


@router.post("/{quotation_id}/accept", response_model=QuotationOut)
async def accept_quotation(
    quotation_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("quotes.create")),
    db: AsyncSession = Depends(get_db),
):
    return await quotation_service.accept_quotation(db, ctx.organization_id, quotation_id)
