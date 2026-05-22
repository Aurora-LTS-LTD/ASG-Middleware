"""
Aurora LTS — Phase 15 Migration (Appendix I Sprint 2)
======================================================

Probes / backfills six DB changes:

  TABLES (created by SQLAlchemy create_tables() from models.py):
    1. business_categories       — Self-referencing L1/L2 taxonomy
    2. ceo_session_snapshots     — "What changed since you last looked"
    3. webauthn_credentials      — Touch ID / Face ID step-up creds

  COLUMNS (added in-place via ALTER TABLE — idempotent):
    4. invoices.gcs_file_path
    5. invoices.retention_class
    6. invoices.last_retrieval_at
    7. invoices.retrieval_count
    8. invoices.legal_hold
    9. organizations.category_id

  CONSTRAINTS (Postgres CHECK guards on business_categories):
   10. category_level_valid     — level IN (1, 2)
   11. root_no_parent           — L1 has no parent, L2 has a parent

  INDEXES:
   12. idx_invoices_retrieval_lookup (partial)
   13. idx_invoices_business_finalized (partial)

This migration is idempotent. Safe to re-run on every startup.

Order matters: business_categories must exist BEFORE organizations.category_id
FK can be created. SQLAlchemy create_tables() handles the table creation order
automatically; this migration's ALTER TABLE for the FK runs AFTER probing.
"""

import os
import logging

from sqlalchemy import text

from app.database.connection import engine

log = logging.getLogger(__name__)


_EXPECTED_TABLES = [
    "business_categories",
    "ceo_session_snapshots",
    "webauthn_credentials",
]

# ALTER TABLE statements — each must be idempotent.
# Postgres 9.6+ supports `ADD COLUMN IF NOT EXISTS`.
_COLUMN_DDL = [
    # ── invoices: archive lifecycle + retrieval tracking ──
    ("invoices", "gcs_file_path",
     "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS gcs_file_path VARCHAR(400)"),
    ("invoices", "retention_class",
     "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS retention_class VARCHAR(20) NOT NULL DEFAULT 'standard'"),
    ("invoices", "last_retrieval_at",
     "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS last_retrieval_at TIMESTAMP"),
    ("invoices", "retrieval_count",
     "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS retrieval_count INTEGER NOT NULL DEFAULT 0"),
    ("invoices", "legal_hold",
     "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS legal_hold BOOLEAN NOT NULL DEFAULT FALSE"),

    # ── organizations: category mapping (L3 of taxonomy) ──
    # Plain ADD COLUMN IF NOT EXISTS — the FK + ON DELETE SET NULL are
    # part of the column DDL via REFERENCES clause.
    ("organizations", "category_id",
     "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS category_id INTEGER "
     "REFERENCES business_categories(id) ON DELETE SET NULL"),
]

# CHECK constraints (idempotent — wrapped in DO block).
_CHECK_DDL = [
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'category_level_valid'
              AND table_name = 'business_categories'
        ) THEN
            ALTER TABLE business_categories
              ADD CONSTRAINT category_level_valid CHECK (level IN (1, 2));
        END IF;
    END $$;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'root_no_parent'
              AND table_name = 'business_categories'
        ) THEN
            ALTER TABLE business_categories
              ADD CONSTRAINT root_no_parent CHECK (
                (level = 1 AND parent_id IS NULL) OR
                (level = 2 AND parent_id IS NOT NULL)
              );
        END IF;
    END $$;
    """,
]

# Partial / specialized indexes (CREATE INDEX IF NOT EXISTS is idempotent).
_INDEX_DDL = [
    """
    CREATE INDEX IF NOT EXISTS idx_invoices_retrieval_lookup
      ON invoices(beneficiary_contact, business_id, invoice_number)
      WHERE status IN ('finalized', 'sent', 'paid')
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_invoices_business_finalized
      ON invoices(business_id, finalized_at DESC)
      WHERE finalized_at IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_organizations_category
      ON organizations(category_id)
    """,
]


def _table_exists(conn, table_name: str) -> bool:
    try:
        conn.execute(text(f"SELECT 1 FROM {table_name} LIMIT 1"))
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _apply_ddl(conn, label: str, ddl: str) -> tuple[bool, str]:
    """Run a single DDL statement. Returns (ok, message)."""
    try:
        conn.execute(text(ddl))
        conn.commit()
        return (True, "applied")
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        msg = str(e).lower()
        # Idempotent DDL may still raise on race conditions; treat
        # "already exists" / "duplicate column" as success.
        if "already exists" in msg or "duplicate" in msg:
            return (True, "already-exists")
        return (False, str(e)[:200])


def run_phase15_migrations() -> None:
    print("=" * 60)
    print("[MIGRATE_P15] Phase 15 — CEO Dashboard Sprint 2 (Appendix I)")
    print("=" * 60)

    found, missing = [], []

    with engine.connect() as conn:
        # ── 1. Probe tables ──
        for t in _EXPECTED_TABLES:
            if _table_exists(conn, t):
                found.append(t)
            else:
                missing.append(t)
        for t in found:
            print(f"[MIGRATE_P15] ✅ {t} present")
        for t in missing:
            print(f"[MIGRATE_P15] ⚠️ {t} MISSING — ensure create_tables() ran first")

        # ── 2. Apply column DDL (only if invoices/organizations tables exist) ──
        if _table_exists(conn, "invoices"):
            for table, col, ddl in _COLUMN_DDL:
                if not _table_exists(conn, table):
                    # organizations may not yet exist on a fresh install where
                    # the model file is loaded but create_tables() hasn't finished
                    print(f"[MIGRATE_P15] ⏩ {table}.{col}: parent table missing — skip")
                    continue
                ok, msg = _apply_ddl(conn, f"{table}.{col}", ddl)
                if ok:
                    print(f"[MIGRATE_P15] ✅ {table}.{col} ({msg})")
                else:
                    print(f"[MIGRATE_P15] ⚠️ {table}.{col} failed: {msg}")

        # ── 3. Apply CHECK constraints ──
        if "business_categories" in found:
            for i, ddl in enumerate(_CHECK_DDL, start=1):
                ok, msg = _apply_ddl(conn, f"check_{i}", ddl)
                if ok:
                    print(f"[MIGRATE_P15] ✅ business_categories check_{i} ({msg})")
                else:
                    print(f"[MIGRATE_P15] ⚠️ check_{i} failed: {msg}")

        # ── 4. Apply specialized indexes ──
        for i, ddl in enumerate(_INDEX_DDL, start=1):
            ok, msg = _apply_ddl(conn, f"index_{i}", ddl)
            if ok:
                print(f"[MIGRATE_P15] ✅ index_{i} ({msg})")
            else:
                print(f"[MIGRATE_P15] ⚠️ index_{i} failed: {msg}")

    print("-" * 60)
    print(f"[MIGRATE_P15] Tables: {len(found)} present, {len(missing)} missing")
    print("=" * 60)


if __name__ == "__main__":
    run_phase15_migrations()
