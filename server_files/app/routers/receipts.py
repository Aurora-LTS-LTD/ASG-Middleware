"""
Aurora LTS — Receipts Router
==============================
Sprint 2 — JSON-only API for the Receipt + Expense pair.

ENDPOINTS (all JWT-protected):

  GET  /api/v1/organizations/{organization_id}/receipts
       List receipts for an org. Filters: status, date_from, date_to,
       limit, offset. require_org_access(min_role=employee).

  GET  /api/v1/receipts/{receipt_id}
       Single receipt with its parsed Expense and a signed URL to
       the image (15-min TTL). Org-scoped via Receipt.organization_id.

  POST /api/v1/receipts/{receipt_id}/confirm
       Flip the linked Expense to status='confirmed'. Org-scoped.

  POST /api/v1/receipts/{receipt_id}/reject
       Flip the linked Expense to status='rejected' with a reason.
       Org-scoped.

  POST /api/v1/receipts/{receipt_id}/manual-update
       Owner / admin override of OCR-parsed fields. Used when the
       user clicks ✏️ Fix in the dashboard / WhatsApp. Org-scoped.

  GET  /api/v1/admin/receipts/review-queue
       Manual-review queue across ALL orgs. Aurora's "first-50 KYC-style"
       hands-on quality assurance pattern, applied to OCR. require_admin.

  POST /api/v1/admin/receipts/{receipt_id}/reparse
       Force a re-OCR of an existing receipt (e.g. after thresholds
       changed or Document AI processor was upgraded). require_admin.

  POST /api/v1/receipts/upload
       Direct multipart upload (the dashboard / accountant portal will
       use this once they ship). Same code path as the WhatsApp flow.
       require_org_access(min_role=employee).

NOTHING IN THIS ROUTER WRITES TO RECEIPT BYTES DIRECTLY.
Everything goes through services/receipts/pipeline.py — the single
source of OCR-pipeline truth.

REAL-WORLD ANALOGY:
  This router is the receipts-clerk window at the office. People can
  ask "show me what's filed for this month" (GET list), inspect a
  specific receipt (GET single), say "yes that's right, file it"
  (confirm), or say "that's wrong, fix it" (reject / manual-update).
  Admins also have a back-office queue of low-confidence parses to
  hand-verify — the "first-50" pattern.
"""

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import (
    get_db,
    User,
    Receipt,
    Expense,
    Organization,
)
from app.middleware.auth_middleware import (
    get_current_user,
    require_admin,
    require_org_access,
)
from app.services.gcp.storage import signed_url
from app.services.receipts import (
    process_receipt,
    confirm_expense as svc_confirm_expense,
    reject_expense as svc_reject_expense,
    ReceiptOutcomeStatus,
)


router = APIRouter(prefix="/api/v1", tags=["receipts"])


# ═══════════════════════════════════════════════════════════════
# REQUEST / RESPONSE SCHEMAS  (Pydantic)
# ═══════════════════════════════════════════════════════════════
class RejectRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class ManualUpdateRequest(BaseModel):
    """Admin / owner override of OCR-parsed Expense fields."""
    supplier_name: Optional[str] = Field(default=None, max_length=200)
    supplier_tax_id: Optional[str] = Field(default=None, max_length=20)
    total_amount_minor_units: Optional[int] = Field(default=None, ge=0)
    vat_amount_minor_units: Optional[int] = Field(default=None, ge=0)
    expense_date: Optional[datetime.date] = None
    category: Optional[str] = Field(default=None, max_length=40)
    notes: Optional[str] = Field(default=None, max_length=2000)


class ReceiptOut(BaseModel):
    id: str
    organization_id: int
    user_id: int
    gcs_uri: str
    sha256: str
    mime_type: Optional[str]
    bytes_size: Optional[int]
    ocr_status: str
    ocr_confidence_min: Optional[float]
    document_ai_job_id: Optional[str]
    dlp_clean: bool
    source: str
    source_message_id: Optional[str]
    created_at: str
    parsed_at: Optional[str]
    expense_id: Optional[int] = None
    image_signed_url: Optional[str] = None  # populated only on /receipts/{id}


class ExpenseOut(BaseModel):
    id: int
    organization_id: int
    receipt_id: Optional[str]
    supplier_name: Optional[str]
    supplier_tax_id: Optional[str]
    total_amount_minor_units: Optional[int]
    vat_amount_minor_units: Optional[int]
    currency: str
    expense_date: Optional[str]
    category: Optional[str]
    status: str
    confirmed_by_user_id: Optional[int]
    confirmed_at: Optional[str]
    rejection_reason: Optional[str]
    notes: Optional[str]
    created_at: str


