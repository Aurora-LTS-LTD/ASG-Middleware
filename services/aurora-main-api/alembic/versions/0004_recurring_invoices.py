"""recurring_invoice_schedules table (P2-01)

Revision ID: 0004_recurring_invoices
Revises: 0003_api_keys
Create Date: 2026-05-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_recurring_invoices"
down_revision: Union[str, Sequence[str], None] = "0003_api_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recurring_invoice_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.Integer(), sa.ForeignKey("businesses.id"),
                  nullable=False),
        sa.Column("beneficiary_name", sa.String(200), nullable=False),
        sa.Column("beneficiary_tax_id", sa.String(32), nullable=True),
        sa.Column("beneficiary_contact", sa.String(255), nullable=True),
        sa.Column("amount_net", sa.Float(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("cadence", sa.String(16), nullable=False),
        sa.Column("next_due_at", sa.DateTime(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"),
                  nullable=True),
    )
    op.create_index(
        "ix_recurring_invoice_schedules_business_id",
        "recurring_invoice_schedules", ["business_id"],
    )
    op.create_index(
        "ix_recurring_invoice_schedules_next_due_at",
        "recurring_invoice_schedules", ["next_due_at"],
    )
    op.create_index(
        "ix_recurring_invoice_schedules_active",
        "recurring_invoice_schedules", ["active"],
    )
    op.create_index(
        "ix_recurring_due_active",
        "recurring_invoice_schedules", ["next_due_at", "active"],
    )


def downgrade() -> None:
    op.drop_index("ix_recurring_due_active", table_name="recurring_invoice_schedules")
    op.drop_index("ix_recurring_invoice_schedules_active", table_name="recurring_invoice_schedules")
    op.drop_index("ix_recurring_invoice_schedules_next_due_at", table_name="recurring_invoice_schedules")
    op.drop_index("ix_recurring_invoice_schedules_business_id", table_name="recurring_invoice_schedules")
    op.drop_table("recurring_invoice_schedules")
