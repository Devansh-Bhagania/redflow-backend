"""AI: ai_executions, ai_tool_calls

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-16

"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_type", sa.String(30), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id"), nullable=True),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="RUNNING"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ai_executions_org", "ai_executions", ["organization_id"])
    op.create_index("ix_ai_executions_conversation", "ai_executions", ["conversation_id"])

    op.create_table(
        "ai_tool_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("execution_id", sa.Uuid(), sa.ForeignKey("ai_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("result_summary", sa.String(2000), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="SUCCESS"),
        sa.Column("risk_level", sa.String(20), nullable=False, server_default="SAFE"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ai_tool_calls_execution", "ai_tool_calls", ["execution_id"])


def downgrade() -> None:
    op.drop_table("ai_tool_calls")
    op.drop_table("ai_executions")
