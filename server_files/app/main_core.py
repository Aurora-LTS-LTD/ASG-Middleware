"""
Aurora LTS — Operational / AI Core Server  (aurora-api-core)
=============================================================
Second Cloud Run service in the twin-engine architecture. Mounts ONLY
the "Model 2" surface (the AI / operational core) and SHARES the same
database models as the tax/compliance server (aurora-api-tax) via
`app.database` — there is exactly one schema, so there is no drift.

WHAT THIS SERVER MOUNTS:
  • admin_exec        — Executive Dashboard router that CONTAINS the Gemini
                        Copilot endpoints (/api/v1/admin/exec/copilot/*).
                        NOTE: Copilot is NOT a standalone router — it is
                        embedded in admin_exec.py alongside the FULL exec
                        dashboard, so mounting "the Copilot" mounts the entire
                        exec/admin surface. Extract it for a truly lean core.
  • native_shell      — Aurora Mac Shell hardware-binding handshake + revoke.
  • admin_break_glass — emergency break-glass token list/revoke (IAP-strict).
  • auth              — shared token verify/refresh (infra; removable if every
                        token is minted by aurora-api-tax).

WHAT THIS SERVER DOES **NOT** DO:
  It does NOT disable the production-readiness check. The original brief
  asked to "strip validate_backend_selectors so the core can boot on
  stubs/mocks." That function did not exist in the codebase, and removing
  the equivalent safety would let this service write FAKE Israel Tax
  Authority allocation numbers and a STUBBED audit trail into the shared
  PRODUCTION database. Instead, the check is implemented below and FAILS
  CLOSED. If you consciously need to boot on stubs in a NON-production
  environment, set CORE_ALLOW_STUBBED_BACKENDS=1 — an explicit, auditable
  opt-in. It is intentionally NOT set for you, and must never be set on a
  service that points at the production DATABASE_URL.
"""

import datetime
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import create_tables
from app.routers.admin_exec import router as admin_exec_router               # Model 2 — Gemini Copilot lives here (whole exec dashboard)
from app.routers.native_shell import router as native_shell_router           # Model 2 — Aurora Mac Shell hardware-binding handshake
from app.routers.admin_break_glass import router as admin_break_glass_router # Model 2 (moved) — emergency token list/revoke (IAP-strict)
from app.routers.auth import router as auth_router                           # shared infra — token verify/refresh (removable)

load_dotenv()


# ─────────────────────────────────────────────────────────────
# PRODUCTION-READINESS GATE  (the safe version of the asked-for
# "validate_backend_selectors")
# ─────────────────────────────────────────────────────────────
# Compliance-critical selectors must NOT run in stub/mock mode when the
# service is a real Cloud Run instance, because this process shares the
# production database. Fake ITA allocation numbers and a stubbed audit
# trail are regulatory hazards, not test conveniences.
_COMPLIANCE_CRITICAL = {
    "ITA_BACKEND": {"mock"},                 # 'production' is the safe value
    "AUDIT_BIGQUERY_BACKEND": {"stub"},      # 'gcp' is the safe value
}


def validate_backend_selectors() -> None:
    """Fail closed if compliance backends are stubbed in a cloud runtime."""
    is_cloud = os.getenv("AURORA_RUNTIME", "").lower() == "cloud_run"
    allow_stub = os.getenv("CORE_ALLOW_STUBBED_BACKENDS", "").strip() in ("1", "true", "TRUE")

    offenders = {
        var: os.getenv(var, "").strip().lower()
        for var, bad in _COMPLIANCE_CRITICAL.items()
        if os.getenv(var, "").strip().lower() in bad
    }
    if not offenders:
        return

    if is_cloud and not allow_stub:
        raise RuntimeError(
            "REFUSING TO BOOT: compliance backends are stubbed in a cloud "
            f"runtime ({offenders}). Set each to its real backend "
            "(ITA_BACKEND=production, AUDIT_BIGQUERY_BACKEND=gcp), OR point "
            "this service at a NON-production database and set "
            "CORE_ALLOW_STUBBED_BACKENDS=1 to opt in explicitly."
        )
    if offenders:
        print(
            f"[CORE][WARN] booting with stubbed compliance backends {offenders} "
            f"(cloud={is_cloud}, explicit_opt_in={allow_stub}). "
            "This MUST NOT touch the production database."
        )


app = FastAPI(
    title="Aurora LTS — Operational Core API",
    description="Model 2 — Gemini Copilot + receipt AI. Shares the Aurora schema.",
    version="3.0.0-core",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://console.api-aurora-lts.com",
        "https://app.aurora-ltd.co.il",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
    max_age=600,
)

app.include_router(auth_router)                  # kept: token verify/refresh; drop if tokens are only minted by aurora-api-tax
app.include_router(native_shell_router)          # Aurora Mac Shell handshake + device revoke
app.include_router(admin_break_glass_router)     # emergency break-glass token list/revoke (IAP-strict)
app.include_router(admin_exec_router)            # CEO Executive Dashboard + embedded Gemini Copilot


@app.on_event("startup")
async def startup():
    print("=" * 50)
    print("  Aurora LTS — Operational Core (aurora-api-core)")
    print("=" * 50)
    validate_backend_selectors()   # fail closed before we touch the shared DB
    create_tables()
    print("[CORE] ready")


@app.get("/")
def health_check():
    return {
        "service": "aurora-api-core",
        "status": "ok",
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }


@app.get("/api/v1/core/health")
def core_health():
    """Named health endpoint the Founder's Cockpit polls for the M2 dot.

    Public + unauthenticated (mirrors the tax server's
    /api/v1/onboarding/health). Reports the compliance-selector posture so
    the cockpit can show *why* a dot is amber even when the process is up.
    """
    ita = os.getenv("ITA_BACKEND", "mock").strip().lower()
    audit = os.getenv("AUDIT_BIGQUERY_BACKEND", "stub").strip().lower()
    stubbed = ita == "mock" or audit == "stub"
    return {
        "service": "aurora-api-core",
        "status": "ok",
        "compliance_backends": "stubbed" if stubbed else "live",
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }
