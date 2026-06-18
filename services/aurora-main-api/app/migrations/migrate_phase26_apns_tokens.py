"""Aurora LTS — Phase 26 APNs Token columns  (P2-25)"""
from __future__ import annotations
import logging
from sqlalchemy import inspect, text
from aurora_shared.database.connection import engine, get_engine

log = logging.getLogger(__name__)


def run() -> None:
    inspector = inspect(get_engine())  # real Engine — inspect() can't introspect the _LazyEngine proxy

    def _has_column(table: str, col: str) -> bool:
        try:
            cols = [c["name"] for c in inspector.get_columns(table)]
            return col in cols
        except Exception:
            return True  # assume present if we can't check

    with engine.begin() as conn:
        for table in ("native_device_keys", "accountant_devices"):
            if not _has_column(table, "apns_device_token"):
                try:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN apns_device_token VARCHAR(200)"
                    ))
                    log.info("[phase26] added apns_device_token to %s", table)
                except Exception as e:
                    log.warning("[phase26] could not add apns_device_token to %s: %s", table, e)

    log.info("[phase26] apns token columns done")
