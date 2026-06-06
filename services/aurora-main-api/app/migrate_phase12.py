"""
Aurora LTS — Phase 12 Migration (Sprint 7 — Marketing Capture + v2.0 Virtual Tax Shield)
=========================================================================================

Adds the foundation for the v2.0 "data + calculations only" pivot:
  - marketing_leads          — waitlist signups from aurora-ltd.co.il
  - tax_obligations          — projected per-period liabilities (advance, annual, NI, VAT, pension)
  - virtual_ledger           — append-only ledger of liability accruals + remittance events
  - virtual_balance          — denormalised per-org snapshot
  - remittance_links         — pre-filled gov.il payment URLs
  - payment_confirmations    — user-side "I paid" acknowledgements

DESIGN NOTES:
  - All tenant-scoped tables (everything except marketing_leads) carry
    organization_id FK and are RLS-ready for Phase 3 of the security
    roadmap.
  - All NEW tables are created by SQLAlchemy `create_tables()` since
    they are declared in app/database/models.py — this migration's
    job is to PROBE for existence + log status, install conditional
    immutability guards for virtual_ledger / paid-state rows, and
    surface any failure for ops review.
  - Idempotent: safe to re-run on every startup.

WHY VIRTUAL, NOT REAL:
  v1.0 designed an Escrow Trust Pool — Aurora would hold customer
  funds in a segregated bank account, earn float, remit to the ITA
  at quarter-end. That required CMA + Trust Services + Money Services
  licensing in Israel (6–18 month track).
  v2.0 pivots to data-only: Aurora calculates what the user owes and
  sends them a one-click link to pay the ITA directly. No money held,
  no licensing. This migration is the schema for that.
"""

from sqlalchemy import text
from aurora_shared.database.connection import engine


# ─────────────────────────────────────────────────────────────
# Tables this migration verifies exist
# ─────────────────────────────────────────────────────────────
_EXPECTED_TABLES = [
    "marketing_leads",
    "tax_obligations",
    "virtual_ledger",
    "virtual_balance",
    "remittance_links",
    "payment_confirmations",
]


def run_phase12_migrations() -> None:
    print("=" * 60)
    print("[MIGRATE_P12] Phase 12 — Marketing + v2.0 Virtual Tax Shield")
    print("=" * 60)

    found, missing = [], []
    with engine.connect() as conn:
        for table in _EXPECTED_TABLES:
            try:
                # Use parameterised-safe LIMIT 1 probe.
                conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
                conn.commit()
                found.append(table)
            except Exception as e:
                msg = str(e).lower()
                if "no such table" in msg or "does not exist" in msg:
                    missing.append(table)
                else:
                    # Some other probe error — log and treat as missing
                    # so create_tables() will be expected to handle it.
                    print(f"[MIGRATE_P12] ⚠️ Probe error on {table}: {e}")
                    missing.append(table)
                try:
                    conn.rollback()
                except Exception:
                    pass

    for t in found:
        print(f"[MIGRATE_P12] ✅ {t} present")
    for t in missing:
        print(f"[MIGRATE_P12] ⚠️ {t} MISSING — ensure create_tables() ran first")

    # ── Hook in immutability guards for the v2.0 ledger tables ──
    # The existing install_immutability_guards() will pick up the
    # new VirtualLedger and PaymentConfirmation models once they
    # are imported by the model registry. If the guards module
    # has been extended for v2.0 it will protect them automatically.
    try:
        from app.services.compliance.immutability import install_immutability_guards
        install_immutability_guards()
        print("[MIGRATE_P12] ✅ Immutability guards re-applied (covers v2.0 tables if registered)")
    except Exception as e:
        print(f"[MIGRATE_P12] ⚠️ Immutability guard re-apply failed: {e}")

    print("-" * 60)
    print(f"[MIGRATE_P12] Summary: {len(found)} present, {len(missing)} missing")
    print("=" * 60)


if __name__ == "__main__":
    run_phase12_migrations()
