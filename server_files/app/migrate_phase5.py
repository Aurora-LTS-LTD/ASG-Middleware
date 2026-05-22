"""
ASG Solutions — Phase 5 Database Migration (WhatsApp Bot)
===========================================================
Safely adds the columns + tables required by the WhatsApp Business
API bot to the EXISTING database without losing data.

REAL-WORLD ANALOGY:
  Same as Phase 4: we're adding new slots to existing paper forms
  (users table) and adding two brand-new forms (WhatsApp session,
  outbound log) to the filing cabinet.

SQLite QUIRKS (unchanged from Phase 4):
  - ALTER TABLE ADD COLUMN ... UNIQUE  is not supported in one go.
    For whatsapp_phone_e164 we add the column WITHOUT UNIQUE here
    and rely on the application layer to enforce uniqueness. When
    we migrate to Postgres on GCP, we'll add the UNIQUE constraint
    properly.
  - Each column needs its own ALTER statement. We try/except to
    make this idempotent (safe to run multiple times).

WHAT THIS ADDS:
  To table 'users':
    - whatsapp_phone_e164       TEXT
    - whatsapp_pairing_code     TEXT
    - whatsapp_pairing_expires  DATETIME

  New tables:
    - whatsapp_sessions          (via create_tables())
    - whatsapp_outbound_logs     (via create_tables())

USAGE:
  Called automatically at startup from main.py. Can also be run:
    cd ~/asg_platform
    source venv/bin/activate
    python -c "from app.migrate_phase5 import run_phase5_migrations; run_phase5_migrations()"
"""

from sqlalchemy import text

from app.database.connection import engine


def run_phase5_migrations():
    """
    Execute all Phase 5 ALTER TABLE statements.
    Safe to run multiple times — skips any column that already exists.
    """
    # Each tuple is: (table_name, column_name, SQL_type_and_default)
    new_columns = [
        # users table — WhatsApp fields
        ("users", "whatsapp_phone_e164",      "TEXT"),
        ("users", "whatsapp_pairing_code",    "TEXT"),
        ("users", "whatsapp_pairing_expires", "DATETIME"),
    ]

    added = 0
    skipped = 0

    with engine.connect() as conn:
        for table, column, col_def in new_columns:
            sql = f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"[MIGRATE_P5] ✅ Added {table}.{column}")
                added += 1
            except Exception as e:
                error_msg = str(e).lower()
                if "duplicate column" in error_msg or "already exists" in error_msg:
                    print(f"[MIGRATE_P5] ⏩ {table}.{column} already exists — skipped")
                    skipped += 1
                else:
                    print(f"[MIGRATE_P5] ⚠️ Unexpected error for {table}.{column}: {e}")
                    skipped += 1

    print(f"[MIGRATE_P5] Done: {added} added, {skipped} skipped")
    print(f"[MIGRATE_P5] whatsapp_sessions + whatsapp_outbound_logs will be created by create_tables()")


if __name__ == "__main__":
    run_phase5_migrations()
