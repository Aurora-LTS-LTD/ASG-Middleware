"""support tickets — v3.1 helpdesk

Revision ID: 0011_support_tickets
Revises: 0010_v3_command_center
Create Date: 2026-06-25

Adds tickets + ticket_messages. Additive only. (Prod applies via
app.db_setup/migrate_phase33; this covers Alembic environments.)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_support_tickets"
down_revision: Union[str, Sequence[str], None] = "0010_v3_command_center"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(), nullable=False, server_default="normal"),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("assigned_to_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_tickets_organization_id", "tickets", ["organization_id"])
    op.create_index("ix_tickets_status", "tickets", ["status"])
    op.create_index("ix_tickets_created_at", "tickets", ["created_at"])

    op.create_table(
        "ticket_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ticket_messages_ticket_id", "ticket_messages", ["ticket_id"])


def downgrade() -> None:
    op.drop_table("ticket_messages")
    op.drop_table("tickets")