def _receipt_to_dict(r: Receipt, *, expense_id: Optional[int] = None,
                     image_signed_url: Optional[str] = None) -> dict:
    return {
        "id": r.id,
        "organization_id": r.organization_id,
        "user_id": r.user_id,
        "gcs_uri": _gcs_uri_from(r),
        "sha256": r.sha256,
        "mime_type": r.mime_type,
        "bytes_size": r.bytes_size,
        "ocr_status": r.ocr_status,
        "ocr_confidence_min": r.ocr_confidence_min,
        "document_ai_job_id": r.document_ai_job_id,
        "dlp_clean": bool(r.dlp_clean),
        "source": r.source,
        "source_message_id": r.source_message_id,
        "created_at": r.created_at.isoformat() if r.created_at else "",
        "parsed_at": r.parsed_at.isoformat() if r.parsed_at else None,
        "expense_id": expense_id,
        "image_signed_url": image_signed_url,
    }


def _gcs_uri_from(r: Receipt) -> str:
    """Reconstruct gs://bucket/object_key (we don't store the full URI)."""
    return f"gs://{r.gcs_bucket}/{r.gcs_object_key}"


def _expense_to_dict(e: Expense) -> dict:
    return {
        "id": e.id,
        "organization_id": e.organization_id,
        "receipt_id": e.receipt_id,
        "supplier_name": e.supplier_name,
        "supplier_tax_id": e.supplier_tax_id,
        "total_amount_minor_units": e.total_amount_minor_units,
        "vat_amount_minor_units": e.vat_amount_minor_units,
        "currency": e.currency or "ILS",
        "expense_date": e.expense_date.isoformat() if e.expense_date else None,
        "category": e.category,
        "status": e.status,
        "confirmed_by_user_id": e.confirmed_by_user_id,
        "confirmed_at": e.confirmed_at.isoformat() if e.confirmed_at else None,
        "rejection_reason": e.rejection_reason,
        "notes": e.notes,
        "created_at": e.created_at.isoformat() if e.created_at else "",
    }


# ═══════════════════════════════════════════════════════════════
# Helper: load Receipt + verify org access
# ═══════════════════════════════════════════════════════════════
def _load_receipt_or_404(receipt_id: str, db: Session) -> Receipt:
    r = db.query(Receipt).filter(Receipt.id == receipt_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return r


def _check_org_access(receipt: Receipt, current_user: User, db: Session,
                      *, min_role: str = "employee") -> None:
    """Receipt routes don't carry org_id in the path — verify access manually."""
    from app.services.identity import user_can_access_org
    if not user_can_access_org(current_user, receipt.organization_id, db, min_role=min_role):
        raise HTTPException(status_code=403, detail="Access denied to this receipt")


def _get_linked_expense(receipt_id: str, db: Session) -> Optional[Expense]:
    return db.query(Expense).filter(Expense.receipt_id == receipt_id).first()


# ═══════════════════════════════════════════════════════════════
# GET /api/v1/organizations/{organization_id}/receipts
# ═══════════════════════════════════════════════════════════════
@router.get("/organizations/{organization_id}/receipts")
def list_receipts(
    organization_id: int,
    status: Optional[str] = None,
    date_from: Optional[datetime.date] = None,
    date_to: Optional[datetime.date] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _user: User = Depends(require_org_access(min_role="employee")),
):
    """
    List receipts for an organization. Most recent first.

    Filters:
      status     — match `Receipt.ocr_status` (parsed | review_light |
                   review_heavy | failed | dlp_quarantined | pending)
      date_from  — only receipts whose `created_at` >= this date
      date_to    — only receipts whose `created_at` <  this date
      limit      — page size (default 50, max 200)
      offset     — pagination offset
    """
    q = db.query(Receipt).filter(Receipt.organization_id == organization_id)
    if status:
        q = q.filter(Receipt.ocr_status == status)
    if date_from:
        q = q.filter(Receipt.created_at >= datetime.datetime.combine(date_from, datetime.time.min))
    if date_to:
        q = q.filter(Receipt.created_at < datetime.datetime.combine(date_to, datetime.time.min))

    total = q.count()
    limit = min(max(limit, 1), 200)
    rows = q.order_by(Receipt.created_at.desc()).offset(offset).limit(limit).all()

    # Bulk-load linked expenses so we can include expense_id in each row
    expense_map: dict[str, int] = {}
    if rows:
        expense_rows = (
            db.query(Expense.receipt_id, Expense.id)
            .filter(Expense.receipt_id.in_([r.id for r in rows]))
            .all()
        )
        expense_map = {rid: eid for rid, eid in expense_rows if rid}

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_receipt_to_dict(r, expense_id=expense_map.get(r.id)) for r in rows],
    }


