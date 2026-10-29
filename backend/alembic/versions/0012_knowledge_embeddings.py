"""knowledge_base_articles.embedding: title+content vector for semantic search

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-16

"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_base_articles", sa.Column("embedding", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_base_articles", "embedding")
