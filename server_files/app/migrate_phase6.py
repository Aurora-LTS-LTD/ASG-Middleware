"""
ASG Solutions — Phase 6 Database Migration (Identity Foundation)
==================================================================
Sprint 1 of the 12-week Tax & Document Layer roadmap.

WHAT THIS MIGRATION DOES (in order):
  1. Adds new columns to `users` for onboarding & verification.
  2. Creates four new tables (via create_tables()):
        - organizations
        - memberships
        - accountant_engagements
        - invitations
  3. BACKFILLS data from the legacy schema:
        - For every existing Business, create one Organization
          (with display_name = Business.name, etc.)
        - For every existing User with role='business_owner'
          AND business_id != NULL, create a Membership(role='owner',
          is_primary=True) tying that user to the new Organization.
  4. Idempotent: safe to run multiple times. Skips columns/rows that
     already exist.

EXPAND/CONTRACT NOTES:
  This is PHASE A of the User.business_id → Membership migration.
  - We do NOT drop User.business_id here. It remains authoritative
    for legacy code paths.
  - Memberships are added in parallel; reads gradually shift to them
    over Sprint 2-3.
  - User.business_id is dropped in Sprint 5 (Phase C).

REAL-WORLD ANALOGY:
  Same as the Phase 4 / Phase 5 migrations. We are adding new slots
  to the existing forms (users) and creating new forms (organizations,
  memberships, accountant_engagements, invitations) without touching
  what's already filed. The only "active" change is the backfill —
  populating the new forms with copies of existing data so that
  legacy and new tables stay in sync until we cut over.

RUN:
  Automatic at startup (registered in main.py).
  Manual:
    cd ~/Desktop/ASG-Middleware/server_files
    source ../venv/bin/activate
    python -c "from app.migrate_phase6 import run_phase6_migrations; run_phase6_migrations()"
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import datetime

from sqlalchemy import text

from app.database.connection import engine, SessionLocal
from app.database.models import (
    Business,
    User,
    Organization,
    Membership,
)


# ─────────────────────────────────────────────────────────────
# STEP 1 — Add new columns to existing `users` table
# ─────────────────────────────────────────────────────────────
def _alter_users_table() -> tuple[int, int]:
    """
    Add Sprint 1 / Onboarding columns to `users` if they don't already exist.
    Returns (added_count, skipped_count).
    """
    new_columns = [
        # Identity split (was full_name only)
        ("users", "first_name",                "TEXT"),
        ("users", "last_name",                 "TEXT"),
        ("users", "fax",                       "TEXT"),

        # Onboarding journey state
        ("users", "onboarding_status",         "TEXT DEFAULT 'not_started'"),
        ("users", "email_verified_at",         "DATETIME"),
        ("users", "phone_verified_at",         "DATETIME"),

        # Versioned consent (T&C / Privacy) — binder evidence
        ("users", "terms_accepted_version",    "TEXT"),
        ("users", "terms_accepted_at",         "DATETIME"),
        ("users", "privacy_accepted_version",  "TEXT"),
        ("users", "privacy_accepted_at",       "DATETIME"),
    ]

    added = 0
    skipped = 0

    with engine.connect() as conn:
        for table, column, col_def in new_columns:
            sql = f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"[MIGRATE_P6] ✅ Added {table}.{column}")
                added += 1
            except Exception as e:
                msg = str(e).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    print(f"[MIGRATE_P6] ⏩ {table}.{column} already exists — skipped")
                    skipped += 1
                else:
                    print(f"[MIGRATE_P6] ⚠️ Unexpected error for {table}.{column}: {e}")
                    skipped += 1

    return added, skipped


# ─────────────────────────────────────────────────────────────
# STEP 2 — Backfill Organizations from existing Businesses
# ─────────────────────────────────────────────────────────────
def _backfill_organizations() -> int:
    """
    For every existing Business that does NOT yet have a paired
    Organization, create one. Returns the number of Organizations created.

    The pairing is tracked via Organization.legacy_business_id, which
    means this is safely re-runnable: a second invocation finds the
    pairing row already exists and skips.

    Required Organization columns are populated as follows:
      display_name      ← Business.name
      legal_structure   ← inferred from Business.tax_id (or 'osek_morshe' default)
      tax_id            ← Business.tax_id  (may be NULL — backfill placeholder)
      business_address  ← Business.address
      business_phone    ← Business.phone
      legacy_business_id← Business.id

    NOTE: legal_structure default 'osek_morshe' is a SAFE fallback for
    backfill only — real new orgs select it explicitly during onboarding.
    A 9-digit tax_id starting with 5 is reclassified as 'chevra_baam'.
    """
    db = SessionLocal()
    created = 0
    try:
        businesses = db.query(Business).all()
        for biz in businesses:
            existing = (
                db.query(Organization)
                .filter(Organization.legacy_business_id == biz.id)
                .first()
            )
            if existing:
                continue

            # ── Infer legal structure from tax_id when possible ──
            inferred_structure = "osek_morshe"  # safe default
            tax_id_value = (biz.tax_id or "").strip()
            if tax_id_value and tax_id_value.startswith("5") and len(tax_id_value) == 9:
                inferred_structure = "chevra_baam"

            # ── Backfill placeholder tax_id if none on Business ──
            # We use a sentinel format ("BACKFILL-{biz.id}") so the absence
            # of a real tax_id is auditable, not silent. The owner is asked
            # to complete onboarding to replace it with a real, validated
            # tax_id.
            org_tax_id = tax_id_value or f"BACKFILL-{biz.id}"

            org = Organization(
                display_name=biz.name,
                legal_structure=inferred_structure,
                tax_id=org_tax_id,
                tax_id_verified=False,
                business_address=biz.address,
                business_phone=biz.phone,
                kyc_status="pending",
                status=biz.status or "active",
                portal_token=biz.portal_token,
                legacy_business_id=biz.id,
                created_at=biz.created_at or datetime.datetime.utcnow(),
            )
            db.add(org)
            created += 1

        db.commit()
        if created:
            print(f"[MIGRATE_P6] ✅ Backfilled {created} Organization(s) from Business rows")
        else:
            print(f"[MIGRATE_P6] ⏩ All Businesses already have paired Organizations — skipped")
    finally:
        db.close()

    return created


# ─────────────────────────────────────────────────────────────
# STEP 3 — Backfill Memberships from existing Users
# ─────────────────────────────────────────────────────────────
def _backfill_memberships() -> int:
    """
    For every User with role='business_owner' AND business_id NOT NULL,
    create a Membership(role='owner', is_primary=True) connecting them
    to the Organization paired with their legacy Business.

    Returns count of Memberships created.

    Idempotent — re-running skips users who already have a membership
    for that organization (UniqueConstraint on (user_id, organization_id)).
    """
    db = SessionLocal()
    created = 0
    try:
        owners = (
            db.query(User)
            .filter(
                User.role == "business_owner",
                User.business_id.isnot(None),
            )
            .all()
        )

        for user in owners:
            # Find the paired Organization for this user's legacy business.
            org = (
                db.query(Organization)
                .filter(Organization.legacy_business_id == user.business_id)
                .first()
            )
            if not org:
                # Should not happen if step 2 ran successfully; log and skip.
                print(
                    f"[MIGRATE_P6] ⚠️ No Organization paired with business_id="
                    f"{user.business_id} for user {user.email} — skipped"
                )
                continue

            existing = (
                db.query(Membership)
                .filter(
                    Membership.user_id == user.id,
                    Membership.organization_id == org.id,
                )
                .first()
            )
            if existing:
                continue

            membership = Membership(
                user_id=user.id,
                organization_id=org.id,
                role="owner",
                is_primary=True,
                created_at=user.created_at or datetime.datetime.utcnow(),
            )
            db.add(membership)
            created += 1

        db.commit()
        if created:
            print(f"[MIGRATE_P6] ✅ Backfilled {created} Membership(s) (role=owner)")
        else:
            print(f"[MIGRATE_P6] ⏩ All business_owner users already have memberships — skipped")
    finally:
        db.close()

    return created


# ─────────────────────────────────────────────────────────────
# MAIN ENTRY — run_phase6_migrations
# ─────────────────────────────────────────────────────────────
def run_phase6_migrations() -> None:
    """
    Execute Phase 6 migration steps in order.

    Note: the actual CREATE TABLE for organizations / memberships /
    accountant_engagements / invitations is handled by SQLAlchemy's
    create_tables() (called from main.py at startup). This function
    only handles the column additions and backfills.
    """
    print("=" * 60)
    print("[MIGRATE_P6] Phase 6 — Identity Foundation Migration")
    print("=" * 60)

    # Step 1: Add new columns to users
    added_cols, skipped_cols = _alter_users_table()

    # Step 2: Backfill organizations from businesses
    new_orgs = _backfill_organizations()

    # Step 3: Backfill memberships from users
    new_memberships = _backfill_memberships()

    print("-" * 60)
    print(
        f"[MIGRATE_P6] Summary: "
        f"{added_cols} columns added, {skipped_cols} skipped, "
        f"{new_orgs} Organizations backfilled, "
        f"{new_memberships} Memberships backfilled"
    )
    print("=" * 60)


if __name__ == "__main__":
    run_phase6_migrations()
