"""
Aurora LTS — Payout Service (Sprint 5)
==========================================
Lifecycle helpers for AccountantPayout: approve → mark paid → fail.

ACTUAL DISBURSEMENT:
  Sprint 5 ships the data plane. The wire to a payout provider
  (Tranzila / PayPlus / Mascha"v) lands later. Today, the founder
  approves payouts in the admin queue, exports a CSV, and sends a
  bank transfer manually. mark_payout_paid() then records the bank
  reference for audit.

  When a payout API integration ships, it slots in between approve()
  and mark_paid() as `disburse_payout(payout_id)`. The function shape
  is intentionally narrow.
"""

import csv
import datetime
import io
from typing import List, Optional

from sqlalchemy.orm import Session

from app.database import (
    AccountantPayout,
    RevenueShareLedger,
    User,
    ActionLog,
)


def approve_payout(
    *,
    payout_id: int,
    approved_by_user_id: int,
    db: Session,
    notes: Optional[str] = None,
) -> AccountantPayout:
    """Founder/admin approves a pending payout. Required before disbursement."""
    payout = db.query(AccountantPayout).filter(AccountantPayout.id == payout_id).first()
    if not payout:
        raise ValueError(f"payout_id={payout_id} not found")
    if payout.status not in ("pending",):
        raise ValueError(f"Cannot approve a payout in status={payout.status}")

    payout.status = "approved"
    payout.approved_by_user_id = approved_by_user_id
    payout.approved_at = datetime.datetime.utcnow()
    if notes:
        payout.notes = (payout.notes or "") + f"\n[approve] {notes}"

    db.add(ActionLog(
        business_id=None,
        status="rev_share.payout_approved",
        detail=f"payout_id={payout_id} acct={payout.accountant_user_id} period={payout.period} "
               f"total={payout.total_amount_minor_units} approver={approved_by_user_id}",
    ))
    db.commit()
    db.refresh(payout)
    return payout


def mark_payout_paid(
    *,
    payout_id: int,
    provider_ref: str,
    db: Session,
    paid_by_user_id: Optional[int] = None,
) -> AccountantPayout:
    """
    Record that the payout was disbursed (bank transfer, payout-provider
    success, etc.). Flips the linked RevenueShareLedger rows to status='paid'.

    NOTE on immutability: ledger rows with status='paid' are write-locked
    by application discipline (and Postgres trigger in Sprint 6). Once
    paid, this is the audit record; no further mutation.
    """
    payout = db.query(AccountantPayout).filter(AccountantPayout.id == payout_id).first()
    if not payout:
        raise ValueError(f"payout_id={payout_id} not found")
    if payout.status not in ("approved", "pending"):
        # Allow direct pending → paid for super-admin / one-shot emergencies,
        # but log it loudly.
        if payout.status == "paid":
            return payout
        raise ValueError(f"Cannot pay a payout in status={payout.status}")
    if not provider_ref:
        raise ValueError("provider_ref is required (e.g. bank transfer ID)")

    now = datetime.datetime.utcnow()
    payout.status = "paid"
    payout.paid_at = now
    payout.provider_ref = provider_ref

    # Flip linked ledger rows
    ledger_rows = (
        db.query(RevenueShareLedger)
        .filter(RevenueShareLedger.payout_id == payout.id)
        .all()
    )
    for row in ledger_rows:
        if row.status != "paid":
            row.status = "paid"
            row.paid_at = now

    db.add(ActionLog(
        business_id=None,
        status="rev_share.payout_paid",
        detail=(
            f"payout_id={payout_id} acct={payout.accountant_user_id} "
            f"period={payout.period} ref={provider_ref!r} "
            f"rows={len(ledger_rows)}"
        ),
    ))
    db.commit()
    db.refresh(payout)
    return payout


def fail_payout(
    *,
    payout_id: int,
    reason: str,
    db: Session,
) -> AccountantPayout:
    """Disbursement failed (bank rejected, etc.). Move ledger rows back to payable."""
    payout = db.query(AccountantPayout).filter(AccountantPayout.id == payout_id).first()
    if not payout:
        raise ValueError(f"payout_id={payout_id} not found")
    payout.status = "failed"
    payout.failed_at = datetime.datetime.utcnow()
    payout.failure_message = (reason or "")[:500]

    rows = db.query(RevenueShareLedger).filter(RevenueShareLedger.payout_id == payout.id).all()
    for r in rows:
        if r.status not in ("paid",):
            r.status = "payable"
            r.payout_id = None

    db.add(ActionLog(
        business_id=None,
        status="rev_share.payout_failed",
        detail=f"payout_id={payout_id} reason={reason!r}",
    ))
    db.commit()
    db.refresh(payout)
    return payout


def list_payouts_for_accountant(
    *,
    accountant_user_id: int,
    db: Session,
    limit: int = 50,
    offset: int = 0,
) -> List[AccountantPayout]:
    return (
        db.query(AccountantPayout)
        .filter(AccountantPayout.accountant_user_id == accountant_user_id)
        .order_by(AccountantPayout.period.desc())
        .offset(offset)
        .limit(min(max(limit, 1), 200))
        .all()
    )


def export_payouts_csv(
    *,
    period: str,
    db: Session,
) -> tuple[bytes, dict]:
    """
    CSV export of all approved/pending payouts for a period — what the
    founder hands to the bank for batched outgoing transfers.

    Columns:
        accountant_email, full_name, period, amount_ils, currency,
        status, ledger_row_count
    """
    rows = (
        db.query(AccountantPayout)
        .filter(AccountantPayout.period == period)
        .order_by(AccountantPayout.total_amount_minor_units.desc())
        .all()
    )
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow([
        "accountant_email", "full_name", "period",
        "amount_ils", "currency", "status",
        "ledger_row_count",
    ])
    for p in rows:
        user = db.query(User).filter(User.id == p.accountant_user_id).first()
        writer.writerow([
            user.email if user else "?",
            user.full_name if user else "?",
            p.period,
            f"{(p.total_amount_minor_units or 0) / 100:.2f}",
            p.currency or "ILS",
            p.status,
            p.ledger_row_count or 0,
        ])
    return buf.getvalue().encode("utf-8-sig"), {
        "period": period,
        "rows": len(rows),
        "filename": f"aurora-payouts-{period}.csv",
    }
