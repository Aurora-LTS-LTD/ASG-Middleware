"""invoices.kind + original_invoice_id for credit notes (P2-05)

Revision ID: 0006_credit_notes
Revises: 0005_fx_rates
Create Date: 2026-05-27

Adds two columns to invoices so credit notes (חשבונית זיכוי) can be
expressed as invoices that REFERENCE an original:
  kind                 'standard' | 'credit_note'
  original_invoice_id  FK to invoices.id (null for standard)

A credit note carries NEGATIVE amount_net/vat/total — the existing
ITA allocation, VAT report, and PDF flows handle it transparently.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_credit_notes"
down_revision: Union[str, Sequence[str], None] = "0005_fx_rates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE invoices "
            "ADD COLUMN IF NOT EXISTS kind VARCHAR(16) NOT NULL DEFAULT 'standard'"
        )
        op.execute(
            "ALTER TABLE invoices "
            "ADD COLUMN IF NOT EXISTS original_invoice_id INTEGER "
            "REFERENCES invoices(id)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_invoices_kind ON invoices(kind)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_invoices_original_invoice_id "
            "ON invoices(original_invoice_id)"
        )
    else:
        # SQLite doesn't support ADD COLUMN IF NOT EXISTS in older versions.
        # Use SA reflection to gate the alter so re-runs don't crash.
        insp = sa.inspect(bind)
        existing = {c["name"] for c in insp.get_columns("invoices")}
        with op.batch_alter_table("invoices") as batch:
            if "kind" not in existing:
                batch.add_column(sa.Column(
                    "kind", sa.String(16), nullable=False,
                    server_default="standard",
                ))
            if "original_invoice_id" not in existing:
                batch.add_column(sa.Column(
                    "original_invoice_id", sa.Integer(),
                    sa.ForeignKey("invoices.id"), nullable=True,
                ))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_invoices_original_invoice_id")
        op.execute("DROP INDEX IF EXISTS ix_invoices_kind")
        op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS original_invoice_id")
        op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS kind")
    else:
        with op.batch_alter_table("invoices") as batch:
            batch.drop_column("original_invoice_id")
            batch.drop_column("kind")
