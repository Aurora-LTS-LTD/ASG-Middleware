"""
ASG Solutions — Phase 4 Database Migration
============================================
Safely adds the new columns required by the Telegram bot and
allocation retry queue to the EXISTING database tables.

REAL-WORLD ANALOGY:
  The database tables already exist (like a filing cabinet from last year).
  We can't delete and recreate them (all the data would be lost).
  Instead, we ADD new slots/fields to the existing forms — like adding
  a new section to an existing paper form.

SQLite QUIRK:
  Unlike PostgreSQL, SQLite does not support:
    - ADD COLUMN ... UNIQUE  (constraint must be added separately)
    - Multiple ADD COLUMN in one statement
  Each column must be added in its own ALTER TABLE statement.
  We use try/except to gracefully skip columns that already exist
  (safe to run multiple times).

WHAT THIS ADDS:
  To table 'users':
    - telegram_user_id        TEXT  (Telegram numeric user ID)
    - telegram_pairing_code   TEXT  (6-digit one-time code)
    - telegram_pairing_expires DATETIME
    - morning_digest_enabled  INTEGER DEFAULT 0

  To table 'invoices':
    - allocation_retry_count    INTEGER DEFAULT 0
    - allocation_next_retry_at  DATETIME

  New table 'telegram_sessions':
    - Auto-created by create_tables() — no ALTER needed

USAGE:
  Called automatically at startup (in main.py startup event).
  Can also be run manually:
    cd ~/asg_platform
    source venv/bin/activate
    python -c "from app.migrate_phase4 import run_phase4_migrations; run_phase4_migrations()"
"""

from sqlalchemy import text
from app.database.connection import engine


def run_phase4_migrations():
    """
    Execute all Phase 4 ALTER TABLE statements.
    Safe to run multiple times — skips any column that already exists.
    """
    # Each tuple is: (table_name, column_name, SQL_type_and_default)
    # We'll try each one and catch the "duplicate column name" error.
    new_columns = [
        # users table — Telegram fields
        ("users", "telegram_user_id",         "TEXT"),
        ("users", "telegram_pairing_code",     "TEXT"),
        ("users", "telegram_pairing_expires",  "DATETIME"),
        ("users", "morning_digest_enabled",    "INTEGER DEFAULT 0"),

        # invoices table — allocation retry fields
        ("invoices", "allocation_retry_count",    "INTEGER DEFAULT 0"),
        ("invoices", "allocation_next_retry_at",  "DATETIME"),
    ]

    added = 0
    skipped = 0

    with engine.connect() as conn:
        for table, column, col_def in new_columns:
            sql = f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"[MIGRATE_P4] ✅ Added {table}.{column}")
                added += 1
            except Exception as e:
                error_msg = str(e).lower()
                if "duplicate column" in error_msg or "already exists" in error_msg:
                    print(f"[MIGRATE_P4] ⏩ {table}.{column} already exists — skipped")
                    skipped += 1
                else:
                    # Unexpected error — log but don't crash the server
                    print(f"[MIGRATE_P4] ⚠️ Unexpected error for {table}.{column}: {e}")
                    skipped += 1

    print(f"[MIGRATE_P4] Done: {added} added, {skipped} skipped")


if __name__ == "__main__":
    run_phase4_migrations()
