"""Aurora LTS — Phase 31: users.must_change_password column.

Forces a password rotation when an account was provisioned with a temporary /
bootstrap password (CEO Dashboard banking-auth flow). Boolean, NOT NULL, default
FALSE so existing rows backfill cleanly. Idempotent; safe on every boot. Mirrors
the ADD COLUMN pattern of migrate_phase28 (users.firm_name).

This is the PRODUCTION migration path — prod applies schema via app.db_setup
(create_tables + these phase modules), NOT Alembic. The Alembic revision
(alembic/versions/0009_user_must_change_password.py) covers Alembic-based
environments; this phase covers the live Cloud Run path.
"""
from __future__ import annotations
import logging
from sqlalchemy import inspect, text
from aurora_shared.database.connection import engine, get_engine

log = logging.getLogger(__name__)


def run() -> None:
    inspector = inspect(get_engine())  # real Engine — inspect() can't introspect the _LazyEngine proxy

    def _has_column(table: str, col: str) -> bool:
        try:
            return col in [c["name"] for c in inspector.get_columns(table)]
        except Exception:
            return True  # assume present if we can't check

    if not _has_column("users", "must_change_password"):
        with engine.begin() as conn:
            try:
                # NOT NULL + DEFAULT FALSE backfills existing rows in one shot
                # (Postgres and SQLite both accept this form).
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN must_change_password "
                    "BOOLEAN NOT NULL DEFAULT FALSE"
                ))
                log.info("[phase31] added users.must_change_password")
            except Exception as e:
                log.warning("[phase31] could not add users.must_change_password: %s", e)
    log.info("[phase31] users.must_change_password done")
