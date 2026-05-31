"""
Aurora LTS — AML / Sanctions Screening Router  (P2-08)
=========================================================

Endpoints
─────────
  POST  /api/v1/aml/screen
        Screen one or more names on-demand.  Requires admin or
        internal (Cloud Scheduler) caller.

  POST  /api/v1/aml/refresh-lists
        Trigger a synchronous refresh of all sanctions lists from
        the upstream government sources.  Admin-only.

  GET   /api/v1/aml/hits
        List all pending / unreviewed sanctions hits.  Admin-only.

  PATCH /api/v1/aml/hits/{hit_id}
        Mark a hit as false_positive | confirmed | ignored.
        Admin-only.

  GET   /api/v1/aml/stats
        Summary stats: total entries per list, pending hits,
        last sync time.  Admin-only.
"""

from __future__ import annotations

import datetime
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db, ActionLog
from app.database.models import SanctionsListEntry, SanctionsScreeningHit
from app.middleware.auth_middleware import require_admin, get_current_user
from app.services.compliance.sanctions import (
    screen_name,
    screen_multiple,
    sync_lists,
    ScreeningResult,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/aml", tags=["aml"])


# ─────────────────────────────────────────────────────────────
# Request / Response schemas
# ─────────────────────────────────────────────────────────────

class ScreenRequest(BaseModel):
    names: List[str] = Field(..., min_length=1, max_length=10)
    business_id: Optional[int] = None
    invoice_id: Optional[int] = None


class HitResponse(BaseModel):
    id: int
    business_id: Optional[int]
    invoice_id: Optional[int]
    queried_name: str
    matched_entry_id: int
    matched_name: str
    list_source: str
    match_score: float
    status: str
    created_at: datetime.datetime
    reviewed_at: Optional[datetime.datetime]
    review_note: Optional[str]

    class Config:
        from_attributes = True


class ReviewRequest(BaseModel):
    status: str = Field(..., pattern="^(false_positive|confirmed|ignored)$")
    review_note: Optional[str] = Field(None, max_length=500)


class StatsResponse(BaseModel):
    entries_by_source: dict
    total_entries: int
    pending_hits: int
    last_sync_at: Optional[str]
    backend: str


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────

@router.post("/screen", summary="Screen names against sanctions lists")
async def screen_endpoint(
    req: ScreenRequest,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> dict:
    results = screen_multiple(
        req.names,
        business_id=req.business_id,
        db=db,
    )
    worst_tier = _worst_tier([r.risk_tier for r in results])
    return {
        "overall_risk_tier": worst_tier,
        "results": [
            {
                "queried_name": r.queried_name,
                "risk_tier": r.risk_tier,
                "best_score": r.best_score,
                "hit_count": len(r.hits),
                "lists_searched": r.lists_searched,
            }
            for r in results
        ],
    }


@router.post("/refresh-lists", summary="Trigger sanctions list refresh from upstream sources")
async def refresh_lists(
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> dict:
    log.info("Manual sanctions list refresh triggered by admin")
    result = sync_lists(db=db)
    db.add(ActionLog(
        business_id=None,
        status="sanctions.lists.refreshed",
        detail=f"inserted={result.get('inserted',0)} deleted={result.get('deleted',0)} backend={result.get('backend')}",
    ))
    db.commit()
    return result


@router.get("/hits", summary="List all unreviewed sanctions hits")
async def list_hits(
    status: Optional[str] = Query(None, description="Filter by status (pending_review, false_positive, confirmed, ignored, auto_cleared)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> dict:
    q = db.query(SanctionsScreeningHit, SanctionsListEntry).join(
        SanctionsListEntry,
        SanctionsScreeningHit.matched_entry_id == SanctionsListEntry.id,
    )
    if status:
        q = q.filter(SanctionsScreeningHit.status == status)
    else:
        q = q.filter(SanctionsScreeningHit.status == "pending_review")

    total = q.count()
    rows = q.order_by(SanctionsScreeningHit.match_score.desc()).offset(offset).limit(limit).all()

    return {
        "total": total,
        "hits": [
            {
                "id": hit.id,
                "business_id": hit.business_id,
                "invoice_id": hit.invoice_id,
                "queried_name": hit.queried_name,
                "matched_name": entry.full_name,
                "list_source": entry.list_source,
                "match_score": hit.match_score,
                "status": hit.status,
                "entity_type": entry.entity_type,
                "country_code": entry.country_code,
                "program": entry.program,
                "created_at": hit.created_at.isoformat(),
                "reviewed_at": hit.reviewed_at.isoformat() if hit.reviewed_at else None,
                "review_note": hit.review_note,
            }
            for hit, entry in rows
        ],
    }


@router.patch("/hits/{hit_id}", summary="Review a sanctions hit")
async def review_hit(
    hit_id: int,
    req: ReviewRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
) -> dict:
    hit = db.query(SanctionsScreeningHit).filter(SanctionsScreeningHit.id == hit_id).first()
    if not hit:
        raise HTTPException(status_code=404, detail=f"Hit {hit_id} not found")

    old_status = hit.status
    hit.status = req.status
    hit.reviewed_at = datetime.datetime.utcnow()
    hit.reviewed_by_user_id = current_user.id
    hit.review_note = req.review_note

    db.add(ActionLog(
        business_id=hit.business_id,
        status=f"sanctions.hit.reviewed.{req.status}",
        detail=(
            f"hit_id={hit_id} queried_name={hit.queried_name!r} "
            f"old_status={old_status} new_status={req.status} "
            f"reviewer={current_user.id}"
        ),
    ))
    db.commit()
    return {"id": hit_id, "status": req.status, "ok": True}


@router.get("/stats", summary="Sanctions list and hit statistics")
async def stats(
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> StatsResponse:
    import os
    from sqlalchemy import func

    rows = (
        db.query(SanctionsListEntry.list_source, func.count(SanctionsListEntry.id))
        .group_by(SanctionsListEntry.list_source)
        .all()
    )
    entries_by_source = {src: cnt for src, cnt in rows}
    total = sum(entries_by_source.values())
    pending = db.query(SanctionsScreeningHit).filter_by(status="pending_review").count()

    latest = (
        db.query(SanctionsListEntry.fetched_at)
        .order_by(SanctionsListEntry.fetched_at.desc())
        .first()
    )
    last_sync = latest[0].isoformat() if latest else None

    return StatsResponse(
        entries_by_source=entries_by_source,
        total_entries=total,
        pending_hits=pending,
        last_sync_at=last_sync,
        backend=os.getenv("SANCTIONS_BACKEND", "stub"),
    )


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────

_TIER_ORDER = {"clean": 0, "low": 1, "medium": 2, "high": 3, "blocked": 4}


def _worst_tier(tiers: List[str]) -> str:
    if not tiers:
        return "clean"
    return max(tiers, key=lambda t: _TIER_ORDER.get(t, 0))
