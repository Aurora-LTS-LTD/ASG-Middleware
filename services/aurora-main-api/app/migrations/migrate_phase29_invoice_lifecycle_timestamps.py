"""Aurora LTS — Phase 29: invoice lifecycle timestamp columns.

Adds submitted_at / sent_at / cancelled_at to invoices for lifecycle
observability (timeline views + audit). Idempotent; safe on every boot.
Mirrors the ADD COLUMN pattern of migrate_phase26/28.
"""
from __future__ import annotations
import logging
from sqlalchemy import inspect, text
from aurora_shared.database.connection import engine

log = logging.getLogger(__name__)


def run() -> None:
    inspector = inspect(engine)

    def _has_column(table: str, col: str) -> bool:
        try:
            return col in [c["name"] for c in inspector.get_columns(table)]
        except Exception:
            return True  # assume present if we can't check

    with engine.begin() as conn:
        for col in ("submitted_at", "sent_at", "cancelled_at"):
            if not _has_column("invoices", col):
                try:
                    conn.execute(text(f"ALTER TABLE invoices ADD COLUMN {col} TIMESTAMP"))
                    log.info("[phase29] added invoices.%s", col)
                except Exception as e:
                    log.warning("[phase29] could not add invoices.%s: %s", col, e)

    log.info("[phase29] invoice lifecycle timestamps done")
