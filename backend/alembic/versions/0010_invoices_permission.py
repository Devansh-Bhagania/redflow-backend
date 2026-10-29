"""invoices.read permission (staff-facing invoice list)

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-16

"""
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_READ_ROLES = ("OWNER", "ADMIN", "MANAGER", "FINANCE", "VIEWER")


def upgrade() -> None:
    conn = op.get_bind()
    permissions_table = sa.table(
        "permissions", sa.column("id", sa.Uuid()), sa.column("code", sa.String()), sa.column("description", sa.String())
    )
    permission_id = uuid.uuid4()
    conn.execute(permissions_table.insert(), [{"id": permission_id, "code": "invoices.read", "description": "View invoices"}])

    roles_table = sa.table("roles", sa.column("id", sa.Uuid()), sa.column("name", sa.String()))
    role_ids = dict(conn.execute(sa.select(roles_table.c.name, roles_table.c.id)).fetchall())

    role_perm_table = sa.table("role_permissions", sa.column("role_id", sa.Uuid()), sa.column("permission_id", sa.Uuid()))
    rows = [
        {"role_id": role_ids[role_name], "permission_id": permission_id}
        for role_name in _READ_ROLES
        if role_name in role_ids
    ]
    conn.execute(role_perm_table.insert(), rows)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'invoices.read')"))
    conn.execute(sa.text("DELETE FROM permissions WHERE code = 'invoices.read'"))
