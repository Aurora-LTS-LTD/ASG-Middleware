"""
Aurora LTS — Anomaly Detection Router  (P2-20)

Endpoints
─────────
  POST /api/v1/admin/anomaly/run
       Trigger a full anomaly scan (Cloud Scheduler + manual).
       Returns the AnomalyReport summary.  Admin-only.

  GET  /api/v1/admin/anomaly/events
       List open / unresolved anomaly events with filter options.

  PATCH /api/v1/admin/anomaly/events/{id}
        Acknowledge, mark false positive, or escalate an event.

  GET  /api/v1/admin/anomaly/stats
       High-level stats: signals by severity, top signal types, last run.
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from aurora_shared.database import get_db, ActionLog
from aurora_shared.database.models import AnomalyEvent
from aurora_shared.middleware.auth_middleware import require_admin
from app.services.compliance.anomaly_detection import run_daily_scan

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/anomaly", tags=["anomaly"])


# ─────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────

class ResolveRequest(BaseModel):
    status: str = Field(..., pattern="^(acknowledged|false_positive|escalated)$")
    resolution_note: Optional[str] = Field(None, max_length=500)


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────

@router.post("/run", summary="Trigger anomaly scan")
async def trigger_scan(
    background_tasks: BackgroundTasks,
    lookback_days: int = Query(30, ge=7, le=90),
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
) -> dict:
    """
    Run the daily anomaly scan.  Returns immediately with a job-queued
    acknowledgement and runs the heavy scan in a FastAPI BackgroundTask
    so the HTTP response is fast (~50ms).
    """
    log.info("Anomaly scan triggered by admin %d", current_user.id)

    def _run() -> None:
        from aurora_shared.database.connection import SessionLocal
        with SessionLocal() as bg_db:
            report = run_daily_scan(bg_db, lookback_days=lookback_days)
            log.info(
                "Anomaly scan complete: businesses=%d signals=%d",
                report.businesses_scanned,
                report.signals_found,
            )

    background_tasks.add_task(_run)
    return {
        "status": "queued",
        "lookback_days": lookback_days,
        "message": "Scan is running in the background. Results will appear in /events.",
    }


@router.get("/events", summary="List anomaly events")
async def list_events(
    status: Optional[str] = Query(None, description="open|acknowledged|false_positive|escalated"),
    severity: Optional[str] = Query(None, description="low|medium|high|critical"),
    signal_type: Optional[str] = Query(None),
    business_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> dict:
    q = db.query(AnomalyEvent)
    if status:
        q = q.filter(AnomalyEvent.status == status)
    else:
        q = q.filter(AnomalyEvent.status == "open")
    if severity:
        q = q.filter(AnomalyEvent.severity == severity)
    if signal_type:
        q = q.filter(AnomalyEvent.signal_type == signal_type)
    if business_id:
        q = q.filter(AnomalyEvent.business_id == business_id)

    total = q.count()
    events = (
        q.order_by(AnomalyEvent.score.desc(), AnomalyEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "events": [
            {
                "id": e.id,
                "business_id": e.business_id,
                "invoice_id": e.invoice_id,
                "signal_type": e.signal_type,
                "severity": e.severity,
                "score": e.score,
                "description": e.description,
                "status": e.status,
                "created_at": e.created_at.isoformat(),
                "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
            }
            for e in events
        ],
    }


@router.patch("/events/{event_id}", summary="Resolve an anomaly event")
async def resolve_event(
    event_id: int,
    req: ResolveRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
) -> dict:
    event = db.query(AnomalyEvent).filter(AnomalyEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail=f"AnomalyEvent {event_id} not found")

    old_status = event.status
    event.status = req.status
    event.resolved_at = datetime.datetime.utcnow()
    event.resolved_by_user_id = current_user.id
    event.resolution_note = req.resolution_note

    db.add(ActionLog(
        business_id=event.business_id,
        status=f"anomaly.event.{req.status}",
        detail=(
            f"event_id={event_id} signal={event.signal_type} "
            f"old={old_status} new={req.status} reviewer={current_user.id}"
        ),
    ))
    db.commit()
    return {"id": event_id, "status": req.status, "ok": True}


@router.get("/stats", summary="Anomaly detection statistics")
async def stats(
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> dict:
    import os

    # Counts by severity
    rows = (
        db.query(AnomalyEvent.severity, func.count(AnomalyEvent.id))
        .filter(AnomalyEvent.status == "open")
        .group_by(AnomalyEvent.severity)
        .all()
    )
    by_severity = {sev: cnt for sev, cnt in rows}

    # Top signal types
    type_rows = (
        db.query(AnomalyEvent.signal_type, func.count(AnomalyEvent.id))
        .filter(AnomalyEvent.status == "open")
        .group_by(AnomalyEvent.signal_type)
        .order_by(func.count(AnomalyEvent.id).desc())
        .limit(5)
        .all()
    )

    # Last scan time (most recently created event)
    latest = (
        db.query(AnomalyEvent.created_at)
        .order_by(AnomalyEvent.created_at.desc())
        .first()
    )

    return {
        "open_by_severity": by_severity,
        "top_signal_types": {t: c for t, c in type_rows},
        "total_open": sum(by_severity.values()),
        "last_scan_at": latest[0].isoformat() if latest else None,
        "backend": os.getenv("ANOMALY_BACKEND", "heuristic"),
    }
