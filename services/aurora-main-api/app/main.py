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
from sqlalchemy.orm import Session

from aurora_shared.database import create_tables, get_db, Business, User, SessionLocal
from aurora_shared.middleware.auth_middleware import get_current_user, require_admin
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
from app.routers.admin_exec import router as admin_exec_router               # Exec telemetry/charts — copilot extracted to aurora-api-core (routers/copilot.py)
# MOVED to aurora-api-core (app.main_core): native_shell + the extracted copilot router
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
# CREATE THE APP
# ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="Aurora LTS API",
    description="AURORA LTS LTD (אורורה אל.טי.אס. בע\"מ) — Smart Business OS for Israeli SMBs",
    version="3.0.0-aurora",
)


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
app.include_router(admin_exec_router)        # Exec telemetry/charts (copilot now on aurora-api-core)
# native_shell + copilot moved to aurora-api-core (app.main_core)
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
    # ── DB schema setup + phase migrations — NO LONGER on every cold start ──
    # create_all + 18 phases is heavy and contends on the shared DB, which can
    # hang the worker on boot. Run it as a deliberate one-off instead:
    #     cd services/aurora-main-api && python -m app.db_setup
    # Opt in to inline boot-time setup (dev / fresh DB) with AURORA_RUN_DB_SETUP_ON_BOOT=1.
    if os.getenv("AURORA_RUN_DB_SETUP_ON_BOOT", "").strip() in ("1", "true", "TRUE"):
        from app.db_setup import run_db_setup_resilient
        await run_db_setup_resilient()
    else:
        print("[STARTUP] DB setup/migrations skipped on boot — run `python -m app.db_setup` (set AURORA_RUN_DB_SETUP_ON_BOOT=1 to run inline).")

    # ── Start WhatsApp outbound-resend worker (always on) ──
    # Safe to run even if Meta creds aren't set — the worker no-ops
    # until is_configured() is true, then starts draining the queue.
    try:
        from app.services.whatsapp_resend import whatsapp_resend_loop
        asyncio.create_task(whatsapp_resend_loop())
        print("[STARTUP] ✅ WhatsApp resend worker started")
    except Exception as e:
        print(f"[STARTUP] ⚠️ WhatsApp resend worker failed to start: {e}")

    # ── Seed default admin user (runs once) ──
    # GUARDED in production via SKIP_SEED_ADMIN=1. The hard-coded
    # "admin@asg.com / admin123" credentials are appropriate ONLY for
    # local development. On Cloud Run the production admin is created
    # via the one-shot scripts/bootstrap_admin.py job (env-driven
    # email + Secret-Manager-issued password).
    if os.getenv("SKIP_SEED_ADMIN", "").strip() in ("1", "true", "TRUE"):
        print("[STARTUP] SKIP_SEED_ADMIN set — skipping default admin seed (production mode)")
    else:
        db = SessionLocal()
        try:
            admin = db.query(User).filter(User.role == "admin").first()
            if not admin:
                from aurora_shared.services.auth_service import hash_password
                admin = User(
                    email="admin@asg.com",
                    password_hash=hash_password("admin123"),
                    full_name="Ibrahim Masarwa",
                    role="admin",
                )
                db.add(admin)
                db.commit()
                print("[STARTUP] Default admin created: admin@asg.com / admin123")
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
# CEO DASHBOARD / UI PAGES — served from the front-end/ layer
# ─────────────────────────────────────────────────────────────
# The cockpit UI (dashboard, onboarding wizard, accountant SPA) moved out of the
# backend into the repo's front-end/ceo-dashboard/ during the monorepo split
# (Phase 2A, bundle-at-build). Resolution order:
#   1. CEO_DASHBOARD_DIR env var (set by the M1 Dockerfile in Phase 4), else
#   2. <repo>/front-end/ceo-dashboard relative to this file (local dev).
# NOTE: until the Phase-4 Dockerfile COPYs front-end/ceo-dashboard into the M1
# image (or sets CEO_DASHBOARD_DIR), a rebuilt M1 container will 404 these pages.
_CEO_DASHBOARD_DIR = os.getenv("CEO_DASHBOARD_DIR") or os.path.abspath(
    # services/aurora-main-api/app/main.py -> repo root is three levels up.
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "front-end", "ceo-dashboard")
)


@app.get("/dashboard")
def serve_dashboard():
    """Serve the admin dashboard HTML page."""
    return FileResponse(os.path.join(_CEO_DASHBOARD_DIR, "dashboard.html"))


@app.get("/onboarding")
def serve_onboarding():
    """Serve the multi-step Aurora onboarding wizard."""
    return FileResponse(os.path.join(_CEO_DASHBOARD_DIR, "onboarding.html"))


@app.get("/accountant")
def serve_accountant_portal():
    """Serve the accountant-portal SPA (separate from /dashboard)."""
    return FileResponse(os.path.join(_CEO_DASHBOARD_DIR, "accountant", "index.html"))


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
    from aurora_shared.services.identity import (
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
