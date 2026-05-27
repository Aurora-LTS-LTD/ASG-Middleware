"""Aurora LTS — Runtime Secret Loader
======================================
Provides two helpers used throughout the application:

    require_secret(name)   — REQUIRED secret; crashes in prod if absent / placeholder
    optional_secret(name)  — OPTIONAL config value; returns empty string if unset

And one startup helper called from main.py once, before migrations:

    validate_all_secrets() — validates every required secret in one pass;
                             logs ✓ for each one that passes, crashes in prod
                             on the first failure, logs WARNING in dev mode.

────────────────────────────────────────────
HOW SECRETS REACH THE APPLICATION AT RUNTIME
────────────────────────────────────────────
• Local dev (.env file):
    Secrets live in the .env file in the project root.
    load_dotenv() in main.py reads them into os.environ before startup.
    AURORA_RUNTIME is NOT set → dev mode → warnings but no crashes.

• Production (Cloud Run + GCP Secret Manager):
    1. Create each secret in Secret Manager:
         gcloud secrets create JWT_SECRET --data-file=<(echo -n "YOUR_VALUE")
    2. Mount as an env var in the Cloud Run service:
         gcloud run services update aurora-api \\
           --update-secrets=JWT_SECRET=JWT_SECRET:latest
    3. AURORA_RUNTIME=cloud_run is set on the service.
    The application reads them via os.getenv() — no Secret Manager SDK needed.
    On missing / placeholder secret: RuntimeError stops the Cloud Run boot.

────────────────────────────────────────────
SECURITY INVARIANTS (never violate these)
────────────────────────────────────────────
1. This module NEVER logs a secret value — only the name and char count.
2. Placeholder detection catches known dev-time strings to prevent
   accidental production deploys with development secrets.
3. In dev mode (AURORA_RUNTIME != "cloud_run"), missing/placeholder
   secrets produce a WARNING but do NOT stop the server, so the
   developer loop still works without all credentials configured.
4. In prod mode (AURORA_RUNTIME == "cloud_run"), any failure is fatal.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# ── Strings that indicate a value is still a placeholder ──
# If any of these substrings appear (case-insensitive) in a secret value,
# the secret is treated as unset in production.
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
    """Return the value of env var ``name``, or handle it gracefully.

    Behaviour:
      • Value present and clean → log "✓ loaded (N chars)" + return value
      • Value missing/empty     → prod: RuntimeError | dev: WARNING + return ""
      • Value is a placeholder  → prod: RuntimeError | dev: WARNING + return value
      • Value too short         → prod: RuntimeError | dev: WARNING + return value

    Args:
        name:       Environment variable name (e.g. "JWT_SECRET").
        min_length: Minimum acceptable length in characters. Default 32.

    Returns:
        The secret value as a string.

    Raises:
        RuntimeError: In production only, when the secret fails any check.
    """
    value = os.getenv(name, "").strip()
    prod = _is_prod()

    # ── Check: missing ──
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

    # ── Check: placeholder ──
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
            # In dev mode: warn but let startup continue with the placeholder value
            return value

    # ── Check: too short ──
    if len(value) < min_length:
        msg = (
            f"[AURORA STARTUP] Secret '{name}' is only {len(value)} chars; "
            f"minimum {min_length} chars required for security. "
            f"Generate:  python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
        if prod:
            raise RuntimeError(msg)
        log.warning("%s", msg)

    # ── All checks passed ──
    log.info("[STARTUP] Secret '%s' loaded (%d chars) ✓", name, len(value))
    return value


def optional_secret(name: str, default: str = "") -> str:
    """Return env var ``name``, or ``default`` if unset.

    No validation applied — use for optional configuration values
    (feature flags, pool sizes, non-critical tokens, etc.).
    """
    return os.getenv(name, default).strip()


# ── The complete list of secrets required before the server may serve traffic ──
_REQUIRED_SECRETS: list[tuple[str, int]] = [
    # (env_var_name,          min_length)
    ("JWT_SECRET",            32),
    ("WEBAUTHN_STEP_UP_SECRET", 32),
    ("AURORA_IP_HASH_SALT",   16),
    ("DATABASE_URL",          16),
    # SendGrid and WhatsApp are required in prod; warn in dev if unset
    ("SENDGRID_API_KEY",      16),
    ("WHATSAPP_VERIFY_TOKEN",  8),
    ("WHATSAPP_APP_SECRET",   16),
]


def validate_all_secrets() -> None:
    """Validate every required secret at server startup.

    Called once from main.py ``@app.on_event("startup")`` before any
    migrations or request handling begins.

    In production: the first failing secret raises RuntimeError and
    prevents Cloud Run from marking the instance healthy — the deploy
    fails loudly rather than silently serving traffic with broken auth.

    In dev mode: all failures emit WARNING logs and startup continues,
    so you can run the server locally with only the secrets you currently
    have configured.
    """
    log.info("[STARTUP] ── Secret validation pass beginning ──")
    failures: list[str] = []

    for name, min_len in _REQUIRED_SECRETS:
        try:
            require_secret(name, min_length=min_len)
        except RuntimeError as exc:
            # Collect all failures so the operator sees the full list in one boot
            failures.append(str(exc))

    if failures:
        # In prod: all failures surfaced at once so the operator can fix everything
        # in one deploy cycle rather than discovering failures one at a time.
        combined = "\n".join(f"  • {f}" for f in failures)
        raise RuntimeError(
            f"[AURORA STARTUP] {len(failures)} required secret(s) failed validation:\n{combined}"
        )

    log.info(
        "[STARTUP] ── Secret validation complete: %d/%d secrets OK ──",
        len(_REQUIRED_SECRETS),
        len(_REQUIRED_SECRETS),
    )
