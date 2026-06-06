"""
Aurora LTS — Phase 13 Migration (Track 3 — Break-glass Tier 1.5)
=================================================================

Verifies the `break_glass_tokens` table exists. The schema is owned
by SQLAlchemy `create_tables()` (via the BreakGlassToken model in
app/database/models.py); this migration's job is to probe + log.

Idempotent. Safe to re-run on every startup.

WHY A SEPARATE TABLE:
  Break-glass JWTs need server-side revocation (a stolen token must
  be killable without rotating JWT_SECRET, which would invalidate
  ALL tokens). The table stores just the `jti` + metadata; the JWT
  itself is signed with JWT_SECRET and never stored in the DB.

  require_admin() in auth_middleware.py looks up incoming
  `is_emergency_break_glass=true` tokens here BEFORE bypassing IAP.
"""

from sqlalchemy import text
from aurora_shared.database.connection import engine


def run_phase13_migrations() -> None:
    print("=" * 60)
    print("[MIGRATE_P13] Phase 13 — Break-glass Tier 1.5 (BreakGlassToken)")
    print("=" * 60)

    expected = ["break_glass_tokens"]
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
                    print(f"[MIGRATE_P13] ⚠️ Probe error on {table}: {e}")
                    missing.append(table)
                try:
                    conn.rollback()
                except Exception:
                    pass

    for t in found:
        print(f"[MIGRATE_P13] ✅ {t} present")
    for t in missing:
        print(f"[MIGRATE_P13] ⚠️ {t} MISSING — ensure create_tables() ran first")

    print("-" * 60)
    print(f"[MIGRATE_P13] Summary: {len(found)} present, {len(missing)} missing")
    print("=" * 60)


if __name__ == "__main__":
    run_phase13_migrations()
