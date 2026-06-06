"""
Aurora LTS — Receipt OCR Pipeline Orchestrator
=================================================
Sprint 2 — single entry point `process_receipt(...)` that takes the raw
bytes of an uploaded receipt and produces a persisted Receipt + (when
appropriate) a draft Expense.

THE PIPELINE (mirrors Part II Sprint 2 spec):

       ┌─────────────────────────────────────────┐
       │ 1. SHA-256 → org-scoped dedup           │  → if duplicate, return EXISTING outcome
       │ 2. Cloud DLP scan                        │  → if PII, persist as quarantined, return
       │ 3. Upload bytes to GCS                   │
       │ 4. Document AI Expense Parser            │  → may raise OcrError
       │ 5. Compute confidence_min                │
       │ 6. Persist Receipt (+ Expense if route≠failure/quarantine)
       │ 7. Route by confidence (AUTO/LIGHT/HEAVY)│
       └─────────────────────────────────────────┘

PUBLIC SHAPE:
    outcome = process_receipt(
        organization_id=42, user_id=7,
        mime_type="image/jpeg",
        image_bytes=b"...",
        db=db,
        source="whatsapp", source_message_id="wamid.XXX",
    )
    outcome.status   → ReceiptOutcomeStatus
    outcome.route    → ReceiptRoute
    outcome.receipt  → Receipt (always set, even on quarantine/failure)
    outcome.expense  → Expense | None (None on quarantine/failure)
    outcome.parse    → ExpenseParseResult | None

DESIGN NOTES:
  - The same pipeline serves WhatsApp (image type), the dashboard upload
    (when shipped), and the accountant portal — anyone with `image_bytes`
    + an `organization_id` can call it.
  - All persistence is COMMITTED inside this function. Callers receive
    a refreshed Receipt + Expense ready to read.
  - Errors NEVER raise out — the ReceiptOutcomeStatus enum covers every
    branch the caller needs. This means downstream WhatsApp / API code
    has a single shape to switch on.
"""

import datetime
import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sqlalchemy.orm import Session

from aurora_shared.database import (
    Receipt,
    Expense,
    User,
    ActionLog,
)
from app.services.gcp import (
    upload_bytes,
    sha256_object_key,
    parse_expense,
    ExpenseParseResult,
    scan_image,
)
from app.services.gcp.document_ai import OcrError
from app.services.gcp.storage import sha256_of
from app.services.receipts.confidence import (
    ReceiptRoute,
    route_by_confidence,
    to_ocr_status,
)


# ─────────────────────────────────────────────────────────────
# Outcome shape
# ─────────────────────────────────────────────────────────────
class ReceiptOutcomeStatus(str, Enum):
    """High-level result of a process_receipt() call."""
    OK = "ok"                         # Receipt persisted, normal route applies
    DUPLICATE = "duplicate"           # Same sha256 already exists for this org
    QUARANTINED = "quarantined"       # DLP rejected the upload
    OCR_FAILED = "ocr_failed"         # Document AI errored
    INVALID_INPUT = "invalid_input"   # Pre-flight validation failed


@dataclass
class ReceiptParseOutcome:
    status: ReceiptOutcomeStatus
    route: ReceiptRoute
    receipt: Receipt                   # always set (the Receipt row)
    expense: Optional[Expense] = None  # set when route ≠ OCR_FAILURE / DLP_QUARANTINE
    parse: Optional[ExpenseParseResult] = None
    duplicate_of_id: Optional[str] = None  # set when status == DUPLICATE
    error_message: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _ext_from_mime(mime: str) -> str:
    return {
        "image/jpeg": "jpg", "image/jpg": "jpg",
        "image/png": "png",
        "image/heic": "heic", "image/heif": "heif",
        "application/pdf": "pdf",
    }.get((mime or "").lower(), "bin")


def _exists_for_org(db: Session, organization_id: int, sha256_hex: str) -> Optional[Receipt]:
    return (
        db.query(Receipt)
        .filter(
            Receipt.organization_id == organization_id,
            Receipt.sha256 == sha256_hex,
        )
        .first()
    )


