"""
Aurora LTS — Phase 10 Migration (Sprint 5 — Revenue Engine)
================================================================
Creates 3 new tables via create_tables(): revenue_share_ledger,
accountant_payouts, accountant_referrals. Idempotent probe-and-skip.
"""

from sqlalchemy import text
from app.database.connection import engine


def run_phase10_migrations() -> None:
    print("=" * 60)
    print("[MIGRATE_P10] Phase 10 — Revenue Engine Migration")
    print("=" * 60)

    expected = ["revenue_share_ledger", "accountant_payouts", "accountant_referrals"]
    found, missing = [], []
    with engine.connect() as conn:
        for table in expected:
            try:
                conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
                conn.commit()
                found.append(table)
            except Exception as e:
                msg = str(e).lower()
                missing.append(table) if ("no such table" in msg or "does not exist" in msg) else (
                    print(f"[MIGRATE_P10] ⚠️ Probe error on {table}: {e}"), missing.append(table)
                )
    for t in found:
        print(f"[MIGRATE_P10] ✅ {t} present")
    for t in missing:
        print(f"[MIGRATE_P10] ⚠️ {t} MISSING — ensure create_tables() ran first")
    print("-" * 60)
    print(f"[MIGRATE_P10] Summary: {len(found)} present, {len(missing)} missing")
    print("=" * 60)


if __name__ == "__main__":
    run_phase10_migrations()
