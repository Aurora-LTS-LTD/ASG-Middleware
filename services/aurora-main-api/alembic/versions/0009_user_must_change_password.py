"""user.must_change_password — forced temp-password rotation (CEO Dashboard)

Revision ID: 0009_user_must_change_password
Revises: 0008_invoice_payments
Create Date: 2026-06-24

Adds a single additive boolean to the shared `users` table. When True, the
user authenticated with a temporary/bootstrap password and must rotate it via
POST /api/v1/auth/change-password before the native shell will enrol a device
or mint a session. `server_default=false` backfills every existing row at the
DB level (no rewrite-blocking NULL scan), so this is safe on a populated table.

This column is read ONLY by aurora-main-api auth. aurora-api-core (M2) shares
the SQLAlchemy model but has no Alembic chain of its own and never reads this
column, so this single migration owns the shared schema safely.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_user_must_change_password"
down_revision: Union[str, Sequence[str], None] = "0008_invoice_payments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
