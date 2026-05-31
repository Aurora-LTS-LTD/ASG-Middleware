"""bank_statement_entries — reconciliation (P2-06)

Revision ID: 0007_bank_reconciliation
Revises: 0006_credit_notes
Create Date: 2026-05-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_bank_reconciliation"
down_revision: Union[str, Sequence[str], None] = "0006_credit_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bank_statement_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.Integer(),
                  sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("posted_at", sa.DateTime(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="ILS"),
        sa.Column("counterparty_name", sa.String(255), nullable=True),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("source_bank", sa.String(40), nullable=True),
        sa.Column("external_id", sa.String(120), nullable=True),
        sa.Column("match_status", sa.String(16), nullable=False,
                  server_default="unmatched"),
        sa.Column("matched_invoice_id", sa.Integer(),
                  sa.ForeignKey("invoices.id"), nullable=True),
        sa.Column("match_confidence", sa.Float(), nullable=True),
        sa.Column("match_reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("matched_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("business_id", "external_id", name="uq_bse_biz_extid"),
    )
    op.create_index("ix_bse_business_id", "bank_statement_entries", ["business_id"])
    op.create_index("ix_bse_posted_at", "bank_statement_entries", ["posted_at"])
    op.create_index("ix_bse_external_id", "bank_statement_entries", ["external_id"])
    op.create_index("ix_bse_match_status", "bank_statement_entries", ["match_status"])
    op.create_index("ix_bse_matched_invoice_id",
                    "bank_statement_entries", ["matched_invoice_id"])
    op.create_index("ix_bse_business_status_date",
                    "bank_statement_entries",
                    ["business_id", "match_status", "posted_at"])


def downgrade() -> None:
    op.drop_index("ix_bse_business_status_date", table_name="bank_statement_entries")
    op.drop_index("ix_bse_matched_invoice_id", table_name="bank_statement_entries")
    op.drop_index("ix_bse_match_status", table_name="bank_statement_entries")
    op.drop_index("ix_bse_external_id", table_name="bank_statement_entries")
    op.drop_index("ix_bse_posted_at", table_name="bank_statement_entries")
    op.drop_index("ix_bse_business_id", table_name="bank_statement_entries")
    op.drop_table("bank_statement_entries")
