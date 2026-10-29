import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.schemas.knowledge import KnowledgeBaseArticleCreate, KnowledgeBaseArticleOut, KnowledgeBaseArticleUpdate
from app.services import knowledge_service

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


@router.get("", response_model=list[KnowledgeBaseArticleOut])
async def list_articles(
    ctx: TenantContext = Depends(require_permission("knowledge_base.read")), db: AsyncSession = Depends(get_db)
):
    return await knowledge_service.list_articles(db, ctx.organization_id)


@router.post("", response_model=KnowledgeBaseArticleOut, status_code=201)
async def create_article(
    payload: KnowledgeBaseArticleCreate,
    ctx: TenantContext = Depends(require_permission("knowledge_base.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await knowledge_service.create_article(db, ctx.organization_id, payload)


@router.get("/{article_id}", response_model=KnowledgeBaseArticleOut)
async def get_article(
    article_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("knowledge_base.read")),
    db: AsyncSession = Depends(get_db),
):
    return await knowledge_service.get_article(db, ctx.organization_id, article_id)


@router.patch("/{article_id}", response_model=KnowledgeBaseArticleOut)
async def update_article(
    article_id: uuid.UUID,
    payload: KnowledgeBaseArticleUpdate,
    ctx: TenantContext = Depends(require_permission("knowledge_base.manage")),
    db: AsyncSession = Depends(get_db),
):
    article = await knowledge_service.get_article(db, ctx.organization_id, article_id)
    return await knowledge_service.update_article(db, article, payload)
