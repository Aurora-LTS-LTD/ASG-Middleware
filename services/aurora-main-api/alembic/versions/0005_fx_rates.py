"""fx_rates — Bank of Israel daily FX cache (P2-02)

Revision ID: 0005_fx_rates
Revises: 0004_recurring_invoices
Create Date: 2026-05-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_fx_rates"
down_revision: Union[str, Sequence[str], None] = "0004_recurring_invoices"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate_to_ils", sa.Float(), nullable=False),
        sa.Column("observed_date", sa.DateTime(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="boi"),
        sa.UniqueConstraint("currency", "observed_date", name="uq_fx_currency_date"),
    )
    op.create_index("ix_fx_rates_currency", "fx_rates", ["currency"])
    op.create_index("ix_fx_rates_observed_date", "fx_rates", ["observed_date"])
    op.create_index("ix_fx_currency_date", "fx_rates", ["currency", "observed_date"])


def downgrade() -> None:
    op.drop_index("ix_fx_currency_date", table_name="fx_rates")
    op.drop_index("ix_fx_rates_observed_date", table_name="fx_rates")
    op.drop_index("ix_fx_rates_currency", table_name="fx_rates")
    op.drop_table("fx_rates")
