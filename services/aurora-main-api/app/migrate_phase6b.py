"""
ASG / Aurora Solutions — Phase 6b Database Migration
=======================================================
Aurora Onboarding Module on top of the Sprint 1 Identity Foundation.

WHAT THIS MIGRATION DOES:
  1. Adds NO new columns to existing tables (the User extensions
     for onboarding landed in Phase 6 — first_name, last_name,
     onboarding_status, email_verified_at, etc.).
  2. Creates SIX new tables (via create_tables() at startup):
       - onboarding_states     : multi-step web wizard journey
       - otp_verifications     : phone/email OTP (bcrypt-hashed)
       - kyc_documents         : Israeli ID + business cert uploads
       - subscriptions         : plan + billing-cycle + trial state
       - payment_methods       : tokenized PayPlus/etc references
       - subscription_payments : per-charge ledger
  3. Idempotent: safe to run repeatedly.

NO BACKFILL:
  These tables hold data that didn't exist before. Nothing to migrate.
  The first activate() call from a tenant populates them naturally.

REAL-WORLD ANALOGY:
  Phase 6 added rooms to the existing building (organizations,
  memberships). Phase 6b builds the onboarding wing — a brand-new
  set of rooms with their own filing cabinets, accessible only
  through the new onboarding entrance.

RUN:
  Automatic at startup (registered in main.py).
  Manual:
    python -c "from app.migrate_phase6b import run_phase6b_migrations; run_phase6b_migrations()"
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
from sqlalchemy import text

from aurora_shared.database.connection import engine, SessionLocal


# ─────────────────────────────────────────────────────────────
# Phase 6b is mostly table-creation (handled by create_tables()).
# This function exists for symmetry with phase 4 / 5 / 6 and as a
# place to hang any future column additions on the new tables.
# ─────────────────────────────────────────────────────────────
def _fix_payment_methods_org_id_nullability() -> bool:
    """
    Self-healing fix for an early-version schema bug:

      In the first draft of Phase 6b (April 2026), payment_methods.organization_id
      was declared NOT NULL. That contradicts the onboarding architecture, where
      a PaymentMethod is captured BEFORE the Organization is committed at
      activate(). The corrected model has the column nullable.

      SQLite has no `ALTER COLUMN ... DROP NOT NULL`, so we use the canonical
      "create new, copy, drop, rename" rebuild. The rebuild runs ONLY when the
      old NOT NULL is detected — fully idempotent on already-correct schemas.

    POSTGRES (Cloud SQL): the bug never lands because Postgres respects the
    nullable=True declaration on first CREATE TABLE. We early-return without
    touching the table. The dialect guard below is what enforces this.

    Returns True if the rebuild ran, False if the schema was already correct
    (or this isn't a SQLite engine).
    """
    # Dialect guard — this rebuild dance is SQLite-only. On Postgres
    # the column is created nullable from day one, so there's nothing
    # to fix and PRAGMA table_info would fail anyway.
    if engine.dialect.name != "sqlite":
        return False

    with engine.connect() as conn:
        # Inspect the column definition
        try:
            rows = conn.execute(text("PRAGMA table_info(payment_methods)")).fetchall()
        except Exception:
            return False
        if not rows:
            return False

        org_col = next((r for r in rows if r[1] == "organization_id"), None)
        if not org_col:
            return False
        # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
        is_notnull = bool(org_col[3])
        if not is_notnull:
            return False  # Already correct — no-op

        print("[MIGRATE_P6B] 🔧 Detected legacy NOT NULL on payment_methods.organization_id — rebuilding…")

        try:
            # Lift any payment-method rows so we can carry them across the rebuild
            existing_rows = conn.execute(text("SELECT * FROM payment_methods")).fetchall()
            print(f"[MIGRATE_P6B]    {len(existing_rows)} existing row(s) to preserve")
            legacy_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(payment_methods)")).fetchall()]

            # ── Stash rows as a list-of-dicts so we can drop the legacy table cleanly ──
            stash = [dict(zip(legacy_cols, row)) for row in existing_rows]

            # Drop the entire legacy structure (table + its indexes go with it).
            # We've already lifted the rows in memory.
            # SQLAlchemy's create_tables() can't drop just one table cleanly across
            # all dialects, so we DROP TABLE explicitly and recreate via metadata.
            conn.execute(text("DROP TABLE payment_methods"))
            # Defensive: clean any lingering orphan indexes (from prior partial rebuild)
            conn.execute(text("DROP INDEX IF EXISTS ix_payment_methods_id"))
            conn.execute(text("DROP INDEX IF EXISTS ix_payment_methods_organization_id"))
            conn.commit()

            # Recreate via SQLAlchemy metadata — picks up the new nullable=True
            from aurora_shared.database.models import PaymentMethod  # noqa: F401 — registers metadata
            PaymentMethod.__table__.create(bind=engine)

            # Re-insert lifted rows
            if stash:
                col_list = ", ".join(stash[0].keys())
                placeholders = ", ".join(f":{k}" for k in stash[0].keys())
                for row_dict in stash:
                    conn.execute(
                        text(f"INSERT INTO payment_methods ({col_list}) VALUES ({placeholders})"),
                        row_dict,
                    )

            # Belt-and-braces: drop any orphan _legacy_payment_methods from a failed prior rebuild
            conn.execute(text("DROP TABLE IF EXISTS _legacy_payment_methods"))
            conn.commit()
            print("[MIGRATE_P6B] ✅ payment_methods rebuilt with nullable organization_id")
            return True
        except Exception as e:
            print(f"[MIGRATE_P6B] ⚠️ payment_methods rebuild failed: {e}")
            return False


def run_phase6b_migrations() -> None:
    """
    Phase 6b setup. The actual CREATE TABLE for onboarding_states /
    otp_verifications / kyc_documents / subscriptions / payment_methods
    / subscription_payments is handled by SQLAlchemy's create_tables()
    in main.py at startup.

    This function:
      - Self-heals an early-draft schema bug on payment_methods.organization_id
      - Verifies the new tables are present (sanity check)
      - Logs a one-line summary
    """
    print("=" * 60)
    print("[MIGRATE_P6B] Phase 6b — Aurora Onboarding Module Migration")
    print("=" * 60)

    # Self-healing column nullability fix (idempotent — no-op when already correct)
    _fix_payment_methods_org_id_nullability()

    expected_tables = [
        "onboarding_states",
        "otp_verifications",
        "kyc_documents",
        "subscriptions",
        "payment_methods",
        "subscription_payments",
    ]

    found = []
    missing = []

    with engine.connect() as conn:
        for table in expected_tables:
            try:
                # Tiny query to confirm the table exists; SQLite returns
                # 'no such table' if it doesn't.
                conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
                conn.commit()
                found.append(table)
            except Exception as e:
                msg = str(e).lower()
                if "no such table" in msg or "does not exist" in msg:
                    missing.append(table)
                else:
                    # Any other error is fishy but non-fatal at boot
                    print(f"[MIGRATE_P6B] ⚠️ Probe error on {table}: {e}")
                    missing.append(table)

    for t in found:
        print(f"[MIGRATE_P6B] ✅ {t} present")
    for t in missing:
        print(f"[MIGRATE_P6B] ⚠️ {t} MISSING — ensure create_tables() ran first")

    print("-" * 60)
    print(
        f"[MIGRATE_P6B] Summary: {len(found)} tables present, {len(missing)} missing"
    )
    print("=" * 60)


if __name__ == "__main__":
    run_phase6b_migrations()
