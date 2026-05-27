"""invoice_payments — partial payment support (P2-07)

Revision ID: 0008_invoice_payments
Revises: 0007_bank_reconciliation
Create Date: 2026-05-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_invoice_payments"
down_revision: Union[str, Sequence[str], None] = "0007_bank_reconciliation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoice_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_id", sa.Integer(),
                  sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="ILS"),
        sa.Column("paid_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("bank_entry_id", sa.Integer(),
                  sa.ForeignKey("bank_statement_entries.id"), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.UniqueConstraint("invoice_id", "bank_entry_id",
                            name="uq_payment_invoice_bank"),
    )
    op.create_index("ix_invoice_payments_invoice_id",
                    "invoice_payments", ["invoice_id"])
    op.create_index("ix_invoice_payments_bank_entry_id",
                    "invoice_payments", ["bank_entry_id"])


def downgrade() -> None:
    op.drop_index("ix_invoice_payments_bank_entry_id", table_name="invoice_payments")
    op.drop_index("ix_invoice_payments_invoice_id", table_name="invoice_payments")
    op.drop_table("invoice_payments")
