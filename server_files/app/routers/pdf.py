"""
ASG Solutions -- PDF Router
================================
Endpoints for downloading and regenerating invoice PDFs.

ENDPOINTS:
  GET  /api/v1/pdf/{invoice_id}/download    -- Download invoice as PDF
  POST /api/v1/pdf/{invoice_id}/regenerate  -- Regenerate PDF (admin only)

SECURITY:
  All endpoints require JWT (get_current_user).
  Business owners can only access their own invoices (get_business_filter).
  Regenerate is admin-only (require_admin).
"""

# -----------------------------------------------------------------
# IMPORTS
# -----------------------------------------------------------------
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from aurora_shared.database import get_db, Invoice, Business, User
from aurora_shared.middleware.auth_middleware import (
    get_current_user,
    get_business_filter,
    require_admin,
)
from app.services.pdf_service import generate_invoice_pdf, get_invoice_pdf_path

import os

# -----------------------------------------------------------------
# ROUTER
# -----------------------------------------------------------------
router = APIRouter(tags=["PDF"])


# =================================================================
# HELPER: build invoice + business dicts for pdf_service
# =================================================================
def _invoice_dict(invoice: Invoice) -> dict:
    return {
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


def _business_dict(business: Business) -> dict:
    return {
        "name": business.name,
        "tax_id": business.tax_id,
        "address": business.address,
        "logo_url": business.logo_url,
    }


# =================================================================
# ENDPOINT 1: GET /api/v1/pdf/{invoice_id}/download
# =================================================================
@router.get("/api/v1/pdf/{invoice_id}/download")
def download_pdf(
    invoice_id: int,
    lang: str = "ar",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Download the PDF for an invoice.

    If the PDF doesn't exist yet, it is generated on the fly.
    The optional ?lang= query param controls the language:
      ar = Arabic (default, RTL)
      he = Hebrew (RTL)
      en = English (LTR)

    Example: GET /api/v1/pdf/1/download?lang=ar
    """

    # ── Find invoice + verify access ──
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    biz_filter = get_business_filter(current_user)
    if biz_filter is not None and invoice.business_id != biz_filter:
        raise HTTPException(status_code=403, detail="Access denied")

    # ── Only finalized/sent invoices get PDFs ──
    if invoice.status not in ("finalized", "sent"):
        raise HTTPException(
            status_code=400,
            detail="PDF is only available for finalized or sent invoices",
        )

    # ── Validate lang ──
    if lang not in ("ar", "he", "en"):
        lang = "ar"

    # ── Get or generate PDF ──
    pdf_url = invoice.pdf_url or get_invoice_pdf_path(invoice.invoice_number)

    if not pdf_url:
        # Generate now
        business = db.query(Business).filter(Business.id == invoice.business_id).first()
        pdf_url = generate_invoice_pdf(
            invoice_data=_invoice_dict(invoice),
            business_data=_business_dict(business),
            lang=lang,
        )
        # Save PDF url to invoice record
        invoice.pdf_url = pdf_url
        db.commit()

    # ── Build absolute disk path ──
    # pdf_url is like "/static/pdfs/INV-1-0001.pdf"
    # Disk path is "app/static/pdfs/INV-1-0001.pdf"
    disk_path = "app" + pdf_url  # "/static/pdfs/..." → "app/static/pdfs/..."
    if not os.path.exists(disk_path):
        raise HTTPException(status_code=404, detail="PDF file not found on disk")

    filename = os.path.basename(disk_path)
    return FileResponse(
        path=disk_path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# =================================================================
# ENDPOINT 2: POST /api/v1/pdf/{invoice_id}/regenerate
# =================================================================
@router.post("/api/v1/pdf/{invoice_id}/regenerate")
def regenerate_pdf(
    invoice_id: int,
    lang: str = "ar",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Regenerate the PDF for an invoice (admin only).
    Useful after template changes or if the file was lost.
    Optional ?lang=ar|he|en query param.
    """
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if invoice.status not in ("finalized", "sent"):
        raise HTTPException(status_code=400, detail="Only finalized invoices can have PDFs")

    if lang not in ("ar", "he", "en"):
        lang = "ar"

    business = db.query(Business).filter(Business.id == invoice.business_id).first()

    pdf_url = generate_invoice_pdf(
        invoice_data=_invoice_dict(invoice),
        business_data=_business_dict(business),
        lang=lang,
    )

    invoice.pdf_url = pdf_url
    db.commit()

    return {
        "message": "PDF regenerated",
        "invoice_number": invoice.invoice_number,
        "pdf_url": pdf_url,
        "lang": lang,
    }
