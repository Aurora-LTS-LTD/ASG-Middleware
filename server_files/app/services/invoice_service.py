"""
ASG Solutions — Invoice Service
=================================
Business logic for creating and finalizing invoices.
This file is the single source of truth for "what happens when an
invoice is finalized." Both the REST API router and the Telegram bot
call these functions instead of duplicating the logic.

WHY IT EXISTS:
  The finalize logic used to live inside app/routers/invoices.py.
  That was fine when only the dashboard used it. But now the
  Telegram bot also needs to finalize invoices — and we can't
  duplicate 60 lines of tax/allocation/PDF logic. Extract once,
  call from everywhere.

REAL-WORLD ANALOGY:
  Think of this as the "back office" of an accounting firm.
  The front desk (REST router) and the phone line (Telegram bot)
  both route paperwork to the same back office team (this file).
  The back office always follows the same checklist regardless of
  who submitted the paperwork.
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import datetime

from sqlalchemy.orm import Session

from app.database import Invoice, Business, ActionLog
from app.services.tax_compliance import (
    calculate_vat,
    check_tax_compliance,
    generate_invoice_number,
)
# Sprint 3 — dispatcher reads ITA_BACKEND env to choose mock vs production.
# Existing call sites pass the same 4 positional args; the new client
# extends the signature with optional invoice_id + retry_count kwargs
# that drive idempotency on the production path.
from app.services.ita import request_allocation_number


# ─────────────────────────────────────────────────────────────
# CUSTOM EXCEPTION
# ─────────────────────────────────────────────────────────────
class AllocationFailedError(Exception):
    """
    Raised when the ITA (Israel Tax Authority) service refuses
    or is temporarily unavailable when requesting an allocation number.

    The REST router catches this → returns HTTP 502.
    The Telegram bot catches this → queues a retry instead.

    This separation is why we use a custom exception instead of
    raising HTTPException directly from the service.
    """
    pass


class InvoiceNotFoundError(Exception):
    """Raised when the requested invoice does not exist."""
    pass


class InvoiceStateError(Exception):
    """Raised when an operation is invalid for the invoice's current state."""
    def __init__(self, current_status: str):
        self.current_status = current_status
        super().__init__(f"Invoice is already '{current_status}' — only drafts can be finalized")


# ─────────────────────────────────────────────────────────────
# HELPER: invoice → dict
# ─────────────────────────────────────────────────────────────
def invoice_to_dict(invoice: Invoice) -> dict:
    """
    Convert an Invoice DB model to a plain dictionary.
    Used by both the router and the Telegram bot to format responses.
    """
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
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "payment_status": invoice.payment_status or "unpaid",
        "amount_paid": invoice.amount_paid or 0.0,
        "business_name": invoice.business.name if invoice.business else None,
    }


