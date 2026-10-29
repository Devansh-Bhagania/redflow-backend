"""agents table; workflows.agent_id; conversations.workflow_execution_id

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-17

"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(2000), nullable=True),
        sa.Column("default_provider", sa.String(50), nullable=True),
        sa.Column("default_model", sa.String(255), nullable=True),
        sa.Column("system_prompt", sa.String(8000), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("channels", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.add_column("workflows", sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_workflows_agent_id", "workflows", ["agent_id"])

    op.add_column(
        "conversations",
        sa.Column("workflow_execution_id", sa.Uuid(), sa.ForeignKey("workflow_executions.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "workflow_execution_id")
    op.drop_index("ix_workflows_agent_id", table_name="workflows")
    op.drop_column("workflows", "agent_id")
    op.drop_table("agents")
