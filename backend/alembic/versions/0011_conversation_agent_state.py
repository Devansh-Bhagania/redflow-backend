"""conversations.agent_state: persisted shopping-session state for the Sales/Reordering agents

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-16

"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("agent_state", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("conversations", "agent_state")