# ─────────────────────────────────────────────────────────────
# FUNCTION: create_draft_invoice
# ─────────────────────────────────────────────────────────────
def create_draft_invoice(
    db: Session,
    business_id: int,
    beneficiary_name: str,
    amount_net: float,
    beneficiary_tax_id: str | None = None,
    beneficiary_contact: str | None = None,
    description: str | None = None,
) -> dict:
    """
    Create a new invoice in DRAFT status.

    Applies the 2026 Israeli tax rules (18% VAT, thresholds) and
    generates a unique invoice number. Does NOT contact the ITA yet —
    that happens at finalization time.

    RETURNS: dict representation of the new invoice
    RAISES:  ValueError if business_id does not exist
    """

    # ── Verify the business exists ──
    business = db.query(Business).filter(Business.id == business_id).first()
    if not business:
        raise ValueError(f"Business {business_id} not found")

    # ── Apply 18% VAT (tax_compliance is the single source of truth) ──
    vat_info = calculate_vat(amount_net)

    # ── Does this invoice need an ITA allocation number? ──
    compliance = check_tax_compliance(amount_net)
    requires_allocation = 1 if compliance["requires_allocation"] else 0
    allocation_status = "pending" if compliance["requires_allocation"] else "not_required"

    # ── Generate invoice number (e.g. "INV-3-0007") ──
    existing_count = (
        db.query(Invoice)
        .filter(Invoice.business_id == business_id)
        .count()
    )
    invoice_number = generate_invoice_number(business_id, existing_count)

    # ── Create and persist the invoice ──
    invoice = Invoice(
        business_id=business_id,
        invoice_number=invoice_number,
        beneficiary_name=beneficiary_name,
        beneficiary_tax_id=beneficiary_tax_id,
        beneficiary_contact=beneficiary_contact,
        amount_net=vat_info["amount_net"],
        vat_rate=vat_info["vat_rate"],
        vat_amount=vat_info["vat_amount"],
        amount_total=vat_info["amount_total"],
        requires_allocation=requires_allocation,
        allocation_status=allocation_status,
        description=description,
        status="draft",
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    # ── Log the action ──
    db.add(ActionLog(
        business_id=business_id,
        status="created",
        detail=f"Invoice {invoice_number} created (draft) — {vat_info['amount_total']:.2f} ILS",
    ))
    db.commit()

    # ── Publish ExecEvent for the CEO Alert Stream (defensive — never blocks)
    try:
        from app.services.exec_events import publish_exec_event
        publish_exec_event(
            db,
            kind="invoice_draft_created",
            severity="info",
            title=f"Draft invoice {invoice_number} created (₪{vat_info['amount_total']:,.2f})",
            detail=f"business_id={business_id} beneficiary={beneficiary_name}",
            related_entity_type="invoice",
            related_entity_id=invoice.id,
        )
    except Exception:
        pass

    print(f"[INVOICE_SERVICE] Draft created: {invoice_number} | {vat_info['amount_total']:.2f} ILS")
    return invoice_to_dict(invoice)


# ─────────────────────────────────────────────────────────────
# FUNCTION: finalize_invoice
# ─────────────────────────────────────────────────────────────
async def finalize_invoice(
    db: Session,
    invoice_id: int,
    lang: str = "ar",
    actor_label: str = "dashboard",
) -> dict:
    """
    Finalize a draft invoice: lock the amounts, request ITA allocation
    if needed, set due date, and generate the PDF.

    PARAMETERS:
      db          — database session
      invoice_id  — which invoice to finalize
      lang        — language for the PDF ("ar", "he", "en")
      actor_label — who triggered this ("dashboard", "telegram_bot") for logs

    RETURNS: dict representation of the finalized invoice

    RAISES:
      InvoiceNotFoundError  — invoice doesn't exist
      InvoiceStateError     — invoice is not in "draft" status
      AllocationFailedError — ITA service unavailable (caller decides how to handle)
    """

    # ── Find the invoice ──
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise InvoiceNotFoundError(f"Invoice {invoice_id} not found")

    if invoice.status != "draft":
        raise InvoiceStateError(invoice.status)

    # ── Request allocation number if needed ──
    if invoice.requires_allocation == 1 and invoice.allocation_status == "pending":
        print(f"[INVOICE_SERVICE] Requesting allocation for {invoice.invoice_number} via {actor_label}...")

        seller_tax_id = "000000000"  # TODO: read from business.tax_id when profile is set
        buyer_tax_id = invoice.beneficiary_tax_id or "000000000"

        # Sprint 3 — pass idempotency context to the new dispatcher.
        # Mock backend ignores the kwargs; production backend uses them
        # for the JWT jti claim + X-Request-Id header so retries don't
        # over-allocate at ITA's end.
        ita_response = await request_allocation_number(
            seller_tax_id=seller_tax_id,
            buyer_tax_id=buyer_tax_id,
            amount=invoice.amount_total,
            invoice_id=invoice.id,
            retry_count=invoice.allocation_retry_count or 0,
            organization_id=getattr(invoice, "organization_id", None),
        )

        # Sprint 3 — record audit trail on the invoice itself
        invoice.ita_request_id = ita_response.get("request_id")
        invoice.ita_status_code = ita_response.get("http_status")
        invoice.ita_response_raw_json = ita_response.get("raw_response_summary")

        if ita_response["success"]:
            invoice.allocation_number = ita_response["allocation_number"]
            invoice.allocation_status = "approved"
            invoice.allocation_issued_at = datetime.datetime.utcnow()
            print(f"[INVOICE_SERVICE] ✅ Allocation approved: {ita_response['allocation_number']}")
        else:
            # Mark as failed but do NOT finalize yet.
            # The caller decides what to do:
            #   - REST router → raise HTTP 502
            #   - Telegram bot → queue retry, keep as draft + pending_allocation
            invoice.allocation_status = "failed"
            db.commit()
            print(f"[INVOICE_SERVICE] ❌ Allocation FAILED for {invoice.invoice_number}")
            raise AllocationFailedError(
                f"ITA service unavailable for invoice {invoice.invoice_number}"
            )

    # ── Lock the invoice ──
    invoice.status = "finalized"
    invoice.finalized_at = datetime.datetime.utcnow()
    invoice.due_date = datetime.datetime.utcnow() + datetime.timedelta(days=30)
    db.commit()
    db.refresh(invoice)

    # ── Publish ExecEvent for the CEO Alert Stream (defensive)
    try:
        from app.services.exec_events import publish_exec_event
        # Larger amount → higher severity so the CEO sees it surfaced.
        sev = "warning" if (invoice.amount_total or 0) >= 10000 else "info"
        publish_exec_event(
            db,
            kind="invoice_finalized",
            severity=sev,
            title=f"Invoice {invoice.invoice_number} finalized (₪{invoice.amount_total or 0:,.2f})",
            detail=(
                f"business_id={invoice.business_id} actor={actor_label} "
                f"allocation_status={invoice.allocation_status}"
            ),
            related_entity_type="invoice",
            related_entity_id=invoice.id,
        )
    except Exception:
        pass

    # ── Generate PDF (non-fatal: PDF failure never blocks finalization) ──
    try:
        from app.services.pdf_service import generate_invoice_pdf
        business = db.query(Business).filter(Business.id == invoice.business_id).first()
        business_data = {
            "name": business.name if business else "",
            "tax_id": getattr(business, "tax_id", None),
            "address": getattr(business, "address", None),
            "logo_url": getattr(business, "logo_url", None),
        }
        # Build the data dict that pdf_service expects
        inv_data = {
            "id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "business_id": invoice.business_id,
            "beneficiary_name": invoice.beneficiary_name,
            "beneficiary_tax_id": invoice.beneficiary_tax_id,
            "beneficiary_contact": invoice.beneficiary_contact,
            "amount_net": invoice.amount_net,
            "vat_rate": invoice.vat_rate,
            "vat_amount": invoice.vat_amount,
            "amount_total": invoice.amount_total,
            "currency": invoice.currency,
            "allocation_number": invoice.allocation_number,
            "allocation_status": invoice.allocation_status,
            "status": invoice.status,
            "description": invoice.description,
            "payment_status": invoice.payment_status or "unpaid",
            "amount_paid": invoice.amount_paid or 0.0,
            "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
            "finalized_at": invoice.finalized_at.isoformat() if invoice.finalized_at else None,
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        }
        pdf_url = generate_invoice_pdf(inv_data, business_data, lang=lang)
        invoice.pdf_url = pdf_url
        db.commit()
        db.refresh(invoice)
        print(f"[INVOICE_SERVICE] PDF generated: {pdf_url}")
    except Exception as e:
        print(f"[INVOICE_SERVICE] ⚠️ PDF generation failed (non-fatal): {e}")

    # ── Log the action ──
    alloc_detail = f" | Allocation: {invoice.allocation_number}" if invoice.allocation_number else ""
    db.add(ActionLog(
        business_id=invoice.business_id,
        status="finalized",
        detail=f"Invoice {invoice.invoice_number} finalized via {actor_label}{alloc_detail}",
    ))
    db.commit()

    print(f"[INVOICE_SERVICE] ✅ {invoice.invoice_number} finalized!")
    return invoice_to_dict(invoice)
