"""Aurora LTS — Phase 27: accountant_password_resets table.

Production email-based password recovery for the Accountant Portal. Idempotent;
safe on every boot. Mirrors the dialect-aware pattern of migrate_phase25.
"""
from __future__ import annotations
import logging
from sqlalchemy import inspect, text
from aurora_shared.database.connection import engine, get_engine

log = logging.getLogger(__name__)
_PG = engine.dialect.name == "postgresql"


def run() -> None:
    if "accountant_password_resets" in inspect(get_engine()).get_table_names():
        log.debug("[phase27] accountant_password_resets exists")
        return
    log.info("[phase27] creating accountant_password_resets")

    with engine.begin() as conn:
        if _PG:
            conn.execute(text("""
                CREATE TABLE accountant_password_resets (
                    id              SERIAL PRIMARY KEY,
                    email           VARCHAR(120) NOT NULL,
                    code_hash       VARCHAR(64) NOT NULL,
                    issued_at       TIMESTAMP NOT NULL DEFAULT now(),
                    expires_at      TIMESTAMP NOT NULL,
                    attempts_count  INTEGER NOT NULL DEFAULT 0,
                    locked_until    TIMESTAMP,
                    consumed_at     TIMESTAMP,
                    ip_hash         VARCHAR(64) NOT NULL
                )
            """))
        else:
            conn.execute(text("""
                CREATE TABLE accountant_password_resets (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    email           VARCHAR(120) NOT NULL,
                    code_hash       VARCHAR(64) NOT NULL,
                    issued_at       TIMESTAMP NOT NULL,
                    expires_at      TIMESTAMP NOT NULL,
                    attempts_count  INTEGER NOT NULL DEFAULT 0,
                    locked_until    TIMESTAMP,
                    consumed_at     TIMESTAMP,
                    ip_hash         VARCHAR(64) NOT NULL
                )
            """))

        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_accountant_password_resets_email "
            "ON accountant_password_resets (email)"
        ))
        if _PG:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_accountant_password_resets_email_recent "
                "ON accountant_password_resets (email, issued_at) WHERE consumed_at IS NULL"
            ))

    log.info("[phase27] accountant_password_resets created")
