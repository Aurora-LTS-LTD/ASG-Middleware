"""
Aurora LTS (M1) — one-off DB schema setup + phase migrations.

WHY THIS EXISTS:
  M1 used to run create_tables() + all 18 migrate_phase* migrations on EVERY
  cold start. That is heavy (full create_all reflection + 18 phases) and
  contends on the shared db-f1-micro instance — when a second revision starts
  while the live app + M2 are connected, create_tables() can HANG and stall the
  worker boot (observed during the monorepo canary).

  So schema setup is no longer on the per-boot path. Run it as a DELIBERATE
  one-off (with DB headroom), e.g. a Cloud Run job or pre-deploy step:

      cd services/aurora-main-api && python -m app.db_setup

  The web app skips this on boot by default; for local dev / a fresh DB you can
  opt back into inline boot-time setup with AURORA_RUN_DB_SETUP_ON_BOOT=1.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy.exc import InterfaceError, OperationalError

from aurora_shared.database import create_tables

log = logging.getLogger("aurora.db_setup")

_MAX_ATTEMPTS = int(os.getenv("AURORA_DB_SETUP_MAX_ATTEMPTS", "5"))
_BASE_DELAY_S = float(os.getenv("AURORA_DB_SETUP_BASE_DELAY_S", "2"))


def run_db_setup() -> None:
    """create_tables() + every phase migration. Each phase is non-fatal."""
    create_tables()
    # ── Run Phase 4 DB migrations (adds new columns safely) ──
    try:
        from app.migrate_phase4 import run_phase4_migrations
        run_phase4_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 4 migration warning: {e}")

    # ── Run Phase 5 DB migrations (WhatsApp columns + tables) ──
    try:
        from app.migrate_phase5 import run_phase5_migrations
        run_phase5_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 5 migration warning: {e}")

    # ── Run Phase 6 DB migrations (Identity Foundation: orgs, memberships, etc.) ──
    # Adds onboarding columns to users + creates organizations / memberships /
    # accountant_engagements / invitations tables (the latter via create_tables
    # above) + backfills Organization rows from existing Business rows and
    # Membership rows from existing business_owner Users.
    try:
        from app.migrate_phase6 import run_phase6_migrations
        run_phase6_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 6 migration warning: {e}")

    # ── Run Phase 6b DB migrations (Aurora Onboarding Module) ──
    # The new tables (onboarding_states, otp_verifications, kyc_documents,
    # subscriptions, payment_methods, subscription_payments) are created
    # by create_tables() above. This call is a sanity probe.
    try:
        from app.migrate_phase6b import run_phase6b_migrations
        run_phase6b_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 6b migration warning: {e}")

    # ── Run Phase 7 DB migrations (Sprint 2 — Document AI Receipt Pipeline) ──
    # The new tables (receipts, expenses) are created by create_tables()
    # above. This is a sanity probe + place to hang future column adds.
    try:
        from app.migrate_phase7 import run_phase7_migrations
        run_phase7_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 7 migration warning: {e}")

    # ── Run Phase 8 DB migrations (Sprint 3 — Real ITA Client) ──
    # Adds 4 tracking columns to invoices and creates ita_audit_log.
    try:
        from app.migrate_phase8 import run_phase8_migrations
        run_phase8_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 8 migration warning: {e}")

    # ── Run Phase 9 DB migrations (Sprint 4 — Accountant + Exports) ──
    # Sanity probe for `exports` and `accountant_coa_mappings`.
    try:
        from app.migrate_phase9 import run_phase9_migrations
        run_phase9_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 9 migration warning: {e}")

    # ── Run Phase 10 DB migrations (Sprint 5 — Revenue Engine) ──
    # Sanity probe for revenue_share_ledger / accountant_payouts /
    # accountant_referrals.
    try:
        from app.migrate_phase10 import run_phase10_migrations
        run_phase10_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 10 migration warning: {e}")

    # ── Run Phase 11 DB migrations (Sprint 6 — Hardening) ──
    # Sanity probe for audit_export_cursor + installs SQLAlchemy
    # immutability event-listeners on terminal-state rows.
    try:
        from app.migrate_phase11 import run_phase11_migrations
        run_phase11_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 11 migration warning: {e}")

    # ── Run Phase 12 DB migrations (Sprint 7 — Marketing + v2.0) ──
    # Probes marketing_leads + the v2.0 Virtual Tax Shield tables
    # (tax_obligations, virtual_ledger, virtual_balance,
    #  remittance_links, payment_confirmations).
    try:
        from app.migrate_phase12 import run_phase12_migrations
        run_phase12_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 12 migration warning: {e}")

    # ── Run Phase 13 DB migrations (Track 3 — Break-glass Tier 1.5) ──
    # Probes break_glass_tokens table for the emergency JWT system.
    try:
        from app.migrate_phase13 import run_phase13_migrations
        run_phase13_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 13 migration warning: {e}")

    # ── Run Phase 14 DB migrations (Appendix H — Tier 1 CEO Dashboard) ──
    # Probes vertical_templates + exec_events tables.
    # Optionally seeds starter vertical templates if AURORA_SEED_VERTICAL_TEMPLATES=1.
    try:
        from app.migrate_phase14 import run_phase14_migrations
        run_phase14_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 14 migration warning: {e}")

    # ── Run Phase 15 DB migrations (Appendix I Sprint 2) ──
    # Probes business_categories + ceo_session_snapshots + webauthn_credentials.
    # Adds gcs_file_path/retention/legal_hold/retrieval columns to invoices.
    # Adds organizations.category_id FK with ON DELETE SET NULL.
    # Installs CHECK constraints + partial indexes.
    try:
        from app.migrate_phase15 import run_phase15_migrations
        run_phase15_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 15 migration warning: {e}")

    # ── Run Phase 16 DB migrations (Appendix J Sprint 3 — AI Copilot) ──
    # Probes copilot_conversations + copilot_messages + copilot_provisioning_runs +
    # claude_api_usage tables for the Aurora AI Copilot Console.
    try:
        from app.migrate_phase16 import run_phase16_migrations
        run_phase16_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 16 migration warning: {e}")

    # ── Run Phase 17 DB migrations (Appendix L Sprint 4 — Vertex AI / Gemini) ──
    # Renames claude_api_usage → llm_api_usage (in-place) and adds provider column.
    # Probes new tables gemini_runs + daily_brief_cards.
    # Adds gemini_classification_json + gemini_classified_at columns to receipts.
    try:
        from app.migrate_phase17 import run_phase17_migrations
        run_phase17_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 17 migration warning: {e}")

    # ── Run Phase 18 DB migrations (Appendix M Sprint 5 — Pre-Armed Autonomous) ──
    # Probes 5 new tables: project_constraints, hcarl_policy_states,
    # causal_insights, federated_sync_logs, growth_milestones.
    # Seeds the four canonical GrowthMilestone rows (all locked) on first install.
    try:
        from app.migrate_phase18 import run_phase18_migrations
        run_phase18_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 18 migration warning: {e}")

    # ── Run Phase 19 DB migrations (Appendix M Sprint 6 — Domain cutover) ──
    # Revokes all pre-cutover WebAuthn credentials (bound to old RP_ID
    # admin.aurora-ltd.co.il) so the audit trail reflects retirement rather
    # than silent breakage. Controlled by AURORA_PHASE19_REVOKE_ON_BOOT
    # (default 1); set to 0 for rollback scenarios.
    try:
        from app.migrate_phase19 import run_phase19_migrations
        run_phase19_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 19 migration warning: {e}")

    # ── Run Phase 20 DB migrations (Sprint 8.2 — Aurora Mac Shell) ──
    # Probes native_device_keys + native_handshake_challenges tables
    # (created by SQLAlchemy create_tables() above), sweeps stale
    # challenges older than 24h, and confirms JWT_SIGNING_KEY is set
    # for native session token issuance. Controlled by
    # AURORA_PHASE20_ENABLED (default 1).
    try:
        from app.migrate_phase20 import run_phase20_migrations
        run_phase20_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 20 migration warning: {e}")

    # ── Run Phase 21 DB migrations (Sprint 8.2 sibling — Accountant Portal) ──
    # Probes accountant_devices + accountant_refresh_tokens +
    # accountant_otp_attempts. Sweeps stale OTPs (>1h), stale refresh
    # tokens (>60d), and old revoked devices (>1y).
    try:
        from app.migrate_phase21 import run_phase21_migrations
        run_phase21_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 21 migration warning: {e}")

    # ── Phases 21_vault → 30 (app/migrations/ subpackage) ──────────────
    # These shipped with the P2 feature work but were never wired into the
    # migration runner, leaving production schema-drifted vs the deployed
    # models (e.g. users.firm_name missing → /onboarding/start 500s).
    # Each entrypoint is idempotent (ADD COLUMN / CREATE TABLE IF NOT
    # EXISTS) and safe to re-run under the advisory lock.
    try:
        from app.migrations.migrate_phase21_vault import run_phase21_vault_migrations
        run_phase21_vault_migrations()
    except Exception as e:
        log.error("[db_setup] Phase 21 Vault migration FAILED: %s", e)

    for _phase_mod in (
        "migrate_phase22_sanctions",
        "migrate_phase23_anomaly",
        "migrate_phase24_vat_returns",
        "migrate_phase25_payment_links",
        "migrate_phase26_apns_tokens",
        "migrate_phase27_accountant_password_reset",
        "migrate_phase28_user_firm_name",
        "migrate_phase29_invoice_lifecycle_timestamps",
        "migrate_phase30_audit_export_cursor",
        "migrate_phase31_user_must_change_password",
        "migrate_phase32_v3_command_center",
    ):
        try:
            import importlib
            mod = importlib.import_module(f"app.migrations.{_phase_mod}")
            mod.run()
        except Exception as e:
            log.error("[db_setup] %s migration FAILED: %s", _phase_mod, e)


async def run_db_setup_resilient() -> bool:
    """Bounded-retry wrapper (for the one-off job or opt-in inline boot): ride
    out a transient DB-unready / contention window instead of hanging. Non-fatal
    — returns False if it never completed (caller decides)."""
    last: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            run_db_setup()
            if attempt > 1:
                print(f"[DB_SETUP] completed after {attempt} attempt(s)")
            return True
        except (OperationalError, InterfaceError) as e:
            last = e
            head = str(e).splitlines()[0][:160]
            if attempt < _MAX_ATTEMPTS:
                delay = min(_BASE_DELAY_S * attempt, 10.0)
                print(f"[DB_SETUP][WARN] DB not ready (attempt {attempt}/{_MAX_ATTEMPTS}): {head} — retry in {delay:.1f}s")
                await asyncio.sleep(delay)
            else:
                print(f"[DB_SETUP][WARN] DB not ready (final attempt {attempt}/{_MAX_ATTEMPTS}): {head}")
    head = str(last).splitlines()[0][:160] if last else "unknown"
    print(f"[DB_SETUP][ERROR] DB setup did not complete after {_MAX_ATTEMPTS} attempts. Last error: {head}")
    return False


if __name__ == "__main__":
    import sys

    print("[DB_SETUP] one-off schema setup + phase migrations starting…")
    ok = asyncio.run(run_db_setup_resilient())
    print(f"[DB_SETUP] done (ok={ok})")
    sys.exit(0 if ok else 1)
