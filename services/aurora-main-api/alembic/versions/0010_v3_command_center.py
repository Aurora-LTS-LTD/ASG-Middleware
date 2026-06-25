"""v3 CEO Command Center — admin/ops/audit/analytics/RBAC schema

Revision ID: 0010_v3_command_center
Revises: 0009_user_must_change_password
Create Date: 2026-06-24

Adds organizations.archived_at + is_pilot and the customer_notes,
admin_audit_events, analytics_events, roles, permissions, role_permissions
tables. Additive only. (Prod applies via app.db_setup/migrate_phase32; this
revision covers Alembic-based environments.)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_v3_command_center"
down_revision: Union[str, Sequence[str], None] = "0009_user_must_change_password"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.add_column("organizations", sa.Column(
        "is_pilot", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    op.create_table(
        "customer_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("next_action", sa.String(), nullable=True),
        sa.Column("is_resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_customer_notes_organization_id", "customer_notes", ["organization_id"])

    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("actor_role", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=True),
        sa.Column("entity_id", sa.String(), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("ip_hash", sa.String(), nullable=True),
        sa.Column("device", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=False, server_default="info"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_admin_audit_events_actor_user_id", "admin_audit_events", ["actor_user_id"])
    op.create_index("ix_admin_audit_events_created_at", "admin_audit_events", ["created_at"])
    op.create_index("ix_admin_audit_entity", "admin_audit_events", ["entity_type", "entity_id"])

    op.create_table(
        "analytics_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("properties_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_analytics_events_event_type", "analytics_events", ["event_type"])
    op.create_index("ix_analytics_events_organization_id", "analytics_events", ["organization_id"])
    op.create_index("ix_analytics_events_created_at", "analytics_events", ["created_at"])

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("module", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.UniqueConstraint("module", "action", name="uq_permission_module_action"),
    )
    op.create_table(
        "role_permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("permission_id", sa.Integer(), sa.ForeignKey("permissions.id"), nullable=False),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
    )


def downgrade() -> None:
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("analytics_events")
    op.drop_table("admin_audit_events")
    op.drop_table("customer_notes")
    op.drop_column("organizations", "is_pilot")
    op.drop_column("organizations", "archived_at")
