import logging
import math
import uuid

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.knowledge import KnowledgeBaseArticle
from app.schemas.knowledge import KnowledgeBaseArticleCreate, KnowledgeBaseArticleUpdate

logger = logging.getLogger(__name__)

# Below this, a "semantic match" is no more meaningful than noise — fall back
# to treating the article as unmatched rather than returning it.
SIMILARITY_THRESHOLD = 0.5
MAX_RESULTS = 5


async def _embed(text: str) -> list[float] | None:
    """Embeds `text` via the OpenAI-compatible /embeddings endpoint on the
    same server ai_base_url already points at (e.g. LM Studio with an
    embedding model loaded). Returns None — triggering the ILIKE fallback in
    search_articles — when no embedding model is configured or the call
    fails, so a misconfigured/unreachable embeddings server never breaks
    knowledge-base search, only degrades it to keyword matching."""
    if not settings.ai_embedding_model:
        return None
    try:
        async with httpx.AsyncClient(base_url=settings.ai_base_url, timeout=30) as http:
            resp = await http.post(
                "/embeddings",
                json={"model": settings.ai_embedding_model, "input": text},
                headers={"Authorization": f"Bearer {settings.ai_api_key or 'not-needed'}"},
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
    except Exception:
        logger.exception("Embedding call failed, falling back to keyword search")
        return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


async def _article_embedding(article: KnowledgeBaseArticle) -> list[float] | None:
    if article.embedding is None:
        article.embedding = await _embed(f"{article.title}\n{article.content}")
    return article.embedding


async def create_article(
    db: AsyncSession, organization_id: uuid.UUID, payload: KnowledgeBaseArticleCreate
) -> KnowledgeBaseArticle:
    article = KnowledgeBaseArticle(organization_id=organization_id, **payload.model_dump())
    article.embedding = await _embed(f"{article.title}\n{article.content}")
    db.add(article)
    await db.commit()
    await db.refresh(article)
    return article


async def list_articles(db: AsyncSession, organization_id: uuid.UUID) -> list[KnowledgeBaseArticle]:
    result = await db.execute(
        select(KnowledgeBaseArticle).where(KnowledgeBaseArticle.organization_id == organization_id)
    )
    return list(result.scalars().all())


async def get_article(db: AsyncSession, organization_id: uuid.UUID, article_id: uuid.UUID) -> KnowledgeBaseArticle:
    result = await db.execute(
        select(KnowledgeBaseArticle).where(
            KnowledgeBaseArticle.organization_id == organization_id, KnowledgeBaseArticle.id == article_id
        )
    )
    article = result.scalar_one_or_none()
    if not article:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found")
    return article


async def update_article(
    db: AsyncSession, article: KnowledgeBaseArticle, payload: KnowledgeBaseArticleUpdate
) -> KnowledgeBaseArticle:
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(article, field, value)
    if "title" in changes or "content" in changes:
        article.embedding = await _embed(f"{article.title}\n{article.content}")
    db.add(article)
    await db.commit()
    await db.refresh(article)
    return article


async def search_articles(db: AsyncSession, organization_id: uuid.UUID, query: str) -> list[KnowledgeBaseArticle]:
    """Semantic search over published articles when an embedding model is
    configured (ai_embedding_model); otherwise plain ILIKE keyword search."""
    query_embedding = await _embed(query)
    if query_embedding is None:
        result = await db.execute(
            select(KnowledgeBaseArticle).where(
                KnowledgeBaseArticle.organization_id == organization_id,
                KnowledgeBaseArticle.is_published.is_(True),
                (KnowledgeBaseArticle.title.ilike(f"%{query}%") | KnowledgeBaseArticle.content.ilike(f"%{query}%")),
            )
        )
        return list(result.scalars().all())

    result = await db.execute(
        select(KnowledgeBaseArticle).where(
            KnowledgeBaseArticle.organization_id == organization_id,
            KnowledgeBaseArticle.is_published.is_(True),
        )
    )
    articles = list(result.scalars().all())

    scored = []
    for article in articles:
        embedding = await _article_embedding(article)  # lazy backfill for pre-existing rows
        if embedding is not None:
            scored.append((article, _cosine_similarity(query_embedding, embedding)))
    await db.commit()

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [article for article, score in scored[:MAX_RESULTS] if score >= SIMILARITY_THRESHOLD]
