"""
ASG Solutions — Main Application
==================================
This is the entry point of the entire server.
It creates the FastAPI app, registers all routes, and starts serving.

HOW TO START:
  cd ~/asg_platform
  source venv/bin/activate
  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

Then open: http://10.0.0.2:8000/dashboard
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import asyncio
import os
import datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session

from app.database import create_tables, get_db, Business, User, SessionLocal
from app.middleware.auth_middleware import get_current_user, require_admin
from app.middleware.rate_limit import limiter
from app.routers.whatsapp import router as whatsapp_router
from app.routers.invoices import router as invoices_router
from app.routers.auth import router as auth_router
from app.routers.payments import router as payments_router
from app.routers.pdf import router as pdf_router
from app.routers.telegram import router as telegram_router
from app.routers.organizations import router as organizations_router  # Sprint 1 — Identity Foundation
from app.routers.onboarding import router as onboarding_router        # Aurora Onboarding Module / Phase 6b
from app.routers.receipts import router as receipts_router            # Sprint 2 — Document AI Receipt Pipeline
from app.routers.accountant import router as accountant_router        # Sprint 4 — Accountant Channel + Exports
from app.routers.internal import router as internal_router            # Sprint 5 — Cloud Scheduler hooks
from app.routers.admin_compliance import router as admin_compliance_router  # Sprint 6 — DSAR + audit + payouts
from app.routers.marketing import router as marketing_router                 # Sprint 7 — Marketing capture (aurora-ltd.co.il)
from app.routers.admin_break_glass import router as admin_break_glass_router # Track 3 — Break-glass JWT lifecycle (IAP-only)
from app.routers.admin_users import router as admin_users_router             # Track 4 — Admin users + orgs list (feeds aurora-admin-ui)
from app.routers.admin_exec import router as admin_exec_router               # Appendix H — Tier 1 CEO Executive Dashboard backend
from app.routers.native_shell import router as native_shell_router           # Sprint 8.2 — Aurora Mac Shell hardware binding (Phase 20)
from app.routers.accountant_auth import router as accountant_auth_router    # Sprint 8.2 sibling — Accountant Portal auth (Phase 21)


# ─────────────────────────────────────────────────────────────
# LOAD ENVIRONMENT VARIABLES
# ─────────────────────────────────────────────────────────────
# load_dotenv() reads the .env file and makes its values available
# via os.getenv(). This is how we keep secrets (API keys, tokens)
# out of the code.
load_dotenv()


# ─────────────────────────────────────────────────────────────
# RUNTIME HELPERS — Cloud Run vs local dev
# ─────────────────────────────────────────────────────────────
# Cloud Run's filesystem is read-only except for /tmp. We honour two
# env vars that let the same image run on a Mac dev box and on Cloud
# Run without code changes:
#
#   AURORA_RUNTIME      'cloud_run' on GCP, unset locally
#   PDF_STORAGE_PATH    explicit override (wins over the default)
#   KYC_STUB_DIR        explicit override (wins over the default)
#
# Defaults:
#   Cloud Run → /tmp/aurora/{pdfs,kyc_uploads}
#   Local dev → app/static/{pdfs,kyc_uploads}        (legacy paths)
#
# Sprint 2 will replace both with real GCS uploads; until then this
# keeps the founder dev loop simple and the production container safe.
def _is_cloud_runtime() -> bool:
    return os.getenv("AURORA_RUNTIME", "").lower() == "cloud_run"


def _default_pdf_dir() -> str:
    return "/tmp/aurora/pdfs" if _is_cloud_runtime() else "app/static/pdfs"


def _default_kyc_stub_dir() -> str:
    return "/tmp/aurora/kyc_uploads" if _is_cloud_runtime() else "app/static/kyc_uploads"


# ─────────────────────────────────────────────────────────────
# STRUCTURED LOGGING (P1-08)
# ─────────────────────────────────────────────────────────────
# JSON logs to stderr when AURORA_RUNTIME=cloud_run, human-readable
# text when running locally. Includes request_id from P1-07 context.
from app.logging_config import configure_logging
configure_logging()


# ─────────────────────────────────────────────────────────────
# CREATE THE APP
# ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="Aurora LTS API",
    description="AURORA LTS LTD (אורורה אל.טי.אס. בע\"מ) — Smart Business OS for Israeli SMBs",
    version="3.0.0-aurora",
)

# ── Rate limiting (P0-09) ──
# limiter reads RATE_LIMIT_BACKEND: 'memory' (dev) | 'redis' (production).
# RateLimitExceeded → 429 with Retry-After header via slowapi's built-in handler.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── X-Request-ID middleware (P1-07) ──
# Tags every request with a UUID (or trusts the inbound X-Request-ID
# from Cloud Run / Global LB). Echoed back in the response so clients
# can quote it when filing tickets. Exposed via contextvars to
# background tasks + the structured logger.
from app.middleware.request_id import RequestIDMiddleware
app.add_middleware(RequestIDMiddleware)

# ── Global exception handlers (P1-06) ──
# HTTPException re-emitted with request_id attached.
# Uncaught Exception logged with full traceback + request_id, returned
# to client as a safe JSON envelope (no traceback leak).
from app.middleware.error_handlers import register_exception_handlers
register_exception_handlers(app)


# ─────────────────────────────────────────────────────────────
# CORS MIDDLEWARE — production-grade allowlist (Phase 2 SEC-204)
# ─────────────────────────────────────────────────────────────
# CORS = Cross-Origin Resource Sharing. Browsers enforce CORS for
# cross-origin XHR/fetch. Server-to-server traffic (Meta WhatsApp
# webhook, Cloud Scheduler crons) is NOT subject to CORS, so the
# WhatsApp HMAC and X-Aurora-Internal header flows are unaffected
# by this allowlist.
#
# The wildcard previously here (`allow_origins=["*"]` with
# `allow_credentials=True`) was unsafe AND technically rejected by
# modern browsers when credentials are present — see CVE-style
# guidance in OWASP CORS Cheat Sheet. The explicit allowlist below
# is the correct production posture.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://aurora-ltd.co.il",            # marketing apex
        "https://www.aurora-ltd.co.il",        # marketing www subdomain
        "https://app.aurora-ltd.co.il",        # forward-compat: future authenticated SPA
        "https://console.api-aurora-lts.com",  # Executive Cockpit (Appendix M — primary)
        "https://admin.aurora-ltd.co.il",      # legacy admin URL — REMOVE in Appendix M P10 cutover
        # Sprint 8.2.1 — Accountant Portal (Tauri desktop app)
        "https://api-aurora-lts.com",          # API-origin browser fetch from portal web layer
        "https://portal.api-aurora-lts.com",   # portal download/landing page
        "tauri://localhost",                    # Tauri renderer on macOS + Linux
        "https://tauri.localhost",              # Tauri renderer on Windows
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",     # JWT bearer
        "Content-Type",      # JSON POSTs
        "Accept",            # browser defaults
        "Origin",
        "X-Requested-With",
    ],
    max_age=600,  # cache preflight 10 minutes
)


# ─────────────────────────────────────────────────────────────
# REGISTER ROUTERS
# ─────────────────────────────────────────────────────────────
# "include_router" connects each router's endpoints to the app.
# Like adding departments to a company — each router handles
# its own area (WhatsApp, Invoices).
app.include_router(whatsapp_router)
app.include_router(invoices_router)
app.include_router(auth_router)
app.include_router(payments_router)
app.include_router(pdf_router)
app.include_router(telegram_router)
app.include_router(organizations_router)  # Sprint 1 — orgs, memberships, invitations, /me/context
app.include_router(onboarding_router)     # Aurora — multi-step web onboarding wizard
app.include_router(receipts_router)       # Sprint 2 — receipt OCR pipeline + admin review queue
app.include_router(accountant_router)     # Sprint 4 — accountant portal + exports
app.include_router(internal_router)       # Sprint 5 — Cloud Scheduler / internal jobs
app.include_router(admin_compliance_router) # Sprint 6 — DSAR + audit + payouts (admin-only)
app.include_router(marketing_router)         # Sprint 7 — POST /api/v1/marketing/lead (public, anonymous)
app.include_router(admin_break_glass_router) # Track 3 — list + revoke break-glass tokens (IAP-strict)
app.include_router(admin_users_router)       # Track 4 — admin users + orgs list (consumed by aurora-admin-ui)
app.include_router(admin_exec_router)        # Appendix H — Tier 1 CEO Executive Dashboard endpoints
app.include_router(native_shell_router)      # Sprint 8.2 — Aurora Mac Shell handshake + device list/revoke
app.include_router(accountant_auth_router)   # Sprint 8.2 sibling — Accountant Portal OTP + device mgmt


# ─────────────────────────────────────────────────────────────
# STATIC FILES
# ─────────────────────────────────────────────────────────────
# Serve files from the "app/static" folder at the URL "/static".
# This is where the dashboard HTML lives.
app.mount("/static", StaticFiles(directory="app/static"), name="static")


# ─────────────────────────────────────────────────────────────
# STARTUP EVENT
# ─────────────────────────────────────────────────────────────
# This runs once when the server starts. It creates all database
# tables if they don't exist yet.
@app.on_event("startup")
async def startup():
    print("=" * 50)
    print("  ASG Solutions API v2.0.0")
    print("  Starting up...")
    print("=" * 50)

    # ── Validate required secrets before anything else ──
    # In production (AURORA_RUNTIME=cloud_run): raises RuntimeError on the
    # first missing / placeholder secret — Cloud Run will not mark the
    # instance healthy, preventing a broken deploy from serving traffic.
    # In dev mode: logs warnings and continues.
    from app.config.secrets import validate_all_secrets
    validate_all_secrets()

    # ── P1-09/10/11: refuse to boot Cloud Run with stub backends ──
    # ITA / STORAGE / AUDIT_BIGQUERY backends silently no-op when stub.
    # In production that means fake invoice allocations, dropped GCS
    # writes, and discarded audit events — invisible to operators.
    from app.config.backend_check import validate_backend_selectors
    validate_backend_selectors()

    # ── P1-01: serialize migrations across Cloud Run instances ──
    # Postgres advisory lock ensures only one instance runs create_tables()
    # + the 22 phase migrations at a time. Without this, concurrent
    # cold-start ALTER TABLE statements deadlock on AccessExclusiveLock.
    # SQLite path is a no-op (single-process dev).
    from app.db.migration_lock import with_migration_lock
    with with_migration_lock():
        create_tables()
        _run_all_phase_migrations()

    # ── Start WhatsApp outbound-resend worker (always on) ──
    # Safe to run even if Meta creds aren't set — the worker no-ops
    # until is_configured() is true, then starts draining the queue.
    try:
        from app.services.whatsapp_resend import whatsapp_resend_loop
        asyncio.create_task(whatsapp_resend_loop())
        print("[STARTUP] ✅ WhatsApp resend worker started")
    except Exception as e:
        print(f"[STARTUP] ⚠️ WhatsApp resend worker failed to start: {e}")

    # ── Seed default admin user (dev only — never runs on Cloud Run) ──
    # Credentials are read from environment variables so they are NEVER
    # hardcoded in source.  Set in .env for local development:
    #
    #   AURORA_SEED_ADMIN_EMAIL=dev-admin@aurora-lts.local
    #   AURORA_SEED_ADMIN_PASSWORD=<generate with secrets.token_urlsafe(16)>
    #
    # On Cloud Run (AURORA_RUNTIME=cloud_run) this block is skipped
    # entirely regardless of SKIP_SEED_ADMIN.  The production admin is
    # created once via the one-shot scripts/bootstrap_admin.py job.
    _is_cloud_run = os.getenv("AURORA_RUNTIME", "").lower() == "cloud_run"
    _skip_seed = os.getenv("SKIP_SEED_ADMIN", "").strip() in ("1", "true", "TRUE")

    if _is_cloud_run or _skip_seed:
        print("[STARTUP] Admin seed skipped (cloud_run or SKIP_SEED_ADMIN set)")
    else:
        _seed_email = os.getenv("AURORA_SEED_ADMIN_EMAIL", "").strip()
        _seed_password = os.getenv("AURORA_SEED_ADMIN_PASSWORD", "").strip()
        if not _seed_email or not _seed_password:
            print(
                "[STARTUP] Admin seed skipped — set AURORA_SEED_ADMIN_EMAIL and "
                "AURORA_SEED_ADMIN_PASSWORD in .env to auto-create a dev admin."
            )
        else:
            db = SessionLocal()
            try:
                admin = db.query(User).filter(User.role == "admin").first()
                if not admin:
                    from app.services.auth_service import hash_password
                    admin = User(
                        email=_seed_email,
                        password_hash=hash_password(_seed_password),
                        full_name="Dev Admin",
                        role="admin",
                    )
                    db.add(admin)
                    db.commit()
                    print(f"[STARTUP] Dev admin created: {_seed_email}")
                else:
                    print(f"[STARTUP] Admin exists: {admin.email}")
            finally:
                db.close()

    # ── Ensure writable runtime dirs exist ──
    # Cloud Run filesystems are read-only EXCEPT /tmp. So in cloud-run mode
    # we point both PDF + KYC stub writes at /tmp/aurora/{pdfs,kyc_uploads}.
    # In dev mode (default), we keep the legacy app/static/{pdfs,kyc_uploads}
    # paths so URLs like /static/pdfs/... continue to work.
    pdf_dir = os.getenv("PDF_STORAGE_PATH") or _default_pdf_dir()
    kyc_dir = os.getenv("KYC_STUB_DIR") or _default_kyc_stub_dir()
    try:
        os.makedirs(pdf_dir, exist_ok=True)
        print(f"[STARTUP] PDF output dir ready: {pdf_dir}")
    except OSError as e:
        # Non-fatal — PDFs may regenerate on demand. Log + continue.
        print(f"[STARTUP] ⚠️ PDF dir not writable ({pdf_dir}): {e}")
    try:
        os.makedirs(kyc_dir, exist_ok=True)
        print(f"[STARTUP] KYC stub upload dir ready: {kyc_dir}")
    except OSError as e:
        print(f"[STARTUP] ⚠️ KYC stub dir not writable ({kyc_dir}): {e}")

    # ── Initialize Telegram Bot (if token is configured) ──
    # The bot runs as part of this FastAPI process — no separate process needed.
    # Updates arrive via the POST /webhook/telegram/{secret} endpoint.
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if telegram_token:
        try:
            from app.services.telegram_bot import (
                init_application,
                morning_digest_loop,
            )
            from app.services.allocation_queue import allocation_retry_loop

            tg_app = await init_application(telegram_token)
            bot = tg_app.bot

            # ── Start background workers as fire-and-forget asyncio tasks ──
            # These tasks run concurrently with FastAPI's event loop.
            asyncio.create_task(allocation_retry_loop(bot=bot))
            asyncio.create_task(morning_digest_loop(bot=bot))

            print("[STARTUP] ✅ Telegram bot initialized")
            print("[STARTUP] ✅ Allocation retry worker started")
            print("[STARTUP] ✅ Morning digest worker started")
            print("[STARTUP] ℹ️  Register webhook: POST /api/v1/telegram/setup-webhook")
        except Exception as e:
            print(f"[STARTUP] ⚠️ Telegram bot failed to start (non-fatal): {e}")
            print("[STARTUP] Set TELEGRAM_BOT_TOKEN in .env to enable the bot")
    else:
        print("[STARTUP] ⚠️ TELEGRAM_BOT_TOKEN not set — Telegram bot disabled")
        print("[STARTUP] Add TELEGRAM_BOT_TOKEN=your-token to .env to enable")

    print("[STARTUP] Server is ready!")
    print(f"[STARTUP] Dashboard: http://0.0.0.0:8000/dashboard")
    print("=" * 50)


def _run_all_phase_migrations() -> None:
    """
    Run all 22 hand-rolled phase migrations in sequence.

    Extracted from the startup() body so the caller can wrap the entire
    sequence in a single advisory-lock context. Idempotency of each
    individual phase is unchanged (each migrate_phaseN file already uses
    `ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`).
    """
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

    # ── Run Phase 21 Vault DB migrations (Sprint 8.3 — Document Vault) ──
    # Provisions client_documents + vault_ingestion_addresses tables
    # with the 7-year-retention CHECK constraints, builds composite
    # indexes, and backfills a unique 16-hex email-alias token for
    # every Organization that lacks one. Idempotent; safe on every boot.
    try:
        from app.migrations.migrate_phase21_vault import run_phase21_vault_migrations
        run_phase21_vault_migrations()
    except Exception as e:
        print(f"[STARTUP] Phase 21 Vault migration warning: {e}")

    # ── P1-02: Alembic bootstrap or upgrade ──
    # First encounter: stamps the live schema (produced by legacy phases
    # above) as the Alembic baseline — no DDL. Subsequent boots: alembic
    # upgrade head. Already protected by the P1-01 advisory lock above.
    try:
        from app.db.alembic_bootstrap import alembic_bootstrap_or_upgrade
        alembic_bootstrap_or_upgrade()
    except Exception as e:
        print(f"[STARTUP] Alembic bootstrap warning: {e}")


@app.on_event("shutdown")
async def shutdown():
    """Gracefully shut down the Telegram bot when uvicorn stops."""
    try:
        from app.services.telegram_bot import shutdown_application
        await shutdown_application()
        print("[SHUTDOWN] Telegram bot stopped")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────
@app.get("/")
def health_check():
    """Simple health check — if this responds, the server is alive."""
    return {
        "message": "ASG Solutions API is running!",
        "version": "2.0.0",
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────────────────────────
# DASHBOARD PAGE
# ─────────────────────────────────────────────────────────────
@app.get("/dashboard")
def serve_dashboard():
    """Serve the admin dashboard HTML page."""
    return FileResponse("app/static/dashboard.html")


# ─────────────────────────────────────────────────────────────
# AURORA ONBOARDING WIZARD PAGE
# ─────────────────────────────────────────────────────────────
@app.get("/onboarding")
def serve_onboarding():
    """Serve the multi-step Aurora onboarding wizard."""
    return FileResponse("app/static/onboarding.html")


# ─────────────────────────────────────────────────────────────
# ACCOUNTANT PORTAL (Sprint 4)
# ─────────────────────────────────────────────────────────────
@app.get("/accountant")
def serve_accountant_portal():
    """Serve the accountant-portal SPA (separate from /dashboard)."""
    return FileResponse("app/static/accountant/index.html")


# ═══════════════════════════════════════════════════════════════
# MINIMAL BUSINESS ENDPOINTS
# ═══════════════════════════════════════════════════════════════
# We need businesses to exist before we can create invoices.
# These are minimal endpoints — full business management comes later.

class BusinessCreate(BaseModel):
    """What you send to create a new business."""
    name: str
    phone: Optional[str] = None
    business_type: Optional[str] = None
    # Sprint 1.8 — Dual-write Audit:
    # Optional. If provided, the user must exist; we'll auto-create a
    # Membership(role='owner', is_primary=True) on the new Organization
    # paired with this Business. Without it, the Organization is
    # created but ownership remains unassigned (admin can attach later
    # via POST /api/v1/organizations/{id}/invitations).
    owner_user_id: Optional[int] = None


@app.post("/api/v1/businesses")
def create_business(
    payload: BusinessCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Create a new business.

    SPRINT 1.8 DUAL-WRITE BEHAVIOR:
      Every Business row created here ALSO gets a paired Organization
      via get_or_create_organization_for_business(). If owner_user_id
      is provided, we additionally create a Membership(role='owner').
      This keeps the legacy Business table and the new Organization
      table consistent during the expand/contract migration.

    BACKWARDS COMPATIBILITY:
      The response shape is unchanged from prior versions; new fields
      (organization_id, owner_membership_id) are ADDED, not replacing
      existing keys, so existing dashboard JS continues to work.
    """
    # Lazy import to avoid a circular dependency at module load time.
    from app.services.identity import (
        get_or_create_organization_for_business,
        add_membership,
    )

    # ── 1. Create the legacy Business (unchanged from prior behavior) ──
    business = Business(
        name=payload.name,
        phone=payload.phone,
        business_type=payload.business_type,
    )
    db.add(business)
    db.flush()  # populate business.id without committing yet

    # ── 2. DUAL-WRITE: paired Organization ──
    org = get_or_create_organization_for_business(business.id, db)

    # ── 3. Optional: create the owner Membership ──
    membership = None
    if payload.owner_user_id is not None:
        owner = (
            db.query(User)
            .filter(User.id == payload.owner_user_id, User.is_active == True)  # noqa: E712
            .first()
        )
        if not owner:
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail=f"owner_user_id={payload.owner_user_id} not found or inactive",
            )
        membership = add_membership(
            user_id=owner.id,
            organization_id=org.id,
            role="owner",
            invited_by_user_id=current_user.id,
            db=db,
        )
        # Legacy compat: mirror to User.business_id (expand/contract bridge)
        if not owner.business_id:
            owner.business_id = business.id

    db.commit()
    db.refresh(business)
    db.refresh(org)
    print(
        f"[BUSINESS] Created: {business.name} "
        f"(business_id={business.id} → organization_id={org.id})"
    )
    return {
        # Original fields (unchanged shape for backwards-compat)
        "id": business.id,
        "name": business.name,
        "phone": business.phone,
        "business_type": business.business_type,
        "status": business.status,
        "portal_token": business.portal_token,
        "created_at": business.created_at.isoformat(),
        # Sprint 1.8 dual-write additions
        "organization_id": org.id,
        "owner_membership_id": membership.id if membership else None,
    }


@app.get("/api/v1/businesses")
def list_businesses(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all businesses."""
    businesses = db.query(Business).all()
    return [
        {
            "id": b.id,
            "name": b.name,
            "phone": b.phone,
            "business_type": b.business_type,
            "status": b.status,
            "portal_token": b.portal_token,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in businesses
    ]
