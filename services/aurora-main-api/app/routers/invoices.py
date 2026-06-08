"""
ASG Solutions — Invoice Router
================================
All invoice-related API endpoints live here.

ENDPOINTS:
  GET  /api/v1/invoices                      — List all invoices
  GET  /api/v1/invoices/{id}                 — Get single invoice
  GET  /api/v1/businesses/{bid}/invoices     — List invoices for a business
  POST /api/v1/invoices                      — Create a new draft invoice
  POST /api/v1/invoices/{id}/finalize        — Finalize + get ITA allocation
  POST /api/v1/invoices/{id}/send-whatsapp   — Send invoice via WhatsApp
  GET  /api/v1/logs                          — Get activity logs
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aurora_shared.database import get_db, Invoice, Business, ActionLog, User
from aurora_shared.middleware.auth_middleware import get_current_user, get_business_filter
from app.services.tax_compliance import (
    calculate_vat,
    check_tax_compliance,
    generate_invoice_number,
)
from app.services.ita_mock_service import request_allocation_number
from app.services.whatsapp_sender import send_invoice_via_whatsapp
from app.services.invoice_service import (
    finalize_invoice as _finalize_invoice_logic,
    AllocationFailedError,
    InvoiceNotFoundError,
    InvoiceStateError,
)
from app.services.invoice_lifecycle import transition, cancel_invoice, InvoiceTransitionError


# ─────────────────────────────────────────────────────────────
# PYDANTIC SCHEMAS
# ─────────────────────────────────────────────────────────────
# Schemas define WHAT DATA the API accepts and returns.
# Think of them as "forms" that validate the data before it
# enters the system. If someone sends garbage, Pydantic rejects it.

class InvoiceCreate(BaseModel):
    """What you send to CREATE a new invoice."""
    business_id: int                          # Which business is issuing this
    beneficiary_name: str                     # Who receives the invoice
    beneficiary_tax_id: Optional[str] = None  # Their tax ID (optional)
    beneficiary_contact: Optional[str] = None # Their phone/email (optional)
    amount_net: float                         # Amount before tax
    description: Optional[str] = None         # Optional note


class CancelInvoiceRequest(BaseModel):
    """Optional reason when cancelling a draft / pending_allocation invoice."""
    reason: Optional[str] = None


class InvoiceResponse(BaseModel):
    """What the API RETURNS for an invoice."""
    id: int
    business_id: int
    invoice_number: str
    beneficiary_name: str
    beneficiary_tax_id: Optional[str]
    beneficiary_contact: Optional[str]
    amount_net: float
    vat_rate: float
    vat_amount: float
    amount_total: float
    currency: str
    requires_allocation: int
    allocation_number: Optional[str]
    allocation_status: str
    pdf_url: Optional[str]
    status: str
    description: Optional[str]
    created_at: datetime.datetime
    finalized_at: Optional[datetime.datetime]
    due_date: Optional[datetime.datetime] = None
    payment_status: str = "unpaid"
    amount_paid: float = 0.0
    business_name: Optional[str] = None

    model_config = {"from_attributes": True}


class SendWhatsAppRequest(BaseModel):
    """What you send to trigger WhatsApp invoice delivery."""
    recipient_phone: str  # The customer's WhatsApp number


# ─────────────────────────────────────────────────────────────
# CREATE THE ROUTER
# ─────────────────────────────────────────────────────────────
router = APIRouter(tags=["Invoices"])


# ─────────────────────────────────────────────────────────────
# HELPER: Convert Invoice model to dict with business_name
# ─────────────────────────────────────────────────────────────
def invoice_to_dict(invoice: Invoice) -> dict:
    """Convert an Invoice model instance to a dictionary."""
    return {
        "id": invoice.id,
        "business_id": invoice.business_id,
        "invoice_number": invoice.invoice_number,
        "beneficiary_name": invoice.beneficiary_name,
        "beneficiary_tax_id": invoice.beneficiary_tax_id,
        "beneficiary_contact": invoice.beneficiary_contact,
        "amount_net": invoice.amount_net,
        "vat_rate": invoice.vat_rate,
        "vat_amount": invoice.vat_amount,
        "amount_total": invoice.amount_total,
        "currency": invoice.currency,
        "requires_allocation": invoice.requires_allocation,
        "allocation_number": invoice.allocation_number,
        "allocation_status": invoice.allocation_status,
        "pdf_url": invoice.pdf_url,
        "status": invoice.status,
        "description": invoice.description,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
        "finalized_at": invoice.finalized_at.isoformat() if invoice.finalized_at else None,
        # Lifecycle timestamps (phase29). getattr-guarded so this is safe before
        # the column migration lands; surfaces real values once it does.
        "submitted_at": getattr(invoice, "submitted_at", None).isoformat() if getattr(invoice, "submitted_at", None) else None,
        "sent_at": getattr(invoice, "sent_at", None).isoformat() if getattr(invoice, "sent_at", None) else None,
        "cancelled_at": getattr(invoice, "cancelled_at", None).isoformat() if getattr(invoice, "cancelled_at", None) else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "payment_status": invoice.payment_status or "unpaid",
        "amount_paid": invoice.amount_paid or 0.0,
        "business_name": invoice.business.name if invoice.business else None,
    }


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 1: GET /api/v1/invoices — List All
# ═══════════════════════════════════════════════════════════════
@router.get("/api/v1/invoices")
def list_invoices(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all invoices (admin sees all, business owner sees own)."""
    biz_filter = get_business_filter(current_user)
    query = db.query(Invoice)
    if biz_filter is not None:
        query = query.filter(Invoice.business_id == biz_filter)
    invoices = query.order_by(Invoice.created_at.desc()).all()
    return [invoice_to_dict(inv) for inv in invoices]


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 2: GET /api/v1/invoices/{id} — Get Single
# ═══════════════════════════════════════════════════════════════
@router.get("/api/v1/invoices/{invoice_id}")
def get_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a single invoice by its ID."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    # Business owners can only see their own invoices
    biz_filter = get_business_filter(current_user)
    if biz_filter is not None and invoice.business_id != biz_filter:
        raise HTTPException(status_code=403, detail="Access denied")
    return invoice_to_dict(invoice)


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 3: GET /api/v1/businesses/{bid}/invoices
# ═══════════════════════════════════════════════════════════════
@router.get("/api/v1/businesses/{business_id}/invoices")
def list_business_invoices(
    business_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all invoices for a specific business."""
    # Business owners can only access their own business
    biz_filter = get_business_filter(current_user)
    if biz_filter is not None and business_id != biz_filter:
        raise HTTPException(status_code=403, detail="Access denied")
    invoices = (
        db.query(Invoice)
        .filter(Invoice.business_id == business_id)
        .order_by(Invoice.created_at.desc())
        .all()
    )
    return [invoice_to_dict(inv) for inv in invoices]


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 4: POST /api/v1/invoices — Create Draft
# ═══════════════════════════════════════════════════════════════
@router.post("/api/v1/invoices")
def create_invoice(
    payload: InvoiceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new invoice in DRAFT status.

    The system automatically:
    1. Calculates VAT (18%) and total amount
    2. Checks if the amount requires an ITA allocation number
    3. Generates a unique invoice number
    """

    # ── Verify the business exists ──
    business = db.query(Business).filter(Business.id == payload.business_id).first()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    # ── Calculate VAT (18%) ──
    vat_info = calculate_vat(payload.amount_net)

    # ── Check tax compliance (does this need an allocation number?) ──
    compliance = check_tax_compliance(payload.amount_net)
    requires_allocation = 1 if compliance["requires_allocation"] else 0
    allocation_status = "pending" if compliance["requires_allocation"] else "not_required"

    # ── Generate invoice number ──
    existing_count = (
        db.query(Invoice)
        .filter(Invoice.business_id == payload.business_id)
        .count()
    )
    invoice_number = generate_invoice_number(payload.business_id, existing_count)

    # ── Create the invoice record ──
    invoice = Invoice(
        business_id=payload.business_id,
        invoice_number=invoice_number,
        beneficiary_name=payload.beneficiary_name,
        beneficiary_tax_id=payload.beneficiary_tax_id,
        beneficiary_contact=payload.beneficiary_contact,
        amount_net=vat_info["amount_net"],
        vat_rate=vat_info["vat_rate"],
        vat_amount=vat_info["vat_amount"],
        amount_total=vat_info["amount_total"],
        requires_allocation=requires_allocation,
        allocation_status=allocation_status,
        description=payload.description,
        status="draft",
    )

    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    print(f"[INVOICE] Created draft invoice {invoice_number} for business {business.name}")
    print(f"[INVOICE] Amount: {vat_info['amount_net']} + VAT {vat_info['vat_amount']} = {vat_info['amount_total']}")
    print(f"[INVOICE] Requires allocation: {'Yes' if requires_allocation else 'No'} (threshold: {compliance['threshold']})")

    # ── Log the action ──
    log = ActionLog(
        business_id=payload.business_id,
        status="created",
        detail=f"Invoice {invoice_number} created (draft) — {vat_info['amount_total']} ILS",
    )
    db.add(log)
    db.commit()

    return invoice_to_dict(invoice)


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 5: POST /api/v1/invoices/{id}/finalize
# ═══════════════════════════════════════════════════════════════
@router.post("/api/v1/invoices/{invoice_id}/finalize")
async def finalize_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Finalize a draft invoice.

    If the invoice requires an allocation number (above the threshold),
    the system contacts the ITA (mock) to obtain one.

    The actual logic lives in app/services/invoice_service.py so it
    can be shared with the Telegram bot without duplication.
    """
    # ── Verify access rights (business owners can only touch their own) ──
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    biz_filter = get_business_filter(current_user)
    if biz_filter is not None and invoice.business_id != biz_filter:
        raise HTTPException(status_code=403, detail="Access denied")

    # ── Delegate to invoice_service ──
    try:
        return await _finalize_invoice_logic(
            db=db,
            invoice_id=invoice_id,
            lang="ar",                   # Dashboard always generates Arabic PDF
            actor_label="dashboard",
        )
    except InvoiceNotFoundError:
        raise HTTPException(status_code=404, detail="Invoice not found")
    except InvoiceStateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AllocationFailedError:
        raise HTTPException(
            status_code=502,
            detail="ITA service temporarily unavailable. Try again.",
        )


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 6: POST /api/v1/invoices/{id}/send-whatsapp
# ═══════════════════════════════════════════════════════════════
@router.post("/api/v1/invoices/{invoice_id}/send-whatsapp")
async def send_invoice_whatsapp(
    invoice_id: int,
    payload: SendWhatsAppRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Send a finalized invoice to the customer via WhatsApp.
    The invoice must be finalized before it can be sent.
    """

    # ── Find the invoice ──
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if invoice.status not in ("finalized", "sent"):
        raise HTTPException(
            status_code=400,
            detail="Invoice must be finalized before sending",
        )

    # ── Send via WhatsApp ──
    invoice_data = invoice_to_dict(invoice)
    result = await send_invoice_via_whatsapp(invoice_data, payload.recipient_phone)

    # ── Mark sent via the central state machine (finalized → sent; idempotent
    #    no-op if already sent). Stamps sent_at + writes the audit row. ──
    transition(
        db, invoice, "sent",
        actor=f"dashboard:{getattr(current_user, 'email', current_user.id)}",
        reason=f"WhatsApp → {payload.recipient_phone}",
    )

    return {
        "message": f"Invoice {invoice.invoice_number} sent to {payload.recipient_phone}",
        "make_response": result,
    }


# ═══════════════════════════════════════════════════════════════
# ENDPOINT: POST /api/v1/invoices/{id}/cancel — void a draft/pending invoice
# ═══════════════════════════════════════════════════════════════
@router.post("/api/v1/invoices/{invoice_id}/cancel")
def cancel_invoice_endpoint(
    invoice_id: int,
    payload: CancelInvoiceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel (void) a draft or pending_allocation invoice.

    Finalized/sent invoices are tax-locked — reverse them with a credit note
    (returns 409)."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    biz_filter = get_business_filter(current_user)
    if biz_filter is not None and invoice.business_id != biz_filter:
        raise HTTPException(status_code=404, detail="Invoice not found")

    try:
        cancel_invoice(
            db, invoice,
            reason=(payload.reason or "cancelled via dashboard"),
            actor=f"dashboard:{getattr(current_user, 'email', current_user.id)}",
        )
    except InvoiceTransitionError as e:
        raise HTTPException(
            status_code=409,
            detail={"error": "invoice_tax_locked", "message": str(e)},
        )

    return {
        "message": f"Invoice {invoice.invoice_number} cancelled",
        "status": invoice.status,
    }


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 7: GET /api/v1/logs — Activity Logs
# ═══════════════════════════════════════════════════════════════
@router.get("/api/v1/logs")
def list_logs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return recent activity logs (last 100 entries)."""
    biz_filter = get_business_filter(current_user)
    query = db.query(ActionLog)
    if biz_filter is not None:
        query = query.filter(ActionLog.business_id == biz_filter)
    logs = (
        query
        .order_by(ActionLog.triggered_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": log.id,
            "business_id": log.business_id,
            "status": log.status,
            "detail": log.detail,
            "triggered_at": log.triggered_at.isoformat() if log.triggered_at else None,
        }
        for log in logs
    ]
