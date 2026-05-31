"""
ASG Solutions -- Payments Router
================================
All payment-related API endpoints live here.

ENDPOINTS:
  POST /api/v1/payments                       -- Record a new payment
  GET  /api/v1/invoices/{id}/payments         -- List payments for an invoice
  GET  /api/v1/invoices/overdue               -- List overdue invoices
  GET  /api/v1/payments/summary               -- Payment summary stats
  POST /api/v1/payments/send-reminders        -- Send overdue reminders (admin)

SECURITY:
  All endpoints require JWT authentication (get_current_user).
  Business owners see only their own data (get_business_filter).
  Reminder sending is admin-only (require_admin).
"""

# -----------------------------------------------------------------
# IMPORTS
# -----------------------------------------------------------------
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aurora_shared.database import get_db, Invoice, Payment, User
from aurora_shared.middleware.auth_middleware import (
    get_current_user,
    get_business_filter,
    require_admin,
)
from app.services.payment_service import (
    record_payment,
    get_overdue_invoices,
    get_business_balance,
    get_payments_for_invoice,
)
from app.services.reminder_service import send_overdue_reminders


# -----------------------------------------------------------------
# PYDANTIC SCHEMAS
# -----------------------------------------------------------------

class PaymentCreate(BaseModel):
    """What you send to record a new payment."""
    invoice_id: int                           # Which invoice is being paid
    amount: float                             # How much was paid
    method: str                               # cash / transfer / credit / check
    payment_date: str                         # ISO date string, e.g., "2026-04-13"
    reference: Optional[str] = None           # Check #, transfer ID
    notes: Optional[str] = None               # Optional note


class PaymentResponse(BaseModel):
    """What the API returns after recording a payment."""
    payment_id: int
    invoice_id: int
    invoice_number: str
    amount_paid_now: float
    total_paid: float
    payment_status: str
    remaining: float


# -----------------------------------------------------------------
# CREATE THE ROUTER
# -----------------------------------------------------------------
router = APIRouter(tags=["Payments"])


# =================================================================
# ENDPOINT 1: POST /api/v1/payments -- Record Payment
# =================================================================
@router.post("/api/v1/payments")
def create_payment(
    payload: PaymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Record a payment against an invoice.

    Steps:
      1. Verify the user has access to this invoice's business
      2. Parse the payment date
      3. Call payment_service.record_payment()
      4. Return the updated payment status
    """

    # ── Step 1: Verify access ──
    invoice = db.query(Invoice).filter(Invoice.id == payload.invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    biz_filter = get_business_filter(current_user)
    if biz_filter is not None and invoice.business_id != biz_filter:
        raise HTTPException(status_code=403, detail="Access denied")

    # ── Step 2: Parse the payment date ──
    try:
        payment_date = datetime.datetime.fromisoformat(payload.payment_date)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS",
        )

    # ── Step 3: Record the payment ──
    try:
        result = record_payment(
            db=db,
            invoice_id=payload.invoice_id,
            amount=payload.amount,
            method=payload.method,
            payment_date=payment_date,
            reference=payload.reference,
            notes=payload.notes,
            recorded_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result


# =================================================================
# ENDPOINT 2: GET /api/v1/invoices/{id}/payments -- List Payments
# =================================================================
@router.get("/api/v1/invoices/{invoice_id}/payments")
def list_invoice_payments(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List all payments recorded for a specific invoice.
    """

    # ── Verify invoice exists and user has access ──
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    biz_filter = get_business_filter(current_user)
    if biz_filter is not None and invoice.business_id != biz_filter:
        raise HTTPException(status_code=403, detail="Access denied")

    payments = get_payments_for_invoice(db, invoice_id)

    return {
        "invoice_id": invoice_id,
        "invoice_number": invoice.invoice_number,
        "amount_total": invoice.amount_total,
        "amount_paid": invoice.amount_paid or 0.0,
        "remaining": round(invoice.amount_total - (invoice.amount_paid or 0.0), 2),
        "payment_status": invoice.payment_status or "unpaid",
        "payments": payments,
    }


# =================================================================
# ENDPOINT 3: GET /api/v1/invoices/overdue -- Overdue Invoices
# =================================================================
@router.get("/api/v1/payments/overdue")
def list_overdue_invoices(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List all overdue invoices (past due date, not fully paid).
    Admin sees all businesses, business owner sees only their own.
    """
    biz_filter = get_business_filter(current_user)
    overdue = get_overdue_invoices(db, business_id=biz_filter)
    return overdue


# =================================================================
# ENDPOINT 4: GET /api/v1/payments/summary -- Payment Summary
# =================================================================
@router.get("/api/v1/payments/summary")
def payment_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get payment summary statistics.
    Admin sees totals across all businesses.
    Business owner sees only their own.
    """
    biz_filter = get_business_filter(current_user)

    if biz_filter is not None:
        # Business owner — get their specific balance
        return get_business_balance(db, biz_filter)
    else:
        # Admin — aggregate across all businesses
        from aurora_shared.database.models import Business as BizModel
        all_biz = db.query(BizModel).all()

        total_outstanding = 0.0
        total_paid = 0.0
        total_unpaid_count = 0
        total_overdue_count = 0

        for biz in all_biz:
            balance = get_business_balance(db, biz.id)
            total_outstanding += balance["total_outstanding"]
            total_paid += balance["total_paid"]
            total_unpaid_count += balance["unpaid_invoice_count"]
            total_overdue_count += balance["overdue_count"]

        return {
            "business_id": None,
            "total_outstanding": round(total_outstanding, 2),
            "total_paid": round(total_paid, 2),
            "unpaid_invoice_count": total_unpaid_count,
            "overdue_count": total_overdue_count,
            "oldest_due_date": None,
        }


# =================================================================
# ENDPOINT 5: POST /api/v1/payments/send-reminders -- Send Reminders
# =================================================================
@router.post("/api/v1/payments/send-reminders")
async def trigger_reminders(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Send WhatsApp reminders for all overdue invoices.
    Admin only. Skips invoices already reminded in the last 7 days.
    """
    result = await send_overdue_reminders(db)
    return result
