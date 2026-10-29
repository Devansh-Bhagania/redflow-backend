"""Additional AI agents: conversations.agent_type

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-16

"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("agent_type", sa.String(20), nullable=False, server_default="SALES"))


def downgrade() -> None:
    op.drop_column("conversations", "agent_type")
