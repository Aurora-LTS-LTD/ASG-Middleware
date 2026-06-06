"""
Aurora LTS — Credit Notes Router (P2-05)
==========================================
  POST /api/v1/invoices/{invoice_id}/credit-note
       Issue a credit note against `invoice_id`.

Auth: get_current_user (admin or the invoice's business owner).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from aurora_shared.database import get_db
from aurora_shared.database.models import Invoice, User
from aurora_shared.middleware.auth_middleware import get_current_user
from aurora_shared.middleware.rate_limit import limiter
from app.services.credit_note import issue_credit_note, CreditNoteError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/invoices", tags=["credit-notes"])


class CreditNoteRequest(BaseModel):
    amount_net_to_credit: float = Field(..., gt=0)
    reason: str | None = Field(None, max_length=500)


@router.post("/{invoice_id}/credit-note")
@limiter.limit("30/minute")
def create_credit_note(
    invoice_id: int,
    payload: CreditNoteRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    original = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if original is None:
        raise HTTPException(status_code=404, detail="invoice_not_found")

    if current_user.role != "admin" and current_user.business_id != original.business_id:
        raise HTTPException(status_code=403, detail="not_authorized_for_invoice")

    try:
        result = issue_credit_note(
            db=db,
            original_invoice_id=invoice_id,
            amount_net_to_credit=payload.amount_net_to_credit,
            reason=payload.reason,
        )
    except CreditNoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"credit_note": result, "ok": True}
