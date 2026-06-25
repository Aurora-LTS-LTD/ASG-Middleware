"""
Aurora LTS — Admin System router (CEO Dashboard v3.0)
=====================================================
System Health / Production-Readiness for the Command Center.

  GET /api/v1/admin/system/health  — live API + DB check, plus each
       integration's MODE (production | sandbox | mock | stub) so the CEO
       can see at a glance what is real vs simulated.
  GET /api/v1/admin/system/config  — non-secret runtime config: backend
       selector flags + version/revision. NEVER returns secrets.

Both are read-only and IAP-gated (require_permission ⇒ require_admin).
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from aurora_shared.database import get_db, User
from aurora_shared.services.permissions import require_permission

router = APIRouter(prefix="/api/v1/admin/system", tags=["admin-system"])

# Backend selector flags surfaced to the dashboard. (Keys only — values are
# safe mode strings like "mock"/"gcs", never credentials.)
_BACKEND_FLAGS = [
    ("ita", "ITA_BACKEND", "mock"),
    ("payplus", "PAYPLUS_BACKEND", "stub"),
    ("otp", "OTP_BACKEND", "stub"),
    ("kyc", "KYC_BACKEND", "stub"),
    ("storage", "STORAGE_BACKEND", "stub"),
    ("audit_bigquery", "AUDIT_BIGQUERY_BACKEND", "stub"),
    ("gemini", "GEMINI_BACKEND", "stub"),
    ("ocr", "OCR_BACKEND", "stub"),
    ("dlp", "DLP_BACKEND", "stub"),
    ("rate_limit", "RATE_LIMIT_BACKEND", "memory"),
]

# Modes considered "live" (green) vs "simulated" (amber) for the status strip.
_LIVE_MODES = {"production", "gcs", "gcp", "bigquery", "payplus", "sendgrid", "twilio", "inforu", "vertex_gemini", "documentai", "dlp", "redis"}
_SIM_MODES = {"mock", "stub", "sandbox", "memory", ""}


def _mode(env: str, default: str) -> str:
    return (os.getenv(env, default) or default).strip().lower()


def _readiness(mode: str) -> str:
    if mode in _LIVE_MODES:
        return "production"
    if mode == "sandbox":
        return "sandbox"
    if mode == "mock":
        return "mock"
    return "stub"


@router.get("/health")
def system_health(
    current_user: User = Depends(require_permission("system", "read")),
    db: Session = Depends(get_db),
) -> dict:
    """Live API + DB health + each integration's configured mode."""
    # DB liveness — real check.
    db_ok = True
    db_error = None
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:  # pragma: no cover
        db_ok = False
        db_error = str(e)[:160]

    services = [{"key": "api", "label": "M1 API", "status": "ok", "mode": "production"}]
    services.append({
        "key": "database", "label": "Database",
        "status": "ok" if db_ok else "error",
        "mode": "production", "error": db_error,
    })
    for key, env, default in _BACKEND_FLAGS:
        mode = _mode(env, default)
        services.append({
            "key": key,
            "label": key.replace("_", " ").title(),
            "mode": mode,
            "readiness": _readiness(mode),
            "status": "ok" if mode in _LIVE_MODES else "simulated",
        })

    any_error = any(s.get("status") == "error" for s in services)
    return {
        "overall": "degraded" if any_error else "ok",
        "services": services,
    }


@router.get("/config")
def system_config(
    current_user: User = Depends(require_permission("system", "read")),
) -> dict:
    """Non-secret runtime config. Strictly whitelisted — no keys/tokens/URLs."""
    flags = {key: _mode(env, default) for key, env, default in _BACKEND_FLAGS}
    return {
        "flags": flags,
        "runtime": os.getenv("AURORA_RUNTIME", "local"),
        "cloud_run_revision": os.getenv("K_REVISION"),
        "cloud_run_service": os.getenv("K_SERVICE"),
        "version": os.getenv("AURORA_APP_VERSION", "v3.0.0-aurora"),
        "step_up_enforced": os.getenv("AURORA_EXEC_REQUIRE_STEP_UP", "0") == "1",
        "autonomous_kill_switch": os.getenv("AURORA_AUTONOMOUS_KILL_SWITCH", "0") == "1",
        # Note: this endpoint deliberately returns ONLY mode flags + version
        # metadata. Secrets (API keys, tokens, DATABASE_URL) are never included.
    }
