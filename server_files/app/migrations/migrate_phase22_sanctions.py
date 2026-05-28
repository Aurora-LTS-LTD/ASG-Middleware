"""
Aurora LTS — Phase 22 Sanctions / AML Migration  (P2-08)
=========================================================

Provisions the AML / sanctions screening tables.

Tables (idempotent — CREATE TABLE IF NOT EXISTS):
  1. sanctions_list_entries
        Cached entries downloaded from OFAC SDN, IL-MOF NBCTF,
        EU Consolidated, and UK HMT.  Refreshed weekly by Cloud
        Scheduler. Unique constraint on (list_source, external_id).

  2. sanctions_screening_hits
        Every screening call that produced a match above threshold.
        Status lifecycle: pending_review → false_positive | confirmed | ignored.
        Auto-cleared hits (low-score below human-review threshold) land
        directly in status='auto_cleared'.

Indexes (idempotent):
  • ix_sanctions_full_name       — fast fuzzy pre-filter on full_name
  • ix_sanctions_src_extid       — covered by UniqueConstraint (created automatically)
  • ix_screening_hits_status     — admin queue filtering by status
  • ix_screening_hits_business   — per-business hit history
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.database.connection import engine, SessionLocal

log = logging.getLogger(__name__)

_IS_POSTGRES = engine.dialect.name == "postgresql"


def run() -> None:
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    with engine.begin() as conn:
        # ── sanctions_list_entries ────────────────────────────
        if "sanctions_list_entries" not in existing:
            log.info("[phase22] creating table sanctions_list_entries")
            conn.execute(text("""
                CREATE TABLE sanctions_list_entries (
                    id           SERIAL PRIMARY KEY,
                    list_source  VARCHAR(32)  NOT NULL,
                    external_id  VARCHAR(64)  NOT NULL,
                    full_name    VARCHAR(512) NOT NULL,
                    aliases      TEXT,
                    entity_type  VARCHAR(16),
                    country_code VARCHAR(8),
                    program      VARCHAR(120),
                    last_updated_at TIMESTAMP,
                    fetched_at   TIMESTAMP    NOT NULL DEFAULT now(),
                    CONSTRAINT uq_sanctions_src_extid UNIQUE (list_source, external_id)
                )
            """ if _IS_POSTGRES else """
                CREATE TABLE sanctions_list_entries (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    list_source  VARCHAR(32)  NOT NULL,
                    external_id  VARCHAR(64)  NOT NULL,
                    full_name    VARCHAR(512) NOT NULL,
                    aliases      TEXT,
                    entity_type  VARCHAR(16),
                    country_code VARCHAR(8),
                    program      VARCHAR(120),
                    last_updated_at TIMESTAMP,
                    fetched_at   TIMESTAMP    NOT NULL,
                    UNIQUE (list_source, external_id)
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_sanctions_full_name "
                "ON sanctions_list_entries (full_name)"
            ))
            log.info("[phase22] sanctions_list_entries created")
        else:
            log.debug("[phase22] sanctions_list_entries already exists — skipping")

        # ── sanctions_screening_hits ──────────────────────────
        if "sanctions_screening_hits" not in existing:
            log.info("[phase22] creating table sanctions_screening_hits")
            conn.execute(text("""
                CREATE TABLE sanctions_screening_hits (
                    id                   SERIAL PRIMARY KEY,
                    business_id          INTEGER REFERENCES businesses(id),
                    invoice_id           INTEGER REFERENCES invoices(id),
                    queried_name         VARCHAR(512) NOT NULL,
                    matched_entry_id     INTEGER NOT NULL
                        REFERENCES sanctions_list_entries(id),
                    match_score          FLOAT NOT NULL,
                    status               VARCHAR(24) NOT NULL DEFAULT 'pending_review',
                    created_at           TIMESTAMP NOT NULL DEFAULT now(),
                    reviewed_at          TIMESTAMP,
                    reviewed_by_user_id  INTEGER REFERENCES users(id),
                    review_note          VARCHAR(500)
                )
            """ if _IS_POSTGRES else """
                CREATE TABLE sanctions_screening_hits (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_id          INTEGER REFERENCES businesses(id),
                    invoice_id           INTEGER REFERENCES invoices(id),
                    queried_name         VARCHAR(512) NOT NULL,
                    matched_entry_id     INTEGER NOT NULL
                        REFERENCES sanctions_list_entries(id),
                    match_score          FLOAT NOT NULL,
                    status               VARCHAR(24) NOT NULL DEFAULT 'pending_review',
                    created_at           TIMESTAMP NOT NULL,
                    reviewed_at          TIMESTAMP,
                    reviewed_by_user_id  INTEGER REFERENCES users(id),
                    review_note          VARCHAR(500)
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_screening_hits_status "
                "ON sanctions_screening_hits (status)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_screening_hits_business "
                "ON sanctions_screening_hits (business_id)"
            ))
            log.info("[phase22] sanctions_screening_hits created")
        else:
            log.debug("[phase22] sanctions_screening_hits already exists — skipping")

    log.info("[phase22] phase 22 complete")
