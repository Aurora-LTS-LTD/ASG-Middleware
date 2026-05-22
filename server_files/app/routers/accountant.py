"""
Aurora LTS — Accountant Portal Router (Sprint 4)
====================================================
JSON API for the Accountant Portal SPA.

ENDPOINTS (all JWT-protected, role='accountant' required):

  GET  /api/v1/accountant/book
       The accountant's "book" — every Org they have an active
       AccountantEngagement for, with a one-line health summary
       (invoice count, outstanding, overdue, recent expenses,
       last activity).

  GET  /api/v1/accountant/orgs/{organization_id}/summary
       Drill-in: P&L for the period, expenses by category, recent
       receipts, pending review-queue items.

  POST /api/v1/accountant/orgs/{organization_id}/exports
       Kick off an export (uniform_file | hashavshevet) for a period.
       Returns the Export row + a signed URL the accountant clicks
       to download.

  GET  /api/v1/accountant/orgs/{organization_id}/exports
       List past exports for an org.

  GET  /api/v1/accountant/exports/{export_id}
       Single export detail + a fresh 15-min signed URL.

  GET  /api/v1/accountant/coa-mappings
       List the accountant's COA mappings (their Aurora-category →
       account-code dictionary).

  PUT  /api/v1/accountant/coa-mappings
       Upsert a COA mapping. Body: {category, account_code, account_name?}.

ACCESS CONTROL:
  All org-scoped endpoints verify the calling user has an active
  AccountantEngagement on that org. Admins (role='admin') bypass
  the check. Owners / employees on the org see the same data via
  the existing /api/v1/organizations/* endpoints (they can also
  hit /accountant/* if they happen to have an engagement, which
  is unusual but not forbidden).

REAL-WORLD ANALOGY:
  This router is the accountant's filing cabinet. They walk in with
  their badge (JWT), open the drawer for client X (org_id), and
  pull out the file (summary), stamp it (export), or update their
  index card (COA mapping).
"""

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import (
    get_db,
    User,
    Organization,
    Membership,
    AccountantEngagement,
    Invoice,
    Payment,
    Expense,
    Receipt,
    Export,
    AccountantCoaMapping,
    RevenueShareLedger,
    AccountantPayout,
    AccountantReferral,
    ActionLog,
)
from app.middleware.auth_middleware import get_current_user, require_admin
from app.services.exports import (
    create_export,
    get_export as svc_get_export,
    list_exports as svc_list_exports,
    ExportFormatError,
)
from app.services.exports.service import export_signed_url


router = APIRouter(prefix="/api/v1/accountant", tags=["accountant"])


# ─────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────
class CreateExportRequest(BaseModel):
    format: str = Field(..., description="'uniform_file' | 'hashavshevet'")
    period_start: datetime.date
    period_end: datetime.date


class CoaMappingRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=40)
    account_code: str = Field(..., min_length=1, max_length=20)
    account_name: Optional[str] = Field(default=None, max_length=120)


# ─────────────────────────────────────────────────────────────
# Authorization helper
# ─────────────────────────────────────────────────────────────
def _require_accountant_or_admin(current_user: User) -> None:
    if current_user.role not in ("accountant", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Accountant role required",
        )


def _verify_engagement(
    organization_id: int,
    current_user: User,
    db: Session,
) -> None:
    """Confirm the calling user has access to this org as an accountant."""
    if current_user.role == "admin":
        return

    engagement = (
        db.query(AccountantEngagement)
        .filter(
            AccountantEngagement.accountant_user_id == current_user.id,
            AccountantEngagement.organization_id == organization_id,
            AccountantEngagement.status == "active",
        )
        .first()
    )
    if not engagement:
        raise HTTPException(
            status_code=403,
            detail=f"No active engagement on organization_id={organization_id}",
        )


