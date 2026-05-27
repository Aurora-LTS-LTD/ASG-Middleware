"""
Aurora LTS — Invoice Payments Router (P2-07)
==============================================
  POST /api/v1/invoices/{invoice_id}/payments
       Record a partial payment.
  GET  /api/v1/invoices/{invoice_id}/payments
       List all payments + balance_due.
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.database.models import Invoice, InvoicePayment, User
from app.middleware.auth_middleware import get_current_user
from app.middleware.rate_limit import limiter
from app.services.payments_service import (
    compute_balance, record_payment, PaymentError,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/invoices", tags=["invoice-payments"])


class PaymentCreate(BaseModel):
    amount: float = Field(..., gt=0)
    paid_at: Optional[datetime.datetime] = None
    note: Optional[str] = Field(None, max_length=500)


class PaymentOut(BaseModel):
    id: int
    invoice_id: int
    amount: float
    currency: str
    paid_at: datetime.datetime
    source: str
    note: Optional[str]
    created_at: datetime.datetime


class PaymentsListResponse(BaseModel):
    invoice_id: int
    amount_total: float
    balance_due: float
    status: str
    payments: list[PaymentOut]


def _authz_invoice(invoice: Invoice, user: User) -> None:
    if user.role != "admin" and user.business_id != invoice.business_id:
        raise HTTPException(status_code=403, detail="not_authorized_for_invoice")


@router.post("/{invoice_id}/payments", response_model=PaymentOut, status_code=201)
@limiter.limit("60/minute")
def create_payment(
    invoice_id: int,
    payload: PaymentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PaymentOut:
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if invoice is None:
        raise HTTPException(status_code=404, detail="invoice_not_found")
    _authz_invoice(invoice, current_user)

    try:
        payment = record_payment(
            db=db,
            invoice_id=invoice_id,
            amount=payload.amount,
            paid_at=payload.paid_at,
            source="manual",
            note=payload.note,
            created_by_user_id=current_user.id,
        )
    except PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return _payment_to_out(payment)


@router.get("/{invoice_id}/payments", response_model=PaymentsListResponse)
@limiter.limit("120/minute")
def list_payments(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PaymentsListResponse:
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if invoice is None:
        raise HTTPException(status_code=404, detail="invoice_not_found")
    _authz_invoice(invoice, current_user)

    payments = (
        db.query(InvoicePayment)
        .filter(InvoicePayment.invoice_id == invoice_id)
        .order_by(InvoicePayment.paid_at.asc())
        .all()
    )

    return PaymentsListResponse(
        invoice_id=invoice_id,
        amount_total=float(invoice.amount_total or 0.0),
        balance_due=compute_balance(invoice_id, db),
        status=invoice.status,
        payments=[_payment_to_out(p) for p in payments],
    )


def _payment_to_out(p: InvoicePayment) -> PaymentOut:
    return PaymentOut(
        id=p.id,
        invoice_id=p.invoice_id,
        amount=p.amount,
        currency=p.currency,
        paid_at=p.paid_at,
        source=p.source,
        note=p.note,
        created_at=p.created_at,
    )
