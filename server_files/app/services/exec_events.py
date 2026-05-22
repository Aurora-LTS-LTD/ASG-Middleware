"""
Aurora LTS — ExecEvent Publisher (Appendix H Tier 1)
=====================================================

Single-source publisher used by every service that wants to emit an
operator-visible event to the CEO Executive Dashboard's Alert Stream.

Contract:
    from app.services.exec_events import publish_exec_event
    publish_exec_event(
        db,
        kind="invoice_finalized",
        severity="info",          # "info" | "warning" | "critical"
        title="Invoice INV-00042 finalized (₪5,200)",
        detail="org=acme org_id=12 amount_net=5200 invoice_id=42",
        related_entity_type="invoice",
        related_entity_id=42,
    )

The publisher is INTENTIONALLY DEFENSIVE:
    • All exceptions are caught and logged at WARNING.
    • An event publish failure NEVER propagates back to the caller.
      The Alert Stream is operator UX, not a control path; an outage
      of the dashboard feed must not break the FSM.

Durability:
    • Writes to the `exec_events` Postgres table (durable, queryable).
    • A separate in-memory ring buffer is maintained per-process for
      sub-second SSE delivery without paying a DB round-trip per poll.
      The buffer is best-effort; the DB row is the source of truth.

Pruning:
    • A Cloud Scheduler cron (separate, in routers/internal.py) deletes
      rows older than 30 days. This module never deletes.
"""

from __future__ import annotations

import logging
import threading
import datetime
from collections import deque
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session

from app.database.models import ExecEvent

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# In-memory ring buffer
# ─────────────────────────────────────────────────────────────
# Holds the most recent N events as dicts for fast SSE polling.
# Per-process — not shared across Cloud Run instances. That's
# acceptable for Tier 1 (single-instance, single-CEO traffic).
# When traffic warrants Memorystore pub/sub (Tier 1.5), this
# buffer becomes the local mirror of a Redis stream.
_RING_CAPACITY = 200
_ring: "deque[Dict[str, Any]]" = deque(maxlen=_RING_CAPACITY)
_ring_lock = threading.Lock()


def _to_dict(ev: ExecEvent) -> Dict[str, Any]:
    return {
        "id": ev.id,
        "kind": ev.kind,
        "severity": ev.severity,
        "title": ev.title,
        "detail": ev.detail,
        "related_entity_type": ev.related_entity_type,
        "related_entity_id": ev.related_entity_id,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
    }


def publish_exec_event(
    db: Session,
    *,
    kind: str,
    title: str,
    severity: str = "info",
    detail: Optional[str] = None,
    related_entity_type: Optional[str] = None,
    related_entity_id: Optional[int] = None,
) -> Optional[int]:
    """
    Persist an ExecEvent and push to the in-memory ring buffer.

    Returns the new event's id on success, or None on any failure
    (defensive — never raises).
    """
    if severity not in ("info", "warning", "critical"):
        severity = "info"

    try:
        ev = ExecEvent(
            kind=kind[:50],
            severity=severity,
            title=title[:200],
            detail=detail,
            related_entity_type=(related_entity_type or None) and related_entity_type[:40],
            related_entity_id=related_entity_id,
            created_at=datetime.datetime.utcnow(),
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)

        with _ring_lock:
            _ring.append(_to_dict(ev))

        return ev.id
    except Exception as e:
        # NEVER let an event-publish failure break the calling flow.
        try:
            db.rollback()
        except Exception:
            pass
        log.warning(
            "[exec_events.publish] failed (non-fatal): kind=%s severity=%s err=%s: %s",
            kind, severity, type(e).__name__, str(e)[:200],
        )
        return None


def recent_events_since(
    db: Session,
    since_id: int = 0,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Return events with id > since_id (ascending) up to `limit`.

    Strategy:
      1. Try the ring buffer first (zero DB cost).
      2. If the ring's lowest id > since_id (i.e., we missed events),
         fall through to a DB query — never miss an event.

    Used by:
      • GET /api/v1/admin/exec/events?since=<cursor>
      • aurora-admin-ui's SSE poller (every ~2s)
    """
    # Ring fast path
    with _ring_lock:
        ring_snapshot = list(_ring)

    if ring_snapshot:
        ring_min_id = ring_snapshot[0]["id"]
        if since_id >= ring_min_id - 1:
            # Ring covers the window — slice from it directly.
            return [e for e in ring_snapshot if e["id"] > since_id][:limit]

    # DB fallback (cold start, or window exceeded ring capacity)
    try:
        rows = (
            db.query(ExecEvent)
            .filter(ExecEvent.id > since_id)
            .order_by(ExecEvent.id.asc())
            .limit(limit)
            .all()
        )
        return [_to_dict(r) for r in rows]
    except Exception as e:
        log.warning("[exec_events.recent_events_since] DB query failed: %s", e)
        return []


def warm_ring_from_db(db: Session) -> int:
    """
    Best-effort: hydrate the ring buffer from the DB at process start.
    Called once from the FastAPI lifespan / startup hook.
    Returns the number of events loaded.
    """
    try:
        rows = (
            db.query(ExecEvent)
            .order_by(ExecEvent.id.desc())
            .limit(_RING_CAPACITY)
            .all()
        )
        rows.reverse()  # back to ascending order for the ring
        with _ring_lock:
            _ring.clear()
            for r in rows:
                _ring.append(_to_dict(r))
        return len(rows)
    except Exception as e:
        log.warning("[exec_events.warm_ring_from_db] failed (non-fatal): %s", e)
        return 0
