from sqlalchemy import Boolean, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrgScopedMixin, TimestampMixin, UUIDPKMixin


class KnowledgeBaseArticle(Base, UUIDPKMixin, OrgScopedMixin, TimestampMixin):
    __tablename__ = "knowledge_base_articles"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Embedding of title+content for semantic search (see knowledge_service).
    # A plain JSON float array, not pgvector — brute-force cosine similarity
    # over it is fine at this KB's scale and keeps SQLite test compatibility.
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
