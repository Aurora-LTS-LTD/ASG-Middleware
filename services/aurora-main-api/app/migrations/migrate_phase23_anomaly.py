"""
Aurora LTS — Phase 23 Anomaly Detection Migration  (P2-20)
=========================================================
Provisions the anomaly_events table.
"""

from __future__ import annotations
import logging
from sqlalchemy import inspect, text
from aurora_shared.database.connection import engine, get_engine

log = logging.getLogger(__name__)
_IS_PG = engine.dialect.name == "postgresql"


def run() -> None:
    inspector = inspect(get_engine())  # real Engine — inspect() can't introspect the _LazyEngine proxy
    if "anomaly_events" in inspector.get_table_names():
        log.debug("[phase23] anomaly_events already exists — skipping")
        return

    log.info("[phase23] creating table anomaly_events")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE anomaly_events (
                id                  SERIAL PRIMARY KEY,
                business_id         INTEGER REFERENCES businesses(id),
                invoice_id          INTEGER REFERENCES invoices(id),
                signal_type         VARCHAR(48)  NOT NULL,
                severity            VARCHAR(16)  NOT NULL,
                score               FLOAT        NOT NULL,
                description         VARCHAR(1000) NOT NULL,
                metadata_json       TEXT,
                status              VARCHAR(24)  NOT NULL DEFAULT 'open',
                created_at          TIMESTAMP    NOT NULL DEFAULT now(),
                resolved_at         TIMESTAMP,
                resolved_by_user_id INTEGER REFERENCES users(id),
                resolution_note     VARCHAR(500)
            )
        """ if _IS_PG else """
            CREATE TABLE anomaly_events (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id         INTEGER REFERENCES businesses(id),
                invoice_id          INTEGER REFERENCES invoices(id),
                signal_type         VARCHAR(48)  NOT NULL,
                severity            VARCHAR(16)  NOT NULL,
                score               FLOAT        NOT NULL,
                description         VARCHAR(1000) NOT NULL,
                metadata_json       TEXT,
                status              VARCHAR(24)  NOT NULL DEFAULT 'open',
                created_at          TIMESTAMP    NOT NULL,
                resolved_at         TIMESTAMP,
                resolved_by_user_id INTEGER REFERENCES users(id),
                resolution_note     VARCHAR(500)
            )
        """))
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS ix_anomaly_events_status ON anomaly_events (status)",
            "CREATE INDEX IF NOT EXISTS ix_anomaly_events_severity ON anomaly_events (severity)",
            "CREATE INDEX IF NOT EXISTS ix_anomaly_events_business ON anomaly_events (business_id)",
            "CREATE INDEX IF NOT EXISTS ix_anomaly_events_created ON anomaly_events (created_at)",
        ]:
            conn.execute(text(idx_sql))

    log.info("[phase23] anomaly_events created")
