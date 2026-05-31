"""baseline — current schema as of P1-02 adoption

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-27

This is the Alembic baseline for Aurora LTS. It represents the schema
state produced by the legacy migrate_phase4..migrate_phase21 + Phase 21
Vault sequence as of the P1-02 cutover.

upgrade() and downgrade() are intentionally empty: when this revision
is first encountered, the bootstrap helper STAMPS the database to this
revision without running any DDL — the current schema already matches.

All FUTURE schema changes get their own Alembic revision and run via
`alembic upgrade head`. Old migrate_phase*.py files remain frozen and
continue to no-op idempotently under the P1-01 advisory lock.
"""
from typing import Sequence, Union

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intentionally empty — see module docstring.
    pass


def downgrade() -> None:
    # Pre-Alembic schema cannot be reconstructed by Alembic.
    # Downgrade past baseline is unsupported.
    raise RuntimeError(
        "Cannot downgrade past baseline. Pre-Alembic schema is managed "
        "by the frozen migrate_phase*.py files."
    )