# ═══════════════════════════════════════════════════════════════
# GET /api/v1/accountant/book
# ═══════════════════════════════════════════════════════════════
@router.get("/book")
def get_book(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    The accountant's book — every Org they have an active engagement on.
    Returns a one-line summary per Org so the portal can render a grid.
    """
    _require_accountant_or_admin(current_user)

    if current_user.role == "admin":
        # Admins see ALL orgs
        org_ids_q = db.query(Organization.id)
    else:
        org_ids_q = (
            db.query(AccountantEngagement.organization_id)
            .filter(
                AccountantEngagement.accountant_user_id == current_user.id,
                AccountantEngagement.status == "active",
            )
        )
    org_ids = [row[0] for row in org_ids_q.all()]
    if not org_ids:
        return {"count": 0, "items": []}

    items = []
    for oid in org_ids:
        org = db.query(Organization).filter(Organization.id == oid).first()
        if not org:
            continue

        # Per-org one-line health summary
        invoice_count = db.query(func.count(Invoice.id)).filter(
            Invoice.business_id == org.legacy_business_id
        ).scalar() or 0
        outstanding = db.query(func.coalesce(func.sum(Invoice.amount_total - Invoice.amount_paid), 0)).filter(
            Invoice.business_id == org.legacy_business_id,
            Invoice.payment_status.in_(("unpaid", "partial")),
        ).scalar() or 0
        last_activity = db.query(func.max(Invoice.created_at)).filter(
            Invoice.business_id == org.legacy_business_id
        ).scalar()
        review_queue_count = db.query(func.count(Receipt.id)).filter(
            Receipt.organization_id == oid,
            Receipt.ocr_status.in_(("review_light", "review_heavy")),
        ).scalar() or 0

        items.append({
            "id": org.id,
            "display_name": org.display_name,
            "legal_structure": org.legal_structure,
            "tax_id": org.tax_id,
            "kyc_status": org.kyc_status,
            "status": org.status,
            "invoice_count": int(invoice_count),
            "outstanding_amount": float(outstanding),
            "review_queue_count": int(review_queue_count),
            "last_activity_at": last_activity.isoformat() if last_activity else None,
        })

    return {"count": len(items), "items": items}


# ═══════════════════════════════════════════════════════════════
# GET /api/v1/accountant/orgs/{organization_id}/summary
# ═══════════════════════════════════════════════════════════════
@router.get("/orgs/{organization_id}/summary")
def get_org_summary(
    organization_id: int,
    period_start: Optional[datetime.date] = None,
    period_end: Optional[datetime.date] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Single-org drill-in. P&L for the period, plus pointers into
    receipts / invoices / exports.
    """
    _require_accountant_or_admin(current_user)
    _verify_engagement(organization_id, current_user, db)

    if not period_start:
        # Default: this calendar month
        today = datetime.date.today()
        period_start = today.replace(day=1)
    if not period_end:
        period_end = datetime.date.today()

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    period_start_dt = datetime.datetime.combine(period_start, datetime.time.min)
    period_end_dt = (
        datetime.datetime.combine(period_end, datetime.time.min)
        + datetime.timedelta(days=1)
    )

    # ── Income (invoices) ──
    invoice_total = db.query(func.coalesce(func.sum(Invoice.amount_total), 0)).filter(
        Invoice.business_id == org.legacy_business_id,
        Invoice.created_at >= period_start_dt,
        Invoice.created_at < period_end_dt,
        Invoice.status.in_(("finalized", "sent")),
    ).scalar() or 0
    vat_collected = db.query(func.coalesce(func.sum(Invoice.vat_amount), 0)).filter(
        Invoice.business_id == org.legacy_business_id,
        Invoice.created_at >= period_start_dt,
        Invoice.created_at < period_end_dt,
        Invoice.status.in_(("finalized", "sent")),
    ).scalar() or 0
    invoice_count = db.query(func.count(Invoice.id)).filter(
        Invoice.business_id == org.legacy_business_id,
        Invoice.created_at >= period_start_dt,
        Invoice.created_at < period_end_dt,
    ).scalar() or 0

    # ── Expenses ──
    expense_rows = (
        db.query(
            Expense.category,
            func.count(Expense.id),
            func.coalesce(func.sum(Expense.total_amount_minor_units), 0),
            func.coalesce(func.sum(Expense.vat_amount_minor_units), 0),
        )
        .filter(
            Expense.organization_id == organization_id,
            Expense.status == "confirmed",
            Expense.expense_date >= period_start,
            Expense.expense_date <= period_end,
        )
        .group_by(Expense.category)
        .all()
    )
    expenses_by_category = [
        {
            "category": cat or "uncategorised",
            "count": int(count),
            "total_amount_minor_units": int(total or 0),
            "vat_amount_minor_units": int(vat or 0),
        }
        for cat, count, total, vat in expense_rows
    ]
    expense_total_minor = sum(r["total_amount_minor_units"] for r in expenses_by_category)
    vat_paid_minor = sum(r["vat_amount_minor_units"] for r in expenses_by_category)

    # ── Receipts review queue ──
    review_queue_count = db.query(func.count(Receipt.id)).filter(
        Receipt.organization_id == organization_id,
        Receipt.ocr_status.in_(("review_light", "review_heavy")),
    ).scalar() or 0

    # ── VAT due (output VAT - input VAT) ──
    vat_due_minor = int(round(float(vat_collected) * 100)) - int(vat_paid_minor)

    return {
        "organization": {
            "id": org.id,
            "display_name": org.display_name,
            "legal_structure": org.legal_structure,
            "tax_id": org.tax_id,
            "kyc_status": org.kyc_status,
        },
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
        "income": {
            "invoice_count": int(invoice_count),
            "total_amount": float(invoice_total),
            "vat_collected": float(vat_collected),
        },
        "expenses": {
            "by_category": expenses_by_category,
            "total_amount_minor_units": expense_total_minor,
            "vat_paid_minor_units": vat_paid_minor,
        },
        "vat": {
            "collected_minor_units": int(round(float(vat_collected) * 100)),
            "paid_minor_units": int(vat_paid_minor),
            "due_minor_units": vat_due_minor,
            "rate_pct": 18.0,  # 2026 Israel VAT rate
        },
        "review_queue_count": int(review_queue_count),
    }


# ═══════════════════════════════════════════════════════════════
# POST /api/v1/accountant/orgs/{organization_id}/exports
# ═══════════════════════════════════════════════════════════════
@router.post("/orgs/{organization_id}/exports")
def create_export_endpoint(
    organization_id: int,
    payload: CreateExportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Build + upload an export for a period. Always returns the Export
    row (with `signed_url` populated when status='completed').
    """
    _require_accountant_or_admin(current_user)
    _verify_engagement(organization_id, current_user, db)

    if payload.period_start > payload.period_end:
        raise HTTPException(status_code=400, detail="period_start must be <= period_end")

    try:
        export = create_export(
            organization_id=organization_id,
            requested_by_user_id=current_user.id,
            format=payload.format,
            period_start=payload.period_start,
            period_end=payload.period_end,
            db=db,
            accountant_user_id=current_user.id,
        )
    except ExportFormatError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _export_to_dict(export)


# ═══════════════════════════════════════════════════════════════
# GET /api/v1/accountant/orgs/{organization_id}/exports
# ═══════════════════════════════════════════════════════════════
@router.get("/orgs/{organization_id}/exports")
def list_exports_endpoint(
    organization_id: int,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_accountant_or_admin(current_user)
    _verify_engagement(organization_id, current_user, db)

    rows = svc_list_exports(
        organization_id=organization_id, db=db, limit=limit, offset=offset,
    )
    return {
        "count": len(rows),
        "items": [_export_to_dict(r, include_signed_url=False) for r in rows],
    }


# ═══════════════════════════════════════════════════════════════
# GET /api/v1/accountant/exports/{export_id}
# ═══════════════════════════════════════════════════════════════
@router.get("/exports/{export_id}")
def get_export_endpoint(
    export_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_accountant_or_admin(current_user)
    export = svc_get_export(export_id=export_id, db=db)
    if not export:
        raise HTTPException(status_code=404, detail="Export not found")
    _verify_engagement(export.organization_id, current_user, db)

    return _export_to_dict(export)


# ═══════════════════════════════════════════════════════════════
# GET /api/v1/accountant/coa-mappings
# ═══════════════════════════════════════════════════════════════
@router.get("/coa-mappings")
def list_coa_mappings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_accountant_or_admin(current_user)
    rows = (
        db.query(AccountantCoaMapping)
        .filter(AccountantCoaMapping.accountant_user_id == current_user.id)
        .order_by(AccountantCoaMapping.category.asc())
        .all()
    )
    return {
        "count": len(rows),
        "items": [
            {
                "id": r.id,
                "category": r.category,
                "account_code": r.account_code,
                "account_name": r.account_name,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


# ═══════════════════════════════════════════════════════════════
# PUT /api/v1/accountant/coa-mappings  (upsert)
# ═══════════════════════════════════════════════════════════════
@router.put("/coa-mappings")
def upsert_coa_mapping(
    payload: CoaMappingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_accountant_or_admin(current_user)

    row = (
        db.query(AccountantCoaMapping)
        .filter(
            AccountantCoaMapping.accountant_user_id == current_user.id,
            AccountantCoaMapping.category == payload.category,
        )
        .first()
    )
    if row:
        row.account_code = payload.account_code
        row.account_name = payload.account_name
    else:
        row = AccountantCoaMapping(
            accountant_user_id=current_user.id,
            category=payload.category,
            account_code=payload.account_code,
            account_name=payload.account_name,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "category": row.category,
        "account_code": row.account_code,
        "account_name": row.account_name,
    }


# ═══════════════════════════════════════════════════════════════
# GET /api/v1/accountant/earnings
# ═══════════════════════════════════════════════════════════════
@router.get("/earnings")
def get_earnings(
    period: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Accountant's own earnings summary: lifetime totals + per-period
    breakdown + the live "X accrued this month" number that drives
    the dashboard hero metric.

    Query parameter:
      period  optional "YYYY-MM" — restrict the breakdown to this month.
              Default: last 12 months grouped by month.
    """
    _require_accountant_or_admin(current_user)

    # Lifetime totals across all statuses
    rows = (
        db.query(
            RevenueShareLedger.status,
            func.count(RevenueShareLedger.id),
            func.coalesce(func.sum(RevenueShareLedger.share_amount_minor_units), 0),
        )
        .filter(RevenueShareLedger.accountant_user_id == current_user.id)
        .group_by(RevenueShareLedger.status)
        .all()
    )
    by_status = {
        s: {"count": int(c), "amount_minor_units": int(amt or 0)}
        for s, c, amt in rows
    }

    # Per-period roll-up via AccountantPayout (last 12 periods)
    periods = (
        db.query(AccountantPayout)
        .filter(AccountantPayout.accountant_user_id == current_user.id)
        .order_by(AccountantPayout.period.desc())
        .limit(12)
        .all()
    )
    period_summary = [
        {
            "period": p.period,
            "total_amount_minor_units": int(p.total_amount_minor_units or 0),
            "ledger_row_count": int(p.ledger_row_count or 0),
            "status": p.status,
            "approved_at": p.approved_at.isoformat() if p.approved_at else None,
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
            "provider_ref": p.provider_ref,
        }
        for p in periods
    ]

    # Current-month live accrual (rows in 'accrued' or 'payable' status this month)
    today = datetime.date.today()
    current_period = today.strftime("%Y-%m")
    period_start_dt = datetime.datetime(today.year, today.month, 1)
    current_month_total = db.query(
        func.coalesce(func.sum(RevenueShareLedger.share_amount_minor_units), 0)
    ).filter(
        RevenueShareLedger.accountant_user_id == current_user.id,
        RevenueShareLedger.created_at >= period_start_dt,
        RevenueShareLedger.status.in_(("accrued", "payable")),
    ).scalar() or 0

    referral_count = (
        db.query(func.count(AccountantReferral.id))
        .filter(AccountantReferral.accountant_user_id == current_user.id)
        .scalar()
        or 0
    )

    return {
        "current_period": current_period,
        "current_month_accrued_minor_units": int(current_month_total),
        "lifetime_by_status": by_status,
        "lifetime_total_paid_minor_units": int(by_status.get("paid", {}).get("amount_minor_units", 0)),
        "periods_last_12": period_summary,
        "referral_count": int(referral_count),
        "currency": "ILS",
    }


# ═══════════════════════════════════════════════════════════════
# GET /api/v1/accountant/payouts
# ═══════════════════════════════════════════════════════════════
@router.get("/payouts")
def list_my_payouts(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the calling accountant's payouts."""
    _require_accountant_or_admin(current_user)
    rows = (
        db.query(AccountantPayout)
        .filter(AccountantPayout.accountant_user_id == current_user.id)
        .order_by(AccountantPayout.period.desc())
        .offset(offset)
        .limit(min(max(limit, 1), 200))
        .all()
    )
    return {
        "count": len(rows),
        "items": [
            {
                "id": p.id, "period": p.period,
                "total_amount_minor_units": int(p.total_amount_minor_units or 0),
                "ledger_row_count": int(p.ledger_row_count or 0),
                "status": p.status,
                "approved_at": p.approved_at.isoformat() if p.approved_at else None,
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
                "provider_ref": p.provider_ref,
                "currency": p.currency,
            }
            for p in rows
        ],
    }


# ═══════════════════════════════════════════════════════════════
# GET /api/v1/accountant/referrals
# ═══════════════════════════════════════════════════════════════
@router.get("/referrals")
def list_my_referrals(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the orgs this accountant referred onto Aurora."""
    _require_accountant_or_admin(current_user)
    rows = (
        db.query(AccountantReferral)
        .filter(AccountantReferral.accountant_user_id == current_user.id)
        .order_by(AccountantReferral.created_at.desc())
        .all()
    )
    out = []
    for r in rows:
        org = db.query(Organization).filter(Organization.id == r.organization_id).first()
        out.append({
            "id": r.id,
            "organization_id": r.organization_id,
            "organization_display_name": org.display_name if org else None,
            "source": r.source,
            "activated_at": r.activated_at.isoformat() if r.activated_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {"count": len(out), "items": out}


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _export_to_dict(export: Export, *, include_signed_url: bool = True) -> dict:
    body = {
        "id": export.id,
        "organization_id": export.organization_id,
        "format": export.format,
        "period_start": export.period_start.isoformat() if export.period_start else None,
        "period_end": export.period_end.isoformat() if export.period_end else None,
        "status": export.status,
        "file_size_bytes": export.file_size_bytes,
        "record_count": export.record_count,
        "sha256": export.sha256,
        "error_message": export.error_message,
        "created_at": export.created_at.isoformat() if export.created_at else None,
        "completed_at": export.completed_at.isoformat() if export.completed_at else None,
    }
    if include_signed_url and export.status == "completed":
        body["signed_url"] = export_signed_url(export, ttl_seconds=900)
    else:
        body["signed_url"] = None
    return body
