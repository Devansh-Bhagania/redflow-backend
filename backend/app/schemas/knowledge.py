import uuid
from datetime import datetime

from pydantic import BaseModel


class KnowledgeBaseArticleCreate(BaseModel):
    title: str
    content: str
    category: str | None = None
    is_published: bool = True


class KnowledgeBaseArticleUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    category: str | None = None
    is_published: bool | None = None


class KnowledgeBaseArticleOut(BaseModel):
    id: uuid.UUID
    title: str
    content: str
    category: str | None
    is_published: bool
    created_at: datetime

    model_config = {"from_attributes": True}
