"""ai_executions: workflow/node observability columns

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-17

"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_executions", sa.Column("provider", sa.String(50), nullable=True))
    op.add_column("ai_executions", sa.Column("workflow_id", sa.Uuid(), sa.ForeignKey("workflows.id"), nullable=True))
    op.add_column("ai_executions", sa.Column("workflow_version_id", sa.Uuid(), sa.ForeignKey("workflow_versions.id"), nullable=True))
    op.add_column("ai_executions", sa.Column("workflow_execution_id", sa.Uuid(), sa.ForeignKey("workflow_executions.id"), nullable=True))
    op.add_column("ai_executions", sa.Column("node_id", sa.String(100), nullable=True))
    op.add_column("ai_executions", sa.Column("finish_reason", sa.String(30), nullable=True))
    op.add_column("ai_executions", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ai_executions", sa.Column("tools_available", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for col in ("tools_available", "retry_count", "finish_reason", "node_id", "workflow_execution_id", "workflow_version_id", "workflow_id", "provider"):
        op.drop_column("ai_executions", col)
