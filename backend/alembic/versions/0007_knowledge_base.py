"""Knowledge base: knowledge_base_articles, knowledge_base.* permissions

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-16

"""
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_NEW_PERMISSIONS = {
    "knowledge_base.read": "View knowledge base articles",
    "knowledge_base.manage": "Create and edit knowledge base articles",
}
# Roles that get knowledge_base.read; OWNER/ADMIN/MANAGER already get every
# permission at runtime lookup time via app.core.permissions, but the grants
# for existing DBs were snapshotted by migration 0001 and need seeding here too.
_READ_ROLES = ("OWNER", "ADMIN", "MANAGER", "SALES", "SUPPORT", "VIEWER")
_MANAGE_ROLES = ("OWNER", "ADMIN", "MANAGER")


def upgrade() -> None:
    op.create_table(
        "knowledge_base_articles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_knowledge_base_articles_org", "knowledge_base_articles", ["organization_id"])

    conn = op.get_bind()
    permissions_table = sa.table(
        "permissions", sa.column("id", sa.Uuid()), sa.column("code", sa.String()), sa.column("description", sa.String())
    )
    permission_ids = {code: uuid.uuid4() for code in _NEW_PERMISSIONS}
    conn.execute(
        permissions_table.insert(),
        [{"id": pid, "code": code, "description": desc} for (code, desc), pid in zip(_NEW_PERMISSIONS.items(), permission_ids.values())],
    )

    roles_table = sa.table("roles", sa.column("id", sa.Uuid()), sa.column("name", sa.String()))
    role_ids = dict(conn.execute(sa.select(roles_table.c.name, roles_table.c.id)).fetchall())

    role_perm_table = sa.table("role_permissions", sa.column("role_id", sa.Uuid()), sa.column("permission_id", sa.Uuid()))
    rows = [
        {"role_id": role_ids[role_name], "permission_id": permission_ids["knowledge_base.read"]}
        for role_name in _READ_ROLES
        if role_name in role_ids
    ] + [
        {"role_id": role_ids[role_name], "permission_id": permission_ids["knowledge_base.manage"]}
        for role_name in _MANAGE_ROLES
        if role_name in role_ids
    ]
    conn.execute(role_perm_table.insert(), rows)


def downgrade() -> None:
    op.drop_table("knowledge_base_articles")
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('knowledge_base.read', 'knowledge_base.manage'))"))
    conn.execute(sa.text("DELETE FROM permissions WHERE code IN ('knowledge_base.read', 'knowledge_base.manage')"))
