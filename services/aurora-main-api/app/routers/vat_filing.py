"""
Aurora LTS — VAT Return Filing Router  (P2-22)

Endpoints
─────────
  POST /api/v1/vat/prepare-return
        Aggregate the current bi-monthly period and create a draft.
        Triggered by Cloud Scheduler on the 1st of each filing month.
        Also callable manually by admin / business owner for any period.

  GET  /api/v1/vat/returns
        List VAT returns for the authenticated business or (admin) all.

  GET  /api/v1/vat/returns/{id}
        Detail of a single return including all aggregated figures.

  POST /api/v1/vat/returns/{id}/submit
        Submit a draft return to the ITA. Admin or business owner.

  GET  /api/v1/vat/periods/current
        Return the current filing period (year, number, due date).
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aurora_shared.database import get_db
from aurora_shared.database.models import VatReturn
from aurora_shared.middleware.auth_middleware import get_current_user, require_admin, get_business_filter
from app.services.ita.vat_filing import (
    prepare_return,
    submit_return,
    current_period,
    VatPeriod,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/vat", tags=["vat"])


# ─────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────

class PrepareRequest(BaseModel):
    business_id: Optional[int] = None  # admin can specify; business owners use their own
    period_year: Optional[int] = None  # default: current period
    period_number: Optional[int] = None


def _period_response(p: VatPeriod) -> dict:
    return {
        "year": p.year,
        "period_number": p.period_number,
        "frequency": p.frequency,
        "start_date": p.start_date.isoformat(),
        "end_date": p.end_date.isoformat(),
        "due_date": p.due_date.isoformat(),
    }


def _return_response(r: VatReturn) -> dict:
    return {
        "id": r.id,
        "business_id": r.business_id,
        "tax_id": r.tax_id,
        "period": {
            "year": r.period_year,
            "number": r.period_number,
            "frequency": r.period_frequency,
            "start": r.period_start.isoformat() if r.period_start else None,
            "end": r.period_end.isoformat() if r.period_end else None,
            "due": r.due_date.isoformat() if r.due_date else None,
        },
        "sales": {
            "taxable_ils": r.taxable_sales_ils,
            "vat_collected_ils": r.vat_collected_ils,
            "exempt_ils": r.exempt_sales_ils,
            "invoice_count": r.invoice_count,
        },
        "purchases": {
            "taxable_ils": r.taxable_purchases_ils,
            "input_vat_ils": r.input_vat_ils,
            "expense_count": r.expense_count,
        },
        "net_vat_payable_ils": r.net_vat_payable_ils,
        "is_refund": r.net_vat_payable_ils < 0,
        "status": r.status,
        "confirmation_number": r.confirmation_number,
        "rejection_reason": r.rejection_reason,
        "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
        "created_at": r.created_at.isoformat(),
    }


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────

@router.get("/periods/current", summary="Get the current VAT filing period")
async def get_current_period(_=Depends(get_current_user)) -> dict:
    period = current_period()
    return _period_response(period)


@router.post("/prepare-return", summary="Prepare a draft VAT return")
async def prepare_vat_return(
    req: PrepareRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    # Determine business_id
    if req.business_id and current_user.role == "admin":
        business_id = req.business_id
    elif current_user.role in ("business", "admin"):
        business_id = req.business_id or current_user.business_id
    else:
        raise HTTPException(status_code=403, detail="Not authorised to prepare VAT returns")

    if not business_id:
        raise HTTPException(status_code=400, detail="business_id required")

    # Determine period
    if req.period_year and req.period_number:
        from app.services.ita.vat_filing import _period_for_date
        # Reconstruct period from year + number (bi-monthly assumed)
        start_month = (req.period_number - 1) * 2 + 1
        period = _period_for_date(datetime.date(req.period_year, start_month, 1))
    else:
        period = current_period()

    vat_return = prepare_return(business_id=business_id, period=period, db=db)
    return {"status": "draft_created", "vat_return": _return_response(vat_return)}


@router.get("/returns", summary="List VAT returns")
async def list_returns(
    business_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    biz_filter = get_business_filter(current_user)
    q = db.query(VatReturn)
    if biz_filter is not None:
        q = q.filter(VatReturn.business_id == biz_filter)
    elif business_id:
        q = q.filter(VatReturn.business_id == business_id)
    if status:
        q = q.filter(VatReturn.status == status)
    rows = q.order_by(VatReturn.period_year.desc(), VatReturn.period_number.desc()).limit(limit).all()
    return {"returns": [_return_response(r) for r in rows], "total": len(rows)}


@router.get("/returns/{return_id}", summary="Get a VAT return detail")
async def get_return(
    return_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    biz_filter = get_business_filter(current_user)
    r = db.query(VatReturn).filter(VatReturn.id == return_id).first()
    if not r:
        raise HTTPException(status_code=404, detail=f"VatReturn {return_id} not found")
    if biz_filter is not None and r.business_id != biz_filter:
        raise HTTPException(status_code=403, detail="Access denied")
    return _return_response(r)


@router.post("/returns/{return_id}/submit", summary="Submit a draft VAT return to ITA")
async def submit_vat_return(
    return_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    biz_filter = get_business_filter(current_user)
    r = db.query(VatReturn).filter(VatReturn.id == return_id).first()
    if not r:
        raise HTTPException(status_code=404, detail=f"VatReturn {return_id} not found")
    if biz_filter is not None and r.business_id != biz_filter:
        raise HTTPException(status_code=403, detail="Access denied")

    result = submit_return(vat_return_id=return_id, db=db, submitted_by_user_id=current_user.id)
    return {
        "ok": result.success,
        "confirmation_number": result.confirmation_number,
        "message": result.message,
        "backend": result.backend,
        "latency_ms": result.latency_ms,
    }
