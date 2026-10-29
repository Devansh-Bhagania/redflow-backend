from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.config import settings
from app.schemas.knowledge import KnowledgeBaseArticleCreate
from app.services import knowledge_service

def test_cosine_similarity_identical_vectors_is_one():
    assert knowledge_service._cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero():
    assert knowledge_service._cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector_does_not_divide_by_zero():
    assert knowledge_service._cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def _embedding_for(text: str) -> list[float]:
    # Deterministic stand-in for a real embedding model: "shipping"-flavored
    # text points along one axis, everything else along the other.
    return [1.0, 0.0] if "ship" in text.lower() else [0.0, 1.0]


async def _fake_embeddings_post(url, json, headers):
    return httpx.Response(
        200,
        json={"data": [{"embedding": _embedding_for(json["input"])}]},
        request=httpx.Request("POST", "http://localhost:1234/v1" + url),
    )


@pytest.mark.asyncio
async def test_search_ranks_by_semantic_similarity_when_embedding_model_set(client, db_session):
    # Registration goes through the real (unpatched) ASGI test client — only
    # the knowledge_service calls below should hit the fake embeddings server,
    # since httpx.AsyncClient.post is also what the ASGI test client itself
    # uses to talk to the app.
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Owner", "email": "owner@semantic.example", "password": "supersecret123",
            "organization_name": "Semantic Co",
        },
    )
    from sqlalchemy import select

    from app.models.organization import Organization

    org = (await db_session.execute(select(Organization))).scalars().first()

    with patch.object(settings, "ai_embedding_model", "test-embed-model"), patch(
        "httpx.AsyncClient.post", new=AsyncMock(side_effect=_fake_embeddings_post)
    ):
        await knowledge_service.create_article(
            db_session, org.id,
            KnowledgeBaseArticleCreate(title="Shipping times", content="We ship within 3 business days."),
        )
        await knowledge_service.create_article(
            db_session, org.id,
            KnowledgeBaseArticleCreate(title="Return policy", content="Returns accepted within 30 days."),
        )

        results = await knowledge_service.search_articles(db_session, org.id, "how fast do you ship?")

    assert [a.title for a in results] == ["Shipping times"]


@pytest.mark.asyncio
async def test_embedding_call_failure_falls_back_to_none(monkeypatch):
    monkeypatch.setattr(settings, "ai_embedding_model", "test-embed-model")
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.ConnectError("down"))):
        assert await knowledge_service._embed("anything") is None


@pytest.mark.asyncio
async def test_embed_returns_none_when_no_model_configured(monkeypatch):
    monkeypatch.setattr(settings, "ai_embedding_model", "")
    assert await knowledge_service._embed("anything") is None
