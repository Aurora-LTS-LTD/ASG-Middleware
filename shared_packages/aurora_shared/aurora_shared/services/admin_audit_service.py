"""Admin audit — structured before/after record of every CEO-Dashboard mutation.

Every `/api/v1/admin/*` write calls `write_admin_audit_event(...)` so there is
a tamper-evident "who did what to which entity" trail (the Audit module reads
it). Distinct from ActionLog (durable business ops), ItaAuditLog (tax binder)
and ExecEvent (transient alert feed).

The Mac app never writes audit — only the backend does.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from aurora_shared.database.models import AdminAuditEvent

log = logging.getLogger(__name__)


def hash_ip(ip: Optional[str]) -> Optional[str]:
    """SHA-256 of the client IP — we store the hash, never the raw address."""
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


def write_admin_audit_event(
    db: Session,
    *,
    actor: Any = None,
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Any = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    ip: Optional[str] = None,
    device: Optional[str] = None,
    severity: str = "info",
) -> AdminAuditEvent:
    """Record an admin action. `actor` is the authenticated User (or None for
    system). Flushes into the caller's transaction (caller commits)."""
    ev = AdminAuditEvent(
        actor_user_id=getattr(actor, "id", None),
        actor_role=getattr(actor, "role", None),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        before_json=before,
        after_json=after,
        ip_hash=hash_ip(ip),
        device=device,
        severity=severity,
        created_at=datetime.datetime.utcnow(),
    )
    db.add(ev)
    try:
        db.flush()
    except Exception as e:  # never let auditing crash the mutation path
        log.error("[admin_audit] flush failed for action=%s: %s", action, e)
    return ev
