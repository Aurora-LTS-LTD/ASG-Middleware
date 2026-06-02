"""Aurora LTS — Phase 25 Payment Links Migration  (P2-23)"""
from __future__ import annotations
import logging
from sqlalchemy import inspect, text
from aurora_shared.database.connection import engine

log = logging.getLogger(__name__)
_PG = engine.dialect.name == "postgresql"


def run() -> None:
    if "payment_links" in inspect(engine).get_table_names():
        log.debug("[phase25] payment_links exists")
        return
    log.info("[phase25] creating payment_links")

    with engine.begin() as conn:
        if _PG:
            conn.execute(text("""
                CREATE TABLE payment_links (
                    id                      SERIAL PRIMARY KEY,
                    invoice_id              INTEGER NOT NULL REFERENCES invoices(id),
                    business_id             INTEGER NOT NULL REFERENCES businesses(id),
                    token                   VARCHAR(64) UNIQUE NOT NULL,
                    nonce                   VARCHAR(32) NOT NULL,
                    amount_ils              FLOAT NOT NULL,
                    currency                VARCHAR(3) NOT NULL DEFAULT 'ILS',
                    status                  VARCHAR(16) NOT NULL DEFAULT 'open',
                    expires_at              TIMESTAMP NOT NULL,
                    paid_at                 TIMESTAMP,
                    payplus_transaction_id  VARCHAR(128),
                    created_at              TIMESTAMP NOT NULL DEFAULT now(),
                    created_by_user_id      INTEGER REFERENCES users(id)
                )
            """))
        else:
            conn.execute(text("""
                CREATE TABLE payment_links (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_id              INTEGER NOT NULL REFERENCES invoices(id),
                    business_id             INTEGER NOT NULL REFERENCES businesses(id),
                    token                   VARCHAR(64) UNIQUE NOT NULL,
                    nonce                   VARCHAR(32) NOT NULL,
                    amount_ils              FLOAT NOT NULL,
                    currency                VARCHAR(3) NOT NULL DEFAULT 'ILS',
                    status                  VARCHAR(16) NOT NULL DEFAULT 'open',
                    expires_at              TIMESTAMP NOT NULL,
                    paid_at                 TIMESTAMP,
                    payplus_transaction_id  VARCHAR(128),
                    created_at              TIMESTAMP NOT NULL,
                    created_by_user_id      INTEGER REFERENCES users(id)
                )
            """))

        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_payment_links_invoice "
            "ON payment_links (invoice_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_payment_links_status "
            "ON payment_links (status)"
        ))

    log.info("[phase25] payment_links created")
