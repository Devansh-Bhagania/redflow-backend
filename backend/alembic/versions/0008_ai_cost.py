"""Billing: ai_executions.estimated_cost

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-16

"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_executions", sa.Column("estimated_cost", sa.Numeric(10, 4), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("ai_executions", "estimated_cost")
