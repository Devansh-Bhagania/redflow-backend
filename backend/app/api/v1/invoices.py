import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.schemas.invoice import InvoiceOut
from app.services import invoice_service

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(
    ctx: TenantContext = Depends(require_permission("invoices.read")), db: AsyncSession = Depends(get_db)
):
    return await invoice_service.list_invoices(db, ctx.organization_id)


@router.get("/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(
    invoice_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("invoices.read")),
    db: AsyncSession = Depends(get_db),
):
    return await invoice_service.get_invoice(db, ctx.organization_id, invoice_id)
