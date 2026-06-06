"""Aurora LTS — Shared Runtime Secret Loader
=============================================
Canonical, service-agnostic secret helpers for the shared (aurora_shared)
layer. Both M1 (aurora-main-api) and M2 (aurora-api-core) — and the shared
middleware/services they consume — read secrets through these helpers so the
shared layer never has to import a service-local `app.config`.

    require_secret(name)   — REQUIRED secret; crashes in prod if absent / placeholder
    optional_secret(name)  — OPTIONAL value; returns default ("") if unset

Behaviour matches the original M1 implementation:
  • Reads from os.environ (Cloud Run mounts Secret Manager values as env vars).
  • Dev mode (AURORA_RUNTIME != "cloud_run"): failures WARN but don't crash.
  • Prod mode (AURORA_RUNTIME == "cloud_run"): any failure raises RuntimeError.
  • NEVER logs a secret value — only its name and char count.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# Substrings that indicate a value is still a placeholder (case-insensitive).
_PLACEHOLDER_MARKERS = (
    "change-in-production",
    "rotate-me",
    "your_",
    "YOUR_",
    "_here",
    "_HERE",
    "placeholder",
    "example",
    "xxxxxxxx",
)


def _is_prod() -> bool:
    """True when running on Cloud Run (AURORA_RUNTIME=cloud_run)."""
    return os.getenv("AURORA_RUNTIME", "").lower() == "cloud_run"


def require_secret(name: str, *, min_length: int = 32) -> str:
    """Return env var ``name``; crash in prod (warn in dev) if missing/placeholder/short."""
    value = os.getenv(name, "").strip()
    prod = _is_prod()

    if not value:
        msg = (
            f"[AURORA STARTUP] Required secret '{name}' is not set. "
            f"Add it to .env (local dev) or mount it via Secret Manager → Cloud Run (production). "
            f"Generate a value with:  python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
        if prod:
            raise RuntimeError(msg)
        log.warning("%s", msg)
        return ""

    value_lower = value.lower()
    for marker in _PLACEHOLDER_MARKERS:
        if marker.lower() in value_lower:
            msg = (
                f"[AURORA STARTUP] Secret '{name}' still contains a placeholder "
                f"(matched '{marker}'). Replace it with a real value before deploying. "
                f"Generate:  python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
            if prod:
                raise RuntimeError(msg)
            log.warning("%s", msg)
            return value

    if len(value) < min_length:
        msg = (
            f"[AURORA STARTUP] Secret '{name}' is only {len(value)} chars; "
            f"minimum {min_length} chars required for security. "
            f"Generate:  python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
        if prod:
            raise RuntimeError(msg)
        log.warning("%s", msg)

    log.info("[STARTUP] Secret '%s' loaded (%d chars) ✓", name, len(value))
    return value


def optional_secret(name: str, default: str = "") -> str:
    """Return env var ``name``, or ``default`` if unset. No validation applied."""
    return os.getenv(name, default).strip()
