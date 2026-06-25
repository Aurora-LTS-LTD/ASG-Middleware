"""
Aurora LTS — Admin Audit router (CEO Dashboard v3.0)
====================================================
Read API over AdminAuditEvent (the structured "who did what" trail written by
every /admin/* mutation). Powers the Audit / Activity module.

  GET /api/v1/admin/audit/events  — filtered, paginated, newest-first.
"""
from __future__ import annotations

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from aurora_shared.database import get_db, User
from aurora_shared.database.models import AdminAuditEvent
from aurora_shared.services.permissions import require_permission

router = APIRouter(prefix="/api/v1/admin/audit", tags=["admin-audit"])


@router.get("/events")
def list_audit_events(
    action: Optional[str] = Query(None, description="exact action, e.g. customer.suspend"),
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    actor_user_id: Optional[int] = Query(None),
    severity: Optional[str] = Query(None, description="info | warning | critical"),
    since: Optional[str] = Query(None, description="ISO8601 lower bound (inclusive)"),
    until: Optional[str] = Query(None, description="ISO8601 upper bound (exclusive)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_permission("audit", "read")),
    db: Session = Depends(get_db),
) -> dict:
    q = db.query(AdminAuditEvent)
    if action:
        q = q.filter(AdminAuditEvent.action == action)
    if entity_type:
        q = q.filter(AdminAuditEvent.entity_type == entity_type)
    if entity_id:
        q = q.filter(AdminAuditEvent.entity_id == str(entity_id))
    if actor_user_id:
        q = q.filter(AdminAuditEvent.actor_user_id == actor_user_id)
    if severity:
        q = q.filter(AdminAuditEvent.severity == severity)

    def _parse(ts: str):
        try:
            return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None

    if since and (d := _parse(since)):
        q = q.filter(AdminAuditEvent.created_at >= d)
    if until and (d := _parse(until)):
        q = q.filter(AdminAuditEvent.created_at < d)

    total = q.with_entities(func.count(AdminAuditEvent.id)).scalar() or 0
    rows = (
        q.order_by(AdminAuditEvent.created_at.desc(), AdminAuditEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    events = [{
        "id": r.id,
        "actor_user_id": r.actor_user_id,
        "actor_role": r.actor_role,
        "action": r.action,
        "entity_type": r.entity_type,
        "entity_id": r.entity_id,
        "before": r.before_json,
        "after": r.after_json,
        "severity": r.severity,
        "device": r.device,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]

    return {"total": total, "page": page, "page_size": page_size, "events": events}
