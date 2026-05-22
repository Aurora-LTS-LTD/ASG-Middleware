"""
Aurora LTS — Phase 8 Database Migration (Sprint 3 — Real ITA Client)
========================================================================
Sprint 3 of the 12-week Tax & Document Layer roadmap.

WHAT THIS MIGRATION DOES:
  1. Adds 4 columns to the existing `invoices` table:
       - ita_request_id          (idempotency key per attempt)
       - ita_response_raw_json   (sanitised JSON, audit evidence)
       - ita_status_code         (HTTP status returned)
       - allocation_issued_at    (timestamp when ITA approved)
  2. Creates the `ita_audit_log` table via create_tables() at startup.

  All idempotent — safe to re-run.

DIALECT NOTES:
  - SQLite: ALTER TABLE ADD COLUMN works for these (no UNIQUE / FK).
  - Postgres: same statements work; "duplicate column" error message
    is detected by the application-level guard below.

NO BACKFILL:
  These columns hold data that didn't exist before. Existing invoices
  get NULL — the FSM sees them as "no ITA record" and treats them as
  legacy mock-backend invoices.

REAL-WORLD ANALOGY:
  Phase 7 added the receipt-archive room. Phase 8 builds the ITA
  audit-log filing cabinet next to it — every government call gets a
  carbon-copy receipt stuffed inside, sorted by request-id.
"""

from sqlalchemy import text

from app.database.connection import engine


def run_phase8_migrations() -> None:
    """
    Phase 8 migration. Adds invoice tracking columns + creates the
    ita_audit_log table (the latter handled by create_tables()).

    Idempotent: skips columns that already exist.
    """
    print("=" * 60)
    print("[MIGRATE_P8] Phase 8 — Real ITA Client + Audit Log Migration")
    print("=" * 60)

    new_columns = [
        ("invoices", "ita_request_id",         "TEXT"),
        ("invoices", "ita_response_raw_json",  "TEXT"),
        ("invoices", "ita_status_code",        "INTEGER"),
        ("invoices", "allocation_issued_at",   "DATETIME"),
    ]

    added = 0
    skipped = 0

    with engine.connect() as conn:
        for table, column, col_def in new_columns:
            sql = f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"[MIGRATE_P8] ✅ Added {table}.{column}")
                added += 1
            except Exception as e:
                msg = str(e).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    print(f"[MIGRATE_P8] ⏩ {table}.{column} already exists — skipped")
                    skipped += 1
                else:
                    print(f"[MIGRATE_P8] ⚠️ Unexpected error for {table}.{column}: {e}")
                    skipped += 1

    # ── Verify the new ita_audit_log table is present (sanity probe) ──
    table_present = False
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM ita_audit_log LIMIT 1"))
            table_present = True
        except Exception as e:
            msg = str(e).lower()
            if "no such table" in msg or "does not exist" in msg:
                table_present = False
            else:
                print(f"[MIGRATE_P8] ⚠️ Probe error on ita_audit_log: {e}")

    if table_present:
        print("[MIGRATE_P8] ✅ ita_audit_log table present")
    else:
        print("[MIGRATE_P8] ⚠️ ita_audit_log MISSING — ensure create_tables() ran first")

    print("-" * 60)
    print(
        f"[MIGRATE_P8] Summary: {added} columns added, {skipped} skipped, "
        f"audit table {'present' if table_present else 'missing'}"
    )
    print("=" * 60)


if __name__ == "__main__":
    run_phase8_migrations()
