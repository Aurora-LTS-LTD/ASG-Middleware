"""Aurora LTS — Phase 24 VAT Returns Migration  (P2-22)"""
from __future__ import annotations
import logging
from sqlalchemy import inspect, text
from aurora_shared.database.connection import engine, get_engine

log = logging.getLogger(__name__)
_IS_PG = engine.dialect.name == "postgresql"


def run() -> None:
    if "vat_returns" in inspect(get_engine()).get_table_names():
        log.debug("[phase24] vat_returns exists — skipping")
        return
    log.info("[phase24] creating vat_returns")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE vat_returns (
                id                   SERIAL PRIMARY KEY,
                business_id          INTEGER NOT NULL REFERENCES businesses(id),
                tax_id               VARCHAR(20),
                period_year          INTEGER NOT NULL,
                period_number        INTEGER NOT NULL,
                period_frequency     VARCHAR(16) NOT NULL,
                period_start         DATE NOT NULL,
                period_end           DATE NOT NULL,
                due_date             DATE NOT NULL,
                taxable_sales_ils    FLOAT NOT NULL DEFAULT 0,
                vat_collected_ils    FLOAT NOT NULL DEFAULT 0,
                exempt_sales_ils     FLOAT NOT NULL DEFAULT 0,
                invoice_count        INTEGER NOT NULL DEFAULT 0,
                taxable_purchases_ils FLOAT NOT NULL DEFAULT 0,
                input_vat_ils        FLOAT NOT NULL DEFAULT 0,
                expense_count        INTEGER NOT NULL DEFAULT 0,
                net_vat_payable_ils  FLOAT NOT NULL DEFAULT 0,
                status               VARCHAR(16) NOT NULL DEFAULT 'draft',
                confirmation_number  VARCHAR(64),
                rejection_reason     VARCHAR(500),
                submitted_at         TIMESTAMP,
                submitted_by_user_id INTEGER REFERENCES users(id),
                created_at           TIMESTAMP NOT NULL DEFAULT now(),
                CONSTRAINT uq_vat_return_period UNIQUE (business_id, period_year, period_number, period_frequency)
            )
        """ if _IS_PG else """
            CREATE TABLE vat_returns (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id          INTEGER NOT NULL REFERENCES businesses(id),
                tax_id               VARCHAR(20),
                period_year          INTEGER NOT NULL,
                period_number        INTEGER NOT NULL,
                period_frequency     VARCHAR(16) NOT NULL,
                period_start         DATE NOT NULL,
                period_end           DATE NOT NULL,
                due_date             DATE NOT NULL,
                taxable_sales_ils    FLOAT NOT NULL DEFAULT 0,
                vat_collected_ils    FLOAT NOT NULL DEFAULT 0,
                exempt_sales_ils     FLOAT NOT NULL DEFAULT 0,
                invoice_count        INTEGER NOT NULL DEFAULT 0,
                taxable_purchases_ils FLOAT NOT NULL DEFAULT 0,
                input_vat_ils        FLOAT NOT NULL DEFAULT 0,
                expense_count        INTEGER NOT NULL DEFAULT 0,
                net_vat_payable_ils  FLOAT NOT NULL DEFAULT 0,
                status               VARCHAR(16) NOT NULL DEFAULT 'draft',
                confirmation_number  VARCHAR(64),
                rejection_reason     VARCHAR(500),
                submitted_at         TIMESTAMP,
                submitted_by_user_id INTEGER REFERENCES users(id),
                created_at           TIMESTAMP NOT NULL,
                UNIQUE (business_id, period_year, period_number, period_frequency)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vat_returns_due ON vat_returns (due_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vat_returns_status ON vat_returns (status)"))
    log.info("[phase24] vat_returns created")
