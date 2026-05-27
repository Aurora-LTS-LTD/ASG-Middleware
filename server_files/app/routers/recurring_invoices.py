"""
Aurora LTS — Recurring Invoices Router (P2-01)
================================================
Three endpoints + one Cloud Scheduler tick.

  POST   /api/v1/recurring-invoices/tick
         service-to-service (X-API-Key, scope="recurring-tick").
         Called by Cloud Scheduler once an hour. Mints any
         schedules whose next_due_at <= now.

  POST   /api/v1/businesses/{business_id}/recurring-invoices
         Admin/owner creates a recurring schedule.

  GET    /api/v1/businesses/{business_id}/recurring-invoices
         List schedules for this business.

  POST   /api/v1/recurring-invoices/{schedule_id}/cancel
         Soft-delete a schedule (sets active=False).
"""
from __future__ import annotations

import datetime
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.database.models import RecurringInvoiceSchedule, User, Business
from app.middleware.auth_middleware import get_current_user, require_admin
from app.middleware.rate_limit import limiter
from app.middleware.api_key_auth import require_api_key
from app.services.recurring_invoice import (
    tick_due_schedules,
    VALID_CADENCES,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["recurring-invoices"])


# ─────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────
class ScheduleCreate(BaseModel):
    beneficiary_name: str = Field(..., min_length=1, max_length=200)
    beneficiary_tax_id: Optional[str] = Field(None, max_length=32)
    beneficiary_contact: Optional[str] = Field(None, max_length=255)
    amount_net: float = Field(..., gt=0)
    description: Optional[str] = Field(None, max_length=2000)
    cadence: str = Field(..., description="weekly|monthly|quarterly|yearly")
    next_due_at: datetime.datetime


class ScheduleOut(BaseModel):
    id: int
    business_id: int
    beneficiary_name: str
    beneficiary_tax_id: Optional[str]
    amount_net: float
    cadence: str
    next_due_at: datetime.datetime
    last_run_at: Optional[datetime.datetime]
    active: bool
    created_at: datetime.datetime


class TickResponse(BaseModel):
    created_count: int
    error_count: int
    invoice_numbers: List[str]


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────
@router.post(
    "/recurring-invoices/tick",
    response_model=TickResponse,
)
@limiter.limit("60/minute")
def tick(
    request: Request,
    db: Session = Depends(get_db),
    _api_key=Depends(require_api_key(scope="recurring-tick")),
) -> TickResponse:
    """Cloud Scheduler entrypoint. Mints any schedules whose next_due_at <= now."""
    created, errors, invoices = tick_due_schedules(db)
    return TickResponse(
        created_count=created,
        error_count=errors,
        invoice_numbers=[inv.get("invoice_number", "") for inv in invoices],
    )


@router.post(
    "/businesses/{business_id}/recurring-invoices",
    response_model=ScheduleOut,
    status_code=201,
)
@limiter.limit("30/minute")
def create_schedule(
    business_id: int,
    payload: ScheduleCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScheduleOut:
    """Create a recurring-invoice schedule for a business."""
    if payload.cadence not in VALID_CADENCES:
        raise HTTPException(
            status_code=400,
            detail=f"cadence must be one of {sorted(VALID_CADENCES)}",
        )

    biz = db.query(Business).filter(Business.id == business_id).first()
    if not biz:
        raise HTTPException(status_code=404, detail="business_not_found")

    # Authz: admin OR the business owner.
    if current_user.role != "admin" and current_user.business_id != business_id:
        raise HTTPException(status_code=403, detail="not_authorized_for_business")

    schedule = RecurringInvoiceSchedule(
        business_id=business_id,
        beneficiary_name=payload.beneficiary_name.strip(),
        beneficiary_tax_id=(payload.beneficiary_tax_id or "").strip() or None,
        beneficiary_contact=(payload.beneficiary_contact or "").strip() or None,
        amount_net=payload.amount_net,
        description=(payload.description or "").strip() or None,
        cadence=payload.cadence,
        next_due_at=payload.next_due_at,
        active=True,
        created_at=datetime.datetime.utcnow(),
        created_by_user_id=current_user.id,
    )
    db.add(schedule)
    try:
        db.commit()
        db.refresh(schedule)
    except Exception as exc:
        db.rollback()
        log.error("[recurring] create failed: %s", exc)
        raise HTTPException(status_code=500, detail="schedule_persist_failed")

    return _to_out(schedule)


@router.get(
    "/businesses/{business_id}/recurring-invoices",
    response_model=List[ScheduleOut],
)
@limiter.limit("60/minute")
def list_schedules(
    business_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[ScheduleOut]:
    if current_user.role != "admin" and current_user.business_id != business_id:
        raise HTTPException(status_code=403, detail="not_authorized_for_business")
    rows = (
        db.query(RecurringInvoiceSchedule)
        .filter(RecurringInvoiceSchedule.business_id == business_id)
        .order_by(RecurringInvoiceSchedule.next_due_at.asc())
        .all()
    )
    return [_to_out(r) for r in rows]


@router.post("/recurring-invoices/{schedule_id}/cancel")
@limiter.limit("30/minute")
def cancel_schedule(
    schedule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    schedule = (
        db.query(RecurringInvoiceSchedule)
        .filter(RecurringInvoiceSchedule.id == schedule_id)
        .first()
    )
    if not schedule:
        raise HTTPException(status_code=404, detail="schedule_not_found")
    if current_user.role != "admin" and current_user.business_id != schedule.business_id:
        raise HTTPException(status_code=403, detail="not_authorized_for_business")

    schedule.active = False
    db.commit()
    log.info("[recurring] cancelled schedule_id=%s by user=%s", schedule_id, current_user.id)
    return {"ok": True, "id": schedule_id, "active": False}


def _to_out(s: RecurringInvoiceSchedule) -> ScheduleOut:
    return ScheduleOut(
        id=s.id,
        business_id=s.business_id,
        beneficiary_name=s.beneficiary_name,
        beneficiary_tax_id=s.beneficiary_tax_id,
        amount_net=s.amount_net,
        cadence=s.cadence,
        next_due_at=s.next_due_at,
        last_run_at=s.last_run_at,
        active=s.active,
        created_at=s.created_at,
    )
