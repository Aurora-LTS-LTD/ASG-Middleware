"""
Aurora LTS — Phase 7 Database Migration (Document AI Receipt Pipeline)
========================================================================
Sprint 2 of the 12-week Tax & Document Layer roadmap.

WHAT THIS MIGRATION DOES:
  Creates two NEW tables (via create_tables() at startup):
    - receipts   : raw uploaded image + OCR result + GCS coordinates
    - expenses   : structured tax record produced from a Receipt

  Both are organization_id-native from birth (per the dual-write rule
  established in Sprint 1.8). No backfill is needed — these tables hold
  data that didn't exist before Sprint 2.

  Idempotent: safe to run repeatedly. The probe-and-skip pattern matches
  Phase 6b's structure.

NO COLUMN ADDITIONS:
  Unlike phases 4 / 5 / 6 which extended the legacy `users` table, this
  migration is purely additive — two brand new tables, no ALTERs.

REAL-WORLD ANALOGY:
  Phase 6b built the onboarding wing. Phase 7 builds the document
  archive on the next floor — a row of new filing cabinets specifically
  for receipts and the bookkeeping entries they generate.

RUN:
  Automatic at startup (registered in main.py).
  Manual:
    python -c "from app.migrate_phase7 import run_phase7_migrations; run_phase7_migrations()"
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
from sqlalchemy import text

from aurora_shared.database.connection import engine


def run_phase7_migrations() -> None:
    """
    Phase 7 setup. CREATE TABLE for receipts + expenses is handled by
    SQLAlchemy's create_tables() in main.py at startup. This function:
      - Verifies both tables are present (sanity probe)
      - Logs a one-line summary
    """
    print("=" * 60)
    print("[MIGRATE_P7] Phase 7 — Document AI Receipt Pipeline Migration")
    print("=" * 60)

    expected_tables = ["receipts", "expenses"]
    found = []
    missing = []

    with engine.connect() as conn:
        for table in expected_tables:
            try:
                conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
                conn.commit()
                found.append(table)
            except Exception as e:
                msg = str(e).lower()
                if "no such table" in msg or "does not exist" in msg:
                    missing.append(table)
                else:
                    print(f"[MIGRATE_P7] ⚠️ Probe error on {table}: {e}")
                    missing.append(table)

    for t in found:
        print(f"[MIGRATE_P7] ✅ {t} present")
    for t in missing:
        print(f"[MIGRATE_P7] ⚠️ {t} MISSING — ensure create_tables() ran first")

    print("-" * 60)
    print(
        f"[MIGRATE_P7] Summary: {len(found)} tables present, {len(missing)} missing"
    )
    print("=" * 60)


if __name__ == "__main__":
    run_phase7_migrations()
