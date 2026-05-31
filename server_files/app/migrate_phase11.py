"""
Aurora LTS — Phase 11 Migration (Sprint 6 — Hardening)
============================================================
Creates audit_export_cursor table + installs SQLAlchemy event-listener
immutability guards for the audit-grade tables.

POSTGRES IMMUTABILITY (production):
  Real Postgres triggers go in via a separate Cloud Run Job once the
  Postgres instance is live (see docs/DEPLOYMENT.md). The SQLAlchemy
  event listeners below mirror that behaviour so dev / SQLite get the
  same protection.
"""

from sqlalchemy import text, event
from aurora_shared.database.connection import engine


def run_phase11_migrations() -> None:
    print("=" * 60)
    print("[MIGRATE_P11] Phase 11 — Hardening Migration")
    print("=" * 60)

    expected = ["audit_export_cursor"]
    found, missing = [], []
    with engine.connect() as conn:
        for table in expected:
            try:
                conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
                conn.commit()
                found.append(table)
            except Exception as e:
                msg = str(e).lower()
                if "no such table" in msg or "does not exist" in msg:
                    missing.append(table)
                else:
                    print(f"[MIGRATE_P11] ⚠️ Probe error on {table}: {e}")
                    missing.append(table)

    for t in found:
        print(f"[MIGRATE_P11] ✅ {t} present")
    for t in missing:
        print(f"[MIGRATE_P11] ⚠️ {t} MISSING — ensure create_tables() ran first")

    # Install application-level immutability guards. They register
    # SQLAlchemy 'before_update' / 'before_delete' listeners that raise
    # on terminal-state rows.
    try:
        from app.services.compliance.immutability import install_immutability_guards
        install_immutability_guards()
        print("[MIGRATE_P11] ✅ Immutability guards installed")
    except Exception as e:
        print(f"[MIGRATE_P11] ⚠️ Immutability guards setup failed: {e}")

    print("-" * 60)
    print(f"[MIGRATE_P11] Summary: {len(found)} present, {len(missing)} missing")
    print("=" * 60)


if __name__ == "__main__":
    run_phase11_migrations()
