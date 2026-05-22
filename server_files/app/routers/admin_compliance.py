"""
Aurora LTS — Admin Compliance Router (Sprint 6)
=====================================================
Admin-only endpoints for ITA Software-House binder evidence + GDPR/PPL
compliance work.

ENDPOINTS (require_admin):

  GET  /api/v1/admin/compliance/health
       Visibility: which compliance backends are wired (BigQuery,
       immutability, redactor) + last audit-export cursor.

  GET  /api/v1/admin/compliance/dsar/{user_id}
       Build a DSAR zip for the named user. Returns binary zip.

  POST /api/v1/admin/compliance/dsar-erase/{user_id}
       Soft-erase a user (right-to-erasure with tax retention carve-out).
       Anonymises PII, keeps tax-document rows.

  POST /api/v1/admin/compliance/audit-export
       Run the BigQuery export NOW (idempotent — picks up where the
       cursor left off). Cloud Scheduler also calls
       /api/v1/internal/audit-export which proxies here.

  GET  /api/v1/admin/compliance/audit-cursor
       Read the audit-export cursor table (per-source last_id + hash).
       Auditor evidence: shows the hash chain for tamper-detection.

  POST /api/v1/admin/compliance/payouts/{payout_id}/approve
       Admin approves an AccountantPayout (Sprint 5 follow-on).

  POST /api/v1/admin/compliance/payouts/{payout_id}/mark-paid
       Admin records the bank-transfer reference and flips ledger
       rows to status='paid'.
"""

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import (
    get_db,
    User,
    AuditExportCursor,
    AccountantPayout,
    Invoice,
    Receipt,
)
from app.middleware.auth_middleware import require_admin
from app.services.compliance import (
    build_dsar_bundle,
    export_audit_to_bigquery,
    AUDIT_BIGQUERY_BACKEND,
)
from app.services.billing import approve_payout, mark_payout_paid


router = APIRouter(prefix="/api/v1/admin/compliance", tags=["admin-compliance"])


# ─────────────────────────────────────────────────────────────
# GET /health
# ─────────────────────────────────────────────────────────────
@router.get("/health")
def compliance_health(
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
):
    cursors = (
        db.query(AuditExportCursor)
        .order_by(AuditExportCursor.source_table.asc())
        .all()
    )
    return {
        "audit_bigquery_backend": AUDIT_BIGQUERY_BACKEND,
        "immutability_guards_installed": _check_guards_installed(),
        "audit_cursors": [
            {
                "source_table": c.source_table,
                "last_exported_id": c.last_exported_id,
                "last_exported_at": c.last_exported_at.isoformat() if c.last_exported_at else None,
                "rows_in_last_batch": c.rows_in_last_batch,
                "last_batch_hash": (c.last_batch_hash or "")[:32] + "...",
            }
            for c in cursors
        ],
    }


def _check_guards_installed() -> bool:
    try:
        from app.services.compliance.immutability import _INSTALLED  # type: ignore
        return bool(_INSTALLED)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────
# GET /dsar/{user_id}
# ─────────────────────────────────────────────────────────────
@router.get("/dsar/{user_id}")
def dsar_bundle(
    user_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """Stream a zipped DSAR bundle for the named user."""
    try:
        zip_bytes, summary = build_dsar_bundle(user_id=user_id, db=db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    headers = {
        "Content-Disposition": f'attachment; filename="{summary["filename"]}"',
    }
    return Response(content=zip_bytes, media_type="application/zip", headers=headers)


# ─────────────────────────────────────────────────────────────
# POST /dsar-erase/{user_id}
# ─────────────────────────────────────────────────────────────
class DsarEraseRequest(BaseModel):
    confirm: bool = Field(..., description="Must be true to actually run")
    keep_tax_documents: bool = True


@router.post("/dsar-erase/{user_id}")
def dsar_erase(
    user_id: int,
    payload: DsarEraseRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Right-to-erasure (Israeli PPL §17 / GDPR Art. 17). Soft-deletes
    a User: PII fields nulled, is_active=False. Tax-document-bearing
    rows (Invoice, Receipt) preserved per the 7-year retention
    carve-out unless `keep_tax_documents=false`.
    """
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="confirm=true required for irreversible action")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "admin":
        raise HTTPException(status_code=400, detail="Cannot DSAR-erase an admin")

    erased_email = user.email
    user.is_active = False
    user.email = f"erased+{user.id}@aurora-ltd.co.il"
    user.password_hash = "$dsar-erased$"
    user.full_name = f"[erased user #{user.id}]"
    user.first_name = None
    user.last_name = None
    user.fax = None
    user.whatsapp_phone_e164 = None
    user.telegram_user_id = None
    user.onboarding_status = "erased"

    # NOTE: Invoice / Receipt / Expense / KycDocument rows are NOT touched
    # — Israeli tax law requires 7-year retention. The User row pointer
    # stays so audit logs can resolve "user 42 did X" but no PII surfaces.
    db.commit()

    return {
        "ok": True,
        "user_id": user_id,
        "erased_email_was": erased_email,
        "tax_documents_preserved": payload.keep_tax_documents,
        "performed_by_admin_id": admin.id,
        "performed_at": datetime.datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────────────────────────
# POST /audit-export
# ─────────────────────────────────────────────────────────────
@router.post("/audit-export")
def audit_export_endpoint(
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
    batch_size: int = 1000,
):
    """Force-run the BigQuery audit export. Idempotent."""
    summary = export_audit_to_bigquery(db=db, batch_size=batch_size)
    return {"ok": True, **summary}


# ─────────────────────────────────────────────────────────────
# GET /audit-cursor
# ─────────────────────────────────────────────────────────────
@router.get("/audit-cursor")
def audit_cursor(
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
):
    cursors = db.query(AuditExportCursor).all()
    return {
        "cursors": [
            {
                "source_table": c.source_table,
                "last_exported_id": c.last_exported_id,
                "last_exported_at": c.last_exported_at.isoformat() if c.last_exported_at else None,
                "rows_in_last_batch": c.rows_in_last_batch,
                "last_batch_hash": c.last_batch_hash,
            }
            for c in cursors
        ]
    }


# ─────────────────────────────────────────────────────────────
# POST /payouts/{id}/approve  +  /mark-paid
# ─────────────────────────────────────────────────────────────
class ApproveRequest(BaseModel):
    notes: Optional[str] = None


class MarkPaidRequest(BaseModel):
    provider_ref: str = Field(..., min_length=1, max_length=120)


@router.post("/payouts/{payout_id}/approve")
def approve_payout_endpoint(
    payout_id: int,
    payload: ApproveRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        payout = approve_payout(
            payout_id=payout_id, approved_by_user_id=admin.id,
            db=db, notes=payload.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _payout_dict(payout)


@router.post("/payouts/{payout_id}/mark-paid")
def mark_paid_endpoint(
    payout_id: int,
    payload: MarkPaidRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        payout = mark_payout_paid(
            payout_id=payout_id, provider_ref=payload.provider_ref,
            db=db, paid_by_user_id=admin.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _payout_dict(payout)


def _payout_dict(p: AccountantPayout) -> dict:
    return {
        "id": p.id,
        "accountant_user_id": p.accountant_user_id,
        "period": p.period,
        "total_amount_minor_units": p.total_amount_minor_units,
        "ledger_row_count": p.ledger_row_count,
        "status": p.status,
        "provider_ref": p.provider_ref,
        "approved_at": p.approved_at.isoformat() if p.approved_at else None,
        "paid_at": p.paid_at.isoformat() if p.paid_at else None,
    }
