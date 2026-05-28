"""
ASG Solutions -- Payment Service
================================
Handles all payment-related business logic:
  - Recording a payment against an invoice
  - Recalculating invoice payment status
  - Finding overdue invoices
  - Getting a business's outstanding balance

REAL-WORLD ANALOGY:
Think of this as the accounts receivable desk:
  - "Record payment"  = stamping a receipt and updating the ledger
  - "Overdue invoices" = pulling the stack of unpaid bills past their due date
  - "Business balance" = summing up how much a business is still owed

TAX COMPLIANCE NOTE:
  Payments NEVER change the invoice's financial numbers.
  VAT (18%), allocation numbers, and thresholds are locked at
  invoice creation/finalization. Payments only update:
    - invoice.amount_paid (running total)
    - invoice.payment_status (unpaid / partial / paid)
"""

# -----------------------------------------------------------------
# IMPORTS
# -----------------------------------------------------------------
import datetime

from sqlalchemy.orm import Session

from app.database.models import Invoice, Payment, ActionLog, Business


# -----------------------------------------------------------------
# FUNCTION: record_payment
# -----------------------------------------------------------------
# PURPOSE:
#   Record a payment received against an invoice and update the
#   invoice's payment status accordingly.
#
# REAL-WORLD ANALOGY:
#   A customer hands you cash or a check. You write a receipt,
#   update the invoice to show how much has been paid, and check
#   if the bill is now fully settled.
#
# PARAMETERS:
#   db (Session)        -- database session
#   invoice_id (int)    -- which invoice the payment is for
#   amount (float)      -- how much was paid
#   method (str)        -- "cash", "transfer", "credit", or "check"
#   payment_date (datetime) -- when the payment was made
#   reference (str|None) -- check number, transfer ID, etc.
#   notes (str|None)    -- optional note
#   recorded_by (int|None) -- user ID of who recorded this
#
# RETURNS:
#   dict -- {payment_id, invoice_number, payment_status, remaining}
#
# RAISES:
#   ValueError -- if invoice not found, not finalized, or overpayment
# -----------------------------------------------------------------
def record_payment(
    db: Session,
    invoice_id: int,
    amount: float,
    method: str,
    payment_date: datetime.datetime,
    reference: str | None = None,
    notes: str | None = None,
    recorded_by: int | None = None,
) -> dict:
    """Record a payment against an invoice and update payment status."""

    # ── Step 1: Find the invoice ──
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise ValueError("Invoice not found")

    # ── Step 2: Validate invoice status ──
    # Only finalized or sent invoices can receive payments.
    # Draft invoices haven't been issued yet, so no payment expected.
    if invoice.status not in ("finalized", "sent"):
        raise ValueError(
            f"Cannot record payment — invoice is '{invoice.status}'. "
            f"Only finalized or sent invoices can receive payments."
        )

    # ── Step 3: Check for overpayment ──
    current_paid = invoice.amount_paid or 0.0
    remaining = invoice.amount_total - current_paid

    if amount <= 0:
        raise ValueError("Payment amount must be positive")

    if amount > remaining + 0.01:  # Small tolerance for floating point
        raise ValueError(
            f"Payment of {amount:.2f} exceeds remaining balance of {remaining:.2f}"
        )

    # ── Step 4: Validate method ──
    valid_methods = ("cash", "transfer", "credit", "check")
    if method not in valid_methods:
        raise ValueError(f"Invalid payment method. Must be one of: {', '.join(valid_methods)}")

    # ── Step 5: Create the Payment record ──
    payment = Payment(
        invoice_id=invoice_id,
        business_id=invoice.business_id,
        amount=amount,
        method=method,
        reference=reference,
        payment_date=payment_date,
        notes=notes,
        recorded_by=recorded_by,
    )
    db.add(payment)

    # ── Step 6: Update invoice payment totals ──
    invoice.amount_paid = current_paid + amount

    # Recalculate status:
    #   - paid:    collected >= total (within tolerance)
    #   - partial: collected > 0 but less than total
    #   - unpaid:  nothing collected yet
    if invoice.amount_paid >= invoice.amount_total - 0.01:
        invoice.payment_status = "paid"
    elif invoice.amount_paid > 0:
        invoice.payment_status = "partial"
    else:
        invoice.payment_status = "unpaid"

    # ── Step 7: Log the action ──
    log = ActionLog(
        business_id=invoice.business_id,
        status="payment_received",
        detail=(
            f"Payment of {amount:.2f} ILS ({method}) recorded for "
            f"invoice {invoice.invoice_number} — "
            f"status: {invoice.payment_status}"
        ),
    )
    db.add(log)

    db.commit()
    db.refresh(payment)

    new_remaining = invoice.amount_total - invoice.amount_paid
    print(
        f"[PAYMENT] Recorded {amount:.2f} ILS ({method}) for "
        f"{invoice.invoice_number} — "
        f"status: {invoice.payment_status}, remaining: {new_remaining:.2f}"
    )

    # P2-25: fire push notification on full payment
    if invoice.payment_status == "paid":
        try:
            from app.services.realtime.push_notifications import send_push_to_users, EVENTS
            event = EVENTS["invoice_paid"]
            event.data = {
                "invoice_id": invoice.id,
                "amount": amount,
                "invoice_number": getattr(invoice, "invoice_number", ""),
            }
            send_push_to_users("invoice_paid", [invoice.business_id])
        except Exception:
            pass  # push is non-blocking

    return {
        "payment_id": payment.id,
        "invoice_id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "amount_paid_now": amount,
        "total_paid": invoice.amount_paid,
        "payment_status": invoice.payment_status,
        "remaining": round(new_remaining, 2),
    }


