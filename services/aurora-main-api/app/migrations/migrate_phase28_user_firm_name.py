"""Aurora LTS — Phase 28: users.firm_name column.

Editable accountant firm/practice name (portal Settings → Profile). Idempotent;
safe on every boot. Mirrors the ADD COLUMN pattern of migrate_phase26.
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

    if not _has_column("users", "firm_name"):
        with engine.begin() as conn:
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN firm_name VARCHAR"))
                log.info("[phase28] added users.firm_name")
            except Exception as e:
                log.warning("[phase28] could not add users.firm_name: %s", e)
    log.info("[phase28] users.firm_name done")
