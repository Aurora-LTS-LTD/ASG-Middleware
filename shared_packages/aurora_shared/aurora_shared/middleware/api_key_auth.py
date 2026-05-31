"""
Aurora LTS — API Key Authentication (P1-22)
=============================================
Service-to-service authentication for callers without a user identity
(Make.com webhook relays, monitoring probes, future integration
partners). Pair this with rate limiting + an explicit scope check
when adding it to a route.

USAGE:
    from app.middleware.api_key_auth import require_api_key

    @router.post("/api/v1/relay/webhook")
    def handle_webhook(
        payload: dict,
        api_key: ApiKey = Depends(require_api_key(scope="make-webhook")),
    ):
        ...

The dependency reads `X-API-Key`, SHA-256 hashes it, looks up the row,
verifies not revoked, optionally checks scope, updates last_used_at,
and yields the ApiKey row. 401 on any miss; 403 on scope mismatch.

MINTING:
    See scripts/mint_api_key.py — emits a fresh plaintext key + name
    pair (the plaintext is shown ONCE, then never recoverable).
"""
from __future__ import annotations

import datetime
import hashlib
import logging
from typing import Callable, Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.database.models import ApiKey

log = logging.getLogger(__name__)


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def require_api_key(scope: Optional[str] = None) -> Callable:
    """
    Build a FastAPI dependency that validates the X-API-Key header.
    Pass `scope` to require the key's scope column equal that string.
    """

    def _dep(
        x_api_key: str = Header(..., alias="X-API-Key"),
        db: Session = Depends(get_db),
    ) -> ApiKey:
        if not x_api_key or len(x_api_key) < 16:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_api_key",
            )

        key_hash = _sha256_hex(x_api_key)
        row = (
            db.query(ApiKey)
            .filter(ApiKey.key_hash == key_hash)
            .first()
        )

        if row is None:
            log.warning("[api-key] unknown key (hash prefix %s)", key_hash[:8])
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_api_key",
            )

        if row.revoked_at is not None:
            log.warning("[api-key] revoked key used name=%s", row.name)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="api_key_revoked",
            )

        if scope is not None and row.scope != scope:
            log.warning(
                "[api-key] scope mismatch name=%s required=%s actual=%s",
                row.name, scope, row.scope,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="api_key_scope_mismatch",
            )

        # Touch last_used_at — best-effort, never fail the request.
        try:
            row.last_used_at = datetime.datetime.utcnow()
            db.commit()
        except Exception as exc:
            db.rollback()
            log.warning("[api-key] could not update last_used_at: %s", exc)

        return row

    return _dep


__all__ = ["require_api_key"]