# -----------------------------------------------------------------
# FUNCTION: get_overdue_invoices
# -----------------------------------------------------------------
# PURPOSE:
#   Find all invoices that are past their due date and not fully paid.
#   These are candidates for payment reminders.
#
# REAL-WORLD ANALOGY:
#   Like going through the filing cabinet and pulling out all bills
#   that should have been paid by now but haven't been.
#
# PARAMETERS:
#   db (Session)             -- database session
#   business_id (int|None)   -- filter by business (None = all)
#
# RETURNS:
#   list[dict] -- each dict has invoice details + days_overdue
# -----------------------------------------------------------------
def get_overdue_invoices(
    db: Session,
    business_id: int | None = None,
) -> list[dict]:
    """Get all overdue invoices (past due date, not fully paid)."""

    now = datetime.datetime.utcnow()

    query = db.query(Invoice).filter(
        Invoice.payment_status != "paid",         # Not fully paid
        Invoice.due_date != None,                 # Has a due date (was finalized)
        Invoice.due_date < now,                   # Past the due date
        Invoice.status.in_(("finalized", "sent")),  # Active invoices only
    )

    if business_id is not None:
        query = query.filter(Invoice.business_id == business_id)

    overdue = query.order_by(Invoice.due_date.asc()).all()

    results = []
    for inv in overdue:
        days_overdue = (now - inv.due_date).days
        remaining = inv.amount_total - (inv.amount_paid or 0.0)
        results.append({
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "business_id": inv.business_id,
            "business_name": inv.business.name if inv.business else None,
            "beneficiary_name": inv.beneficiary_name,
            "beneficiary_contact": inv.beneficiary_contact,
            "amount_total": inv.amount_total,
            "amount_paid": inv.amount_paid or 0.0,
            "remaining": round(remaining, 2),
            "payment_status": inv.payment_status,
            "due_date": inv.due_date.isoformat() if inv.due_date else None,
            "days_overdue": days_overdue,
        })

    return results


# -----------------------------------------------------------------
# FUNCTION: get_business_balance
# -----------------------------------------------------------------
# PURPOSE:
#   Calculate how much money a business is still owed across all
#   its unpaid/partially paid invoices.
#
# REAL-WORLD ANALOGY:
#   Like adding up all the open tabs at a restaurant at the end
#   of the night to see the total outstanding.
#
# PARAMETERS:
#   db (Session)        -- database session
#   business_id (int)   -- which business to check
#
# RETURNS:
#   dict -- {total_outstanding, total_paid, invoice_count, overdue_count}
# -----------------------------------------------------------------
def get_business_balance(db: Session, business_id: int) -> dict:
    """Get the outstanding balance for a business."""

    now = datetime.datetime.utcnow()

    # Query all non-cancelled invoices for this business
    invoices = (
        db.query(Invoice)
        .filter(
            Invoice.business_id == business_id,
            Invoice.status.in_(("finalized", "sent")),
        )
        .all()
    )

    total_outstanding = 0.0
    total_paid = 0.0
    unpaid_count = 0
    overdue_count = 0
    oldest_due = None

    for inv in invoices:
        paid = inv.amount_paid or 0.0
        remaining = inv.amount_total - paid
        total_paid += paid

        if inv.payment_status != "paid":
            total_outstanding += remaining
            unpaid_count += 1

            if inv.due_date and inv.due_date < now:
                overdue_count += 1

            if inv.due_date and (oldest_due is None or inv.due_date < oldest_due):
                oldest_due = inv.due_date

    return {
        "business_id": business_id,
        "total_outstanding": round(total_outstanding, 2),
        "total_paid": round(total_paid, 2),
        "unpaid_invoice_count": unpaid_count,
        "overdue_count": overdue_count,
        "oldest_due_date": oldest_due.isoformat() if oldest_due else None,
    }


# -----------------------------------------------------------------
# FUNCTION: get_payments_for_invoice
# -----------------------------------------------------------------
# PURPOSE:
#   List all payments recorded against a specific invoice.
#
# PARAMETERS:
#   db (Session)       -- database session
#   invoice_id (int)   -- which invoice to look up
#
# RETURNS:
#   list[dict] -- each dict is a payment record
# -----------------------------------------------------------------
def get_payments_for_invoice(db: Session, invoice_id: int) -> list[dict]:
    """List all payments for a specific invoice."""

    payments = (
        db.query(Payment)
        .filter(Payment.invoice_id == invoice_id)
        .order_by(Payment.payment_date.desc())
        .all()
    )

    return [
        {
            "id": p.id,
            "invoice_id": p.invoice_id,
            "amount": p.amount,
            "method": p.method,
            "reference": p.reference,
            "payment_date": p.payment_date.isoformat() if p.payment_date else None,
            "notes": p.notes,
            "recorded_by": p.recorded_by,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in payments
    ]
