"""
Aurora LTS — Phase 9 Database Migration (Sprint 4 — Accountant Channel)
==========================================================================
Sprint 4 of the 12-week Tax & Document Layer roadmap.

Creates two new tables (via create_tables()):
  - exports                : per-export-request audit row
  - accountant_coa_mappings: per-accountant chart-of-accounts mapping

Idempotent.

NO COLUMN ADDITIONS:
  Both tables are brand-new; no ALTERs needed.
"""

from sqlalchemy import text

from aurora_shared.database.connection import engine


def run_phase9_migrations() -> None:
    print("=" * 60)
    print("[MIGRATE_P9] Phase 9 — Accountant Channel + Exports Migration")
    print("=" * 60)

    expected_tables = ["exports", "accountant_coa_mappings"]
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
                    print(f"[MIGRATE_P9] ⚠️ Probe error on {table}: {e}")
                    missing.append(table)

    for t in found:
        print(f"[MIGRATE_P9] ✅ {t} present")
    for t in missing:
        print(f"[MIGRATE_P9] ⚠️ {t} MISSING — ensure create_tables() ran first")

    print("-" * 60)
    print(f"[MIGRATE_P9] Summary: {len(found)} tables present, {len(missing)} missing")
    print("=" * 60)


if __name__ == "__main__":
    run_phase9_migrations()
