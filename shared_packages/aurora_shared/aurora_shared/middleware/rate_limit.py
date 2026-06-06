"""
Aurora LTS — Central Rate Limiter
===================================
All routers import `limiter` from here. This avoids the circular-import
that would occur if the Limiter lived in main.py (main → router → main).

BACKENDS:
  RATE_LIMIT_BACKEND=memory  (default)
    In-process dict. Zero dependencies. Resets on restart.
    Correct for local dev and single-instance deployments.
    NOT suitable for multi-instance Cloud Run (each instance has its own
    counter — a bot cycling across instances bypasses per-instance limits).

  RATE_LIMIT_BACKEND=redis
    Shared Redis counter via REDIS_URL. Correct for multi-instance Cloud Run.
    Requires a managed Redis instance (Cloud Memorystore recommended).
    If Redis is unreachable at startup, falls back to memory with a WARNING —
    the app never crashes due to Redis being down (fail-open).

IP RESOLUTION:
  Behind Cloud Run's Global Load Balancer, request.client.host is always
  the LB's internal IP — all clients look identical. X-Forwarded-For carries
  the real client IP, which the LB sets and signs. _get_real_ip() reads it.
"""

import logging
import os

from slowapi import Limiter
from slowapi.util import get_remote_address

log = logging.getLogger(__name__)


def _get_real_ip(request) -> str:
    """
    Extract the true client IP.
    Cloud Run Global LB sets X-Forwarded-For; local dev does not.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return get_remote_address(request)


def _storage_uri() -> str:
    backend = (os.getenv("RATE_LIMIT_BACKEND") or "memory").strip().lower()
    if backend == "redis":
        uri = (os.getenv("REDIS_URL") or "redis://localhost:6379").strip()
        log.info("[rate_limit] Redis backend: %s", uri)
        return uri
    log.info("[rate_limit] In-memory backend (single-instance only)")
    return "memory://"


def _make_limiter() -> Limiter:
    try:
        return Limiter(key_func=_get_real_ip, storage_uri=_storage_uri())
    except Exception as exc:
        log.warning(
            "[rate_limit] Could not initialise configured backend (%s) — "
            "falling back to in-memory. Per-instance limits only.", exc
        )
        return Limiter(key_func=_get_real_ip, storage_uri="memory://")


limiter = _make_limiter()
