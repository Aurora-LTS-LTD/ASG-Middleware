"""
Aurora LTS — Phase 14 Migration (Appendix H — Tier 1 CEO Executive Dashboard)
==============================================================================

Probes for the two new tables backing the CEO Executive Dashboard:

  • vertical_templates  — playbook library (one row per business vertical)
  • exec_events         — Alert Stream feed (operator-UX, prunable)

Schema is owned by SQLAlchemy `create_tables()` (via the VerticalTemplate
and ExecEvent models in app/database/models.py); this migration's job is
to probe + log + (optionally) seed.

Idempotent. Safe to re-run on every startup.

Seed strategy:
  • If `vertical_templates` is empty AND env `AURORA_SEED_VERTICAL_TEMPLATES=1`,
    insert a minimal starter row per supported business_type so the dashboard
    has data to render on first load.
  • Default OFF in production until the CEO has reviewed the template content.
"""

import json
import os

from sqlalchemy import text

from app.database.connection import engine, SessionLocal


_STARTER_TEMPLATES = [
    {
        "name": "Restaurant — daily ops",
        "business_type": "restaurant",
        "vat_advisory_text": (
            "Restaurants charge 18% VAT on dine-in + delivery. Tips collected "
            "in cash are NOT VAT-able when paid directly to staff. Track "
            "supplier invoices (produce, dairy) for input-VAT recovery."
        ),
    },
    {
        "name": "Garage — service workshop",
        "business_type": "garage",
        "vat_advisory_text": (
            "Parts charged with VAT; labor charged with VAT. Customer-supplied "
            "parts are pass-through (no VAT). Track supplier parts invoices for "
            "input-VAT recovery."
        ),
    },
    {
        "name": "Retail — small shop",
        "business_type": "retail",
        "vat_advisory_text": (
            "Standard 18% VAT on all sales. Exemptions: fresh fruits/vegetables, "
            "long-shelf milk, exported goods. Track POS Z-reports daily."
        ),
    },
    {
        "name": "Contractor — site work",
        "business_type": "contractor",
        "vat_advisory_text": (
            "Construction labor charged with VAT. Materials supplied to the client "
            "billed at cost+VAT. Sub-contractor invoices recoverable as input-VAT. "
            "Hold 5% retention until project close if specified in contract."
        ),
    },
    {
        "name": "Services — generic",
        "business_type": "services",
        "vat_advisory_text": (
            "All professional services charged with 18% VAT. International clients "
            "may qualify for zero-rated export of services (requires ITA pre-ruling)."
        ),
    },
]


def _seed_starter_templates_if_empty() -> None:
    """Insert one starter row per business_type if the table is empty."""
    with SessionLocal() as db:
        existing = db.execute(text("SELECT COUNT(*) FROM vertical_templates")).scalar() or 0
        if existing > 0:
            print(f"[MIGRATE_P14] vertical_templates already has {existing} row(s) — skip seed")
            return

        print(f"[MIGRATE_P14] Seeding {len(_STARTER_TEMPLATES)} starter templates...")
        for t in _STARTER_TEMPLATES:
            db.execute(
                text(
                    """
                    INSERT INTO vertical_templates
                      (name, business_type, locale,
                       whatsapp_opening_flow_json, invoice_preset_json,
                       receipt_categorization_rules_json, vat_advisory_text,
                       is_active)
                    VALUES
                      (:name, :business_type, 'he',
                       :wa_flow, :inv_preset,
                       :rec_rules, :vat,
                       TRUE)
                    """
                ),
                {
                    "name": t["name"],
                    "business_type": t["business_type"],
                    "wa_flow": json.dumps({}),
                    "inv_preset": json.dumps({}),
                    "rec_rules": json.dumps({}),
                    "vat": t["vat_advisory_text"],
                },
            )
        db.commit()
        print(f"[MIGRATE_P14] ✅ Seeded {len(_STARTER_TEMPLATES)} starter templates")


def run_phase14_migrations() -> None:
    print("=" * 60)
    print("[MIGRATE_P14] Phase 14 — CEO Executive Dashboard (Appendix H)")
    print("=" * 60)

    expected = ["vertical_templates", "exec_events"]
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
                    print(f"[MIGRATE_P14] ⚠️ Probe error on {table}: {e}")
                    missing.append(table)
                try:
                    conn.rollback()
                except Exception:
                    pass

    for t in found:
        print(f"[MIGRATE_P14] ✅ {t} present")
    for t in missing:
        print(f"[MIGRATE_P14] ⚠️ {t} MISSING — ensure create_tables() ran first")

    # Optional seed (disabled in production by default).
    if "vertical_templates" in found and os.getenv("AURORA_SEED_VERTICAL_TEMPLATES", "0") == "1":
        try:
            _seed_starter_templates_if_empty()
        except Exception as e:
            print(f"[MIGRATE_P14] seed step failed (non-fatal): {e}")

    print("-" * 60)
    print(f"[MIGRATE_P14] Summary: {len(found)} present, {len(missing)} missing")
    print("=" * 60)


if __name__ == "__main__":
    run_phase14_migrations()