# ═══════════════════════════════════════════════════════════════
# GET /api/v1/receipts/{receipt_id}
# ═══════════════════════════════════════════════════════════════
@router.get("/receipts/{receipt_id}")
def get_receipt(
    receipt_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Single receipt detail + parsed expense + 15-min signed image URL."""
    receipt = _load_receipt_or_404(receipt_id, db)
    _check_org_access(receipt, current_user, db, min_role="employee")
    expense = _get_linked_expense(receipt_id, db)

    # Signed URL — works in both stub and gcs backends
    try:
        url = signed_url(object_key=receipt.gcs_object_key, ttl_seconds=900)
    except Exception:
        url = None

    return {
        "receipt": _receipt_to_dict(receipt, expense_id=expense.id if expense else None,
                                    image_signed_url=url),
        "expense": _expense_to_dict(expense) if expense else None,
    }


# ═══════════════════════════════════════════════════════════════
# POST /api/v1/receipts/{receipt_id}/confirm
# ═══════════════════════════════════════════════════════════════
@router.post("/receipts/{receipt_id}/confirm")
def confirm_receipt(
    receipt_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Confirm the linked Expense — flips status to 'confirmed'."""
    receipt = _load_receipt_or_404(receipt_id, db)
    _check_org_access(receipt, current_user, db, min_role="employee")
    expense = _get_linked_expense(receipt_id, db)
    if not expense:
        raise HTTPException(status_code=400, detail="No expense to confirm (OCR may have failed)")

    try:
        expense = svc_confirm_expense(
            expense_id=expense.id,
            confirmed_by_user_id=current_user.id,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"ok": True, "expense": _expense_to_dict(expense)}


# ═══════════════════════════════════════════════════════════════
# POST /api/v1/receipts/{receipt_id}/reject
# ═══════════════════════════════════════════════════════════════
@router.post("/receipts/{receipt_id}/reject")
def reject_receipt(
    receipt_id: str,
    payload: RejectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reject the linked Expense — kept for audit, excluded from totals."""
    receipt = _load_receipt_or_404(receipt_id, db)
    _check_org_access(receipt, current_user, db, min_role="employee")
    expense = _get_linked_expense(receipt_id, db)
    if not expense:
        raise HTTPException(status_code=400, detail="No expense to reject")

    try:
        expense = svc_reject_expense(
            expense_id=expense.id,
            rejected_by_user_id=current_user.id,
            reason=payload.reason,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"ok": True, "expense": _expense_to_dict(expense)}


# ═══════════════════════════════════════════════════════════════
# POST /api/v1/receipts/{receipt_id}/manual-update
# ═══════════════════════════════════════════════════════════════
@router.post("/receipts/{receipt_id}/manual-update")
def manual_update_expense(
    receipt_id: str,
    payload: ManualUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Override OCR-parsed Expense fields (amount, supplier, date, category,
    notes). Used when the user clicks ✏️ Fix or an admin corrects a bad
    parse before confirmation.

    NOTE: only mutates Expense fields — the underlying Receipt row +
    raw OCR JSON stay immutable for audit. To re-run OCR, use
    POST /admin/receipts/{id}/reparse.
    """
    receipt = _load_receipt_or_404(receipt_id, db)
    _check_org_access(receipt, current_user, db, min_role="employee")
    expense = _get_linked_expense(receipt_id, db)
    if not expense:
        raise HTTPException(status_code=400, detail="No expense to update")
    if expense.status != "draft":
        raise HTTPException(status_code=400,
                            detail=f"Cannot edit a {expense.status} expense — reject and re-file instead")

    changes = payload.dict(exclude_unset=True)
    for key, value in changes.items():
        setattr(expense, key, value)

    db.commit()
    db.refresh(expense)
    return {"ok": True, "expense": _expense_to_dict(expense)}


# ═══════════════════════════════════════════════════════════════
# GET /api/v1/admin/receipts/review-queue
# ═══════════════════════════════════════════════════════════════
@router.get("/admin/receipts/review-queue")
def admin_review_queue(
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
    limit: int = 50,
    offset: int = 0,
):
    """
    All receipts currently in `review_light` or `review_heavy` status,
    across every organization. The first-50 hands-on QA pattern: the
    founder reviews these by hand to gather ground-truth for later
    threshold tuning.
    """
    q = (
        db.query(Receipt)
        .filter(Receipt.ocr_status.in_(["review_light", "review_heavy"]))
        .order_by(Receipt.created_at.asc())
    )
    total = q.count()
    rows = q.offset(offset).limit(min(max(limit, 1), 200)).all()

    expense_map: dict[str, int] = {}
    if rows:
        expense_rows = (
            db.query(Expense.receipt_id, Expense.id)
            .filter(Expense.receipt_id.in_([r.id for r in rows]))
            .all()
        )
        expense_map = {rid: eid for rid, eid in expense_rows if rid}

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_receipt_to_dict(r, expense_id=expense_map.get(r.id)) for r in rows],
    }


