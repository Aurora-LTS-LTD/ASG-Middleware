"""Aurora LTS — Phase 30: audit_export_cursor table + seed rows.

The BigQuery export tracks per-table progress in audit_export_cursor. The
exporter auto-creates a cursor row at runtime, but this migration guarantees
the table exists and is seeded deterministically (action_logs + ita_audit_log)
rather than relying on first-run timing. Idempotent; safe on every boot.
"""
from __future__ import annotations
import logging
from sqlalchemy import inspect, text
from aurora_shared.database.connection import get_engine

log = logging.getLogger(__name__)


def run() -> None:
    engine = get_engine()  # real Engine (the module-level `engine` is a lazy proxy)
    _PG = engine.dialect.name == "postgresql"
    if "audit_export_cursor" not in inspect(engine).get_table_names():
        log.info("[phase30] creating audit_export_cursor")
        with engine.begin() as conn:
            if _PG:
                conn.execute(text("""
                    CREATE TABLE audit_export_cursor (
                        id                  SERIAL PRIMARY KEY,
                        source_table        VARCHAR(64) NOT NULL,
                        last_exported_id    INTEGER DEFAULT 0,
                        last_exported_at    TIMESTAMP,
                        rows_in_last_batch  INTEGER,
                        last_batch_hash     VARCHAR(64),
                        created_at          TIMESTAMP DEFAULT now(),
                        updated_at          TIMESTAMP
                    )
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE audit_export_cursor (
                        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_table        VARCHAR(64) NOT NULL,
                        last_exported_id    INTEGER DEFAULT 0,
                        last_exported_at    TIMESTAMP,
                        rows_in_last_batch  INTEGER,
                        last_batch_hash     VARCHAR(64),
                        created_at          TIMESTAMP,
                        updated_at          TIMESTAMP
                    )
                """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_audit_export_cursor_source "
                "ON audit_export_cursor (source_table)"
            ))

    # Seed the two source rows (idempotent).
    with engine.begin() as conn:
        for src in ("action_logs", "ita_audit_log"):
            exists = conn.execute(
                text("SELECT 1 FROM audit_export_cursor WHERE source_table = :s"),
                {"s": src},
            ).first()
            if not exists:
                conn.execute(
                    text("INSERT INTO audit_export_cursor (source_table, last_exported_id) VALUES (:s, 0)"),
                    {"s": src},
                )
                log.info("[phase30] seeded audit_export_cursor for %s", src)

    log.info("[phase30] audit_export_cursor ready")