# ─────────────────────────────────────────────────────────────
# Public — process_receipt
# ─────────────────────────────────────────────────────────────
def process_receipt(
    *,
    organization_id: int,
    user_id: int,
    mime_type: str,
    image_bytes: bytes,
    db: Session,
    source: str = "whatsapp",
    source_message_id: Optional[str] = None,
) -> ReceiptParseOutcome:
    """
    Run the full pipeline. Always returns a ReceiptParseOutcome — never
    raises out of the boundary. WhatsApp / API callers switch on
    outcome.status + outcome.route to decide what to say next.
    """
    # ── Pre-flight ──
    if not image_bytes:
        return ReceiptParseOutcome(
            status=ReceiptOutcomeStatus.INVALID_INPUT,
            route=ReceiptRoute.OCR_FAILURE,
            receipt=None,  # type: ignore  — exceptional case; caller checks status first
            error_message="empty image_bytes",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return ReceiptParseOutcome(
            status=ReceiptOutcomeStatus.INVALID_INPUT,
            route=ReceiptRoute.OCR_FAILURE,
            receipt=None,  # type: ignore
            error_message=f"user_id={user_id} not found",
        )

    # ── 1. Dedup by sha256 ──
    sha = sha256_of(image_bytes)
    existing = _exists_for_org(db, organization_id, sha)
    if existing:
        # Re-load the linked Expense, if any, and return the existing outcome
        existing_expense = (
            db.query(Expense)
            .filter(Expense.receipt_id == existing.id)
            .first()
        )
        return ReceiptParseOutcome(
            status=ReceiptOutcomeStatus.DUPLICATE,
            route=_route_from_status(existing.ocr_status),
            receipt=existing,
            expense=existing_expense,
            duplicate_of_id=existing.id,
        )

    # ── 2. Cloud DLP scan ──
    dlp = scan_image(image_bytes=image_bytes, mime_type=mime_type)

    # ── 3. Upload to GCS (or stub) — done BEFORE OCR so the bytes are
    #       persisted even if Document AI fails. Quarantined uploads
    #       still get a row in the bucket so admins can audit them. ──
    object_key = sha256_object_key(
        organization_id=organization_id,
        sha256_hex=sha,
        extension=_ext_from_mime(mime_type),
    )
    try:
        gcs_uri = upload_bytes(
            object_key=object_key,
            data=image_bytes,
            mime_type=mime_type,
        )
    except Exception as e:
        # Storage failure is rare but possible — return a clear outcome.
        receipt = _create_receipt_row(
            db=db, organization_id=organization_id, user_id=user.id,
            mime_type=mime_type, bytes_size=len(image_bytes), sha256_hex=sha,
            object_key=object_key, gcs_uri=f"failed://{object_key}",
            ocr_status="failed", source=source, source_message_id=source_message_id,
            dlp_clean=dlp.clean, dlp_findings=dlp.findings,
        )
        db.commit()
        return ReceiptParseOutcome(
            status=ReceiptOutcomeStatus.OCR_FAILED,
            route=ReceiptRoute.OCR_FAILURE,
            receipt=receipt,
            error_message=f"upload failed: {e}",
        )

    # ── DLP-positive short-circuit: persist Receipt as quarantined,
    #    DO NOT call Document AI, DO NOT create an Expense. ──
    if not dlp.clean:
        receipt = _create_receipt_row(
            db=db, organization_id=organization_id, user_id=user.id,
            mime_type=mime_type, bytes_size=len(image_bytes), sha256_hex=sha,
            object_key=object_key, gcs_uri=gcs_uri,
            ocr_status=to_ocr_status(ReceiptRoute.DLP_QUARANTINE),
            source=source, source_message_id=source_message_id,
            dlp_clean=False, dlp_findings=dlp.findings,
            confidence_min=None, raw_response_json=None, doc_ai_job_id=None,
            parsed_at=datetime.datetime.utcnow(),
        )
        db.add(ActionLog(
            business_id=None,
            status="receipt.dlp_quarantined",
            detail=(
                f"receipt_id={receipt.id} org_id={organization_id} "
                f"reason={dlp.quarantine_reason()!r}"
            ),
        ))
        db.commit()
        return ReceiptParseOutcome(
            status=ReceiptOutcomeStatus.QUARANTINED,
            route=ReceiptRoute.DLP_QUARANTINE,
            receipt=receipt,
            error_message=dlp.quarantine_reason(),
        )

    # ── 4. Document AI (OCR) ──
    parse: Optional[ExpenseParseResult] = None
    ocr_error: Optional[str] = None
    try:
        parse = parse_expense(
            image_bytes=image_bytes,
            mime_type=mime_type,
            gcs_uri=gcs_uri,
        )
    except OcrError as e:
        ocr_error = str(e)

    # ── 5. Route by confidence ──
    route = route_by_confidence(parse) if parse else ReceiptRoute.OCR_FAILURE
    ocr_status = to_ocr_status(route)
    confidence_min = parse.confidence_min if parse else None

    # ── 6. Persist Receipt ──
    receipt = _create_receipt_row(
        db=db, organization_id=organization_id, user_id=user.id,
        mime_type=mime_type, bytes_size=len(image_bytes), sha256_hex=sha,
        object_key=object_key, gcs_uri=gcs_uri,
        ocr_status=ocr_status, source=source, source_message_id=source_message_id,
        dlp_clean=dlp.clean, dlp_findings=dlp.findings,
        confidence_min=confidence_min,
        raw_response_json=parse.raw_response_json if parse else None,
        doc_ai_job_id=parse.document_ai_job_id if parse else None,
        parsed_at=datetime.datetime.utcnow(),
    )
    db.flush()

    # ── 6b. Persist Expense (skip on OCR failure) ──
    expense: Optional[Expense] = None
    if route != ReceiptRoute.OCR_FAILURE and parse is not None:
        # Auto-categorise via Gemini Flash (or heuristic fallback in stub mode)
        proposed_category: Optional[str] = None
        category_notes: Optional[str] = None
        try:
            from app.services.gcp.gemini import categorise_expense
            from aurora_shared.database import Organization
            org = db.query(Organization).filter(Organization.id == organization_id).first()
            industry = org.industry_code if org else None
            cat_result = categorise_expense(
                supplier_name=parse.supplier_name,
                total_amount_minor_units=parse.total_amount_minor_units or 0,
                raw_text=parse.raw_response_json or "",
                org_industry_hint=industry,
            )
            # Only auto-set when confidence is reasonable; otherwise leave NULL
            # for the accountant to assign.
            if cat_result["confidence"] >= 0.5:
                proposed_category = cat_result["category"]
            category_notes = (
                f"[auto:{cat_result['backend']} conf={cat_result['confidence']:.2f}] "
                f"{cat_result['rationale']}"
            )[:500]
        except Exception as e:
            print(f"[PIPELINE] Categorisation failed (non-fatal): {e}")

        expense = Expense(
            organization_id=organization_id,
            receipt_id=receipt.id,
            supplier_name=parse.supplier_name,
            supplier_tax_id=parse.supplier_tax_id,
            total_amount_minor_units=parse.total_amount_minor_units,
            vat_amount_minor_units=parse.vat_amount_minor_units,
            currency=parse.currency or "ILS",
            expense_date=parse.receipt_date,
            category=proposed_category,
            status="draft",
            notes=category_notes,
        )
        db.add(expense)
        db.flush()

    # ── Audit trail ──
    db.add(ActionLog(
        business_id=None,
        status=f"receipt.{ocr_status}",
        detail=(
            f"receipt_id={receipt.id} org_id={organization_id} "
            f"user_id={user.id} sha256={sha[:12]}… "
            f"conf_min={confidence_min} route={route.value} "
            f"expense_id={expense.id if expense else None}"
            + (f" ocr_error={ocr_error!r}" if ocr_error else "")
        ),
    ))
    db.commit()
    db.refresh(receipt)
    if expense:
        db.refresh(expense)

    status = ReceiptOutcomeStatus.OCR_FAILED if route == ReceiptRoute.OCR_FAILURE else ReceiptOutcomeStatus.OK
    return ReceiptParseOutcome(
        status=status,
        route=route,
        receipt=receipt,
        expense=expense,
        parse=parse,
        error_message=ocr_error,
    )


# ─────────────────────────────────────────────────────────────
# Public — confirm_expense
# ─────────────────────────────────────────────────────────────
def confirm_expense(
    *,
    expense_id: int,
    confirmed_by_user_id: int,
    db: Session,
    notes: Optional[str] = None,
) -> Expense:
    """
    Flip an Expense to status='confirmed'. Idempotent: re-confirming
    a confirmed expense is a no-op.
    """
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if not expense:
        raise ValueError(f"expense_id={expense_id} not found")
    if expense.status == "confirmed":
        return expense
    if expense.status == "rejected":
        raise ValueError("Cannot confirm a rejected expense — file a new one")

    expense.status = "confirmed"
    expense.confirmed_by_user_id = confirmed_by_user_id
    expense.confirmed_at = datetime.datetime.utcnow()
    if notes is not None:
        expense.notes = notes

    db.add(ActionLog(
        business_id=None,
        status="expense.confirmed",
        detail=f"expense_id={expense.id} by_user_id={confirmed_by_user_id}",
    ))
    db.commit()
    db.refresh(expense)
    return expense


def reject_expense(
    *,
    expense_id: int,
    rejected_by_user_id: int,
    reason: str,
    db: Session,
) -> Expense:
    """Flip an Expense to status='rejected'. Idempotent."""
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if not expense:
        raise ValueError(f"expense_id={expense_id} not found")
    if expense.status == "rejected":
        return expense

    expense.status = "rejected"
    expense.confirmed_by_user_id = rejected_by_user_id  # bookkeeping
    expense.confirmed_at = datetime.datetime.utcnow()
    expense.rejection_reason = (reason or "")[:500]

    db.add(ActionLog(
        business_id=None,
        status="expense.rejected",
        detail=f"expense_id={expense.id} reason={reason!r} by_user_id={rejected_by_user_id}",
    ))
    db.commit()
    db.refresh(expense)
    return expense


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────
def _create_receipt_row(
    *, db: Session, organization_id: int, user_id: int,
    mime_type: str, bytes_size: int, sha256_hex: str,
    object_key: str, gcs_uri: str,
    ocr_status: str, source: str, source_message_id: Optional[str],
    dlp_clean: bool, dlp_findings,
    confidence_min: Optional[float] = None,
    raw_response_json: Optional[str] = None,
    doc_ai_job_id: Optional[str] = None,
    parsed_at: Optional[datetime.datetime] = None,
) -> Receipt:
    """Single place that constructs a Receipt row, used in every branch."""
    import os as _os
    bucket = _os.getenv("GCS_BUCKET_RECEIPTS", "asg-receipts-prod")
    findings_json = None
    if dlp_findings:
        findings_json = json.dumps(
            [{"info_type": f.info_type, "likelihood": f.likelihood} for f in dlp_findings],
            ensure_ascii=False,
        )

    receipt = Receipt(
        organization_id=organization_id,
        user_id=user_id,
        gcs_bucket=bucket,
        gcs_object_key=object_key,
        sha256=sha256_hex,
        mime_type=mime_type,
        bytes_size=bytes_size,
        ocr_status=ocr_status,
        ocr_confidence_min=confidence_min,
        ocr_raw_json=raw_response_json,
        document_ai_job_id=doc_ai_job_id,
        dlp_clean=dlp_clean,
        dlp_findings_json=findings_json,
        source=source,
        source_message_id=source_message_id,
        parsed_at=parsed_at,
    )
    db.add(receipt)
    return receipt


def _route_from_status(ocr_status: str) -> ReceiptRoute:
    """Inverse of confidence.to_ocr_status() — maps the persisted string back."""
    return {
        "parsed":           ReceiptRoute.AUTO_APPROVE,
        "review_light":     ReceiptRoute.REVIEW_LIGHT,
        "review_heavy":     ReceiptRoute.REVIEW_HEAVY,
        "failed":           ReceiptRoute.OCR_FAILURE,
        "dlp_quarantined":  ReceiptRoute.DLP_QUARANTINE,
        "pending":          ReceiptRoute.OCR_FAILURE,
    }.get(ocr_status, ReceiptRoute.OCR_FAILURE)