# ═══════════════════════════════════════════════════════════════
# POST /api/v1/admin/receipts/{receipt_id}/reparse
# ═══════════════════════════════════════════════════════════════
@router.post("/admin/receipts/{receipt_id}/reparse")
def admin_reparse(
    receipt_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """
    Force a re-OCR of an existing receipt. Useful when:
      - thresholds were tuned and we want to re-route old receipts
      - Document AI processor was upgraded
      - a manual fix to the original Receipt is needed

    Reads the bytes back from GCS (or the stub local path) and runs
    them through the pipeline again. Idempotent on dedup since we
    target the same sha256.

    This is a HEAVY operation — admin-only, not exposed to tenants.
    """
    receipt = _load_receipt_or_404(receipt_id, db)

    # Read bytes back from storage
    from app.services.gcp.storage import _stub_root, STORAGE_BACKEND
    if STORAGE_BACKEND == "stub":
        local = _stub_root() / receipt.gcs_object_key
        if not local.exists():
            raise HTTPException(status_code=410, detail=f"Bytes no longer available at {local}")
        image_bytes = local.read_bytes()
    elif STORAGE_BACKEND == "gcs":
        from google.cloud import storage  # type: ignore
        import os as _os
        client = storage.Client()
        bucket = client.bucket(_os.getenv("GCS_BUCKET_RECEIPTS", "asg-receipts-prod"))
        blob = bucket.blob(receipt.gcs_object_key)
        if not blob.exists():
            raise HTTPException(status_code=410, detail="Bytes no longer in GCS")
        image_bytes = blob.download_as_bytes()
    else:
        raise HTTPException(status_code=500, detail=f"Unknown STORAGE_BACKEND={STORAGE_BACKEND}")

    # Delete the existing Receipt row so the dedup check doesn't short-circuit;
    # the new pipeline run will recreate with fresh OCR data.
    expense = _get_linked_expense(receipt.id, db)
    if expense:
        db.delete(expense)
    db.delete(receipt)
    db.flush()

    outcome = process_receipt(
        organization_id=receipt.organization_id,
        user_id=receipt.user_id,
        mime_type=receipt.mime_type or "image/jpeg",
        image_bytes=image_bytes,
        db=db,
        source="admin_reparse",
        source_message_id=receipt_id,  # original id as correlation
    )

    return {
        "ok": outcome.status == ReceiptOutcomeStatus.OK,
        "status": outcome.status.value,
        "route": outcome.route.value,
        "receipt_id": outcome.receipt.id if outcome.receipt else None,
        "expense_id": outcome.expense.id if outcome.expense else None,
    }


# ═══════════════════════════════════════════════════════════════
# POST /api/v1/receipts/upload  (multipart, dashboard / portal)
# ═══════════════════════════════════════════════════════════════
@router.post("/receipts/upload")
async def upload_receipt(
    organization_id: int,            # ← query parameter (read by require_org_access middleware)
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_org_access(min_role="employee")),
):
    """
    Direct multipart upload — same OCR pipeline as the WhatsApp flow.
    Used by the dashboard / accountant portal once they ship UIs for
    direct-upload (Sprint 4+).

    organization_id is a QUERY PARAMETER (not form field) because the
    require_org_access middleware needs it before the multipart body
    is parsed.

    Example:
        POST /api/v1/receipts/upload?organization_id=42
        Content-Type: multipart/form-data
            file=<binary>

    Constraints:
      - Max file size: 10 MB
      - Allowed mime types: image/jpeg, image/png, image/heic,
                              image/heif, application/pdf
    """
    ALLOWED = {"image/jpeg", "image/png", "image/heic", "image/heif", "application/pdf"}
    if file.content_type not in ALLOWED:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported mime_type: {file.content_type}")

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")

    outcome = process_receipt(
        organization_id=organization_id,
        user_id=current_user.id,
        mime_type=file.content_type,
        image_bytes=image_bytes,
        db=db,
        source="dashboard",
        source_message_id=None,
    )

    expense = outcome.expense
    return {
        "status": outcome.status.value,
        "route": outcome.route.value,
        "receipt": _receipt_to_dict(outcome.receipt, expense_id=expense.id if expense else None) if outcome.receipt else None,
        "expense": _expense_to_dict(expense) if expense else None,
        "error_message": outcome.error_message,
    }
