"""
Aurora LTS — Secret Manager Wrapper
======================================
Sprint 3 — central, cached, dialect-aware accessor for secrets.

Two backends behind one shape:

  SECRET_BACKEND='env' (default — local dev)
    - Reads from os.getenv(name)
    - Convenient for the founder's laptop and stub modes
    - Zero GCP dependency

  SECRET_BACKEND='gcp' (production — Cloud Run + Secret Manager)
    - Reads from Cloud Secret Manager via google-cloud-secret-manager
    - Lazy SDK import — never loaded in env mode
    - Caches results for SECRET_TTL_SECONDS (default 300 = 5 min)
    - Cache dropped on env-flag flip (process restart) or rotate_secret()

WHY CACHE:
  Secret Manager calls are ~50ms; reading on every webhook would add
  noticeable tail latency. 5-minute TTL is a reasonable compromise
  between freshness and cost.

WHY ENV-MODE FALLBACK:
  Cloud Run automatically materialises Secret Manager secrets as env
  vars when --set-secrets= is used. So most code can call os.getenv()
  directly. THIS wrapper is only needed when:
    - we want to read a secret on demand (not at process boot)
    - we want to support runtime rotation without redeploy
    - we want a single audit-log point for secret access

USAGE:
    from app.services.gcp.secrets import get_secret

    private_key = get_secret("AURORA_ITA_PRIVATE_KEY")
    api_key     = get_secret("AURORA_PAYPLUS_API_KEY", default="")
"""

import os
import time
from typing import Optional


SECRET_BACKEND = (os.getenv("SECRET_BACKEND") or "env").strip().lower()
SECRET_TTL_SECONDS = int(os.getenv("SECRET_TTL_SECONDS", "300"))


# ─────────────────────────────────────────────────────────────
# In-process cache: {secret_name → (value, fetched_at_unix_ts)}
# ─────────────────────────────────────────────────────────────
_cache: dict[str, tuple[str, float]] = {}


def _project_id() -> Optional[str]:
    """GCP project ID — Cloud Run sets this automatically."""
    return os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")


def _cache_get(name: str) -> Optional[str]:
    entry = _cache.get(name)
    if not entry:
        return None
    value, fetched_at = entry
    if (time.time() - fetched_at) > SECRET_TTL_SECONDS:
        # Expired — drop it
        _cache.pop(name, None)
        return None
    return value


def _cache_put(name: str, value: str) -> None:
    _cache[name] = (value, time.time())


# ─────────────────────────────────────────────────────────────
# Public — get_secret
# ─────────────────────────────────────────────────────────────
def get_secret(
    name: str,
    *,
    default: Optional[str] = None,
    refresh: bool = False,
) -> Optional[str]:
    """
    Fetch a secret value by name. Returns `default` if not found.

    `name` is the SECRET resource name (without project prefix). The
    GCP backend looks up `projects/{project}/secrets/{name}/versions/latest`.

    `refresh=True` bypasses the in-process cache and forces a re-read.
    """
    if not name:
        return default

    if not refresh:
        cached = _cache_get(name)
        if cached is not None:
            return cached

    if SECRET_BACKEND == "env":
        value = os.getenv(name)
        if value is None:
            return default
        _cache_put(name, value)
        return value

    if SECRET_BACKEND == "gcp":
        try:
            value = _gcp_fetch(name)
        except Exception as e:
            print(f"[SECRETS] ⚠️ Failed to fetch {name!r} from GCP: {e}")
            # Soft-fall back to env so a transient Secret Manager outage
            # doesn't black-out the whole service.
            value = os.getenv(name)
        if value is None:
            return default
        _cache_put(name, value)
        return value

    raise ValueError(f"Unknown SECRET_BACKEND={SECRET_BACKEND!r}")


def _gcp_fetch(name: str) -> Optional[str]:
    """Real Secret Manager call. Lazy SDK import."""
    from google.cloud import secretmanager  # type: ignore

    project = _project_id()
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is unset; cannot fetch secret")

    client = secretmanager.SecretManagerServiceClient()
    resource = f"projects/{project}/secrets/{name}/versions/latest"
    response = client.access_secret_version(request={"name": resource})
    return response.payload.data.decode("utf-8")


# ─────────────────────────────────────────────────────────────
# Public — invalidate / rotate
# ─────────────────────────────────────────────────────────────
def invalidate_secret(name: str) -> None:
    """
    Drop the cached value for `name` so the next get_secret() call
    refetches. Used by the admin rotate endpoint after a new version
    is added to Secret Manager.
    """
    _cache.pop(name, None)


def invalidate_all() -> None:
    """Drop every cached secret. Useful after a credential rotation event."""
    _cache.clear()
