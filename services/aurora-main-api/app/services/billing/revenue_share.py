"""
Aurora LTS — Revenue Share Engine (Sprint 5)
=================================================
Two phases:

  1. ACCRUAL — accrue_on_charge_success(payment_id):
     Called when a SubscriptionPayment flips to 'succeeded'. Looks up
     the engaged accountant (via AccountantEngagement.status='active'),
     creates ONE RevenueShareLedger row in status='accrued'.

     Run synchronously from the charge handler. Idempotent: if a ledger
     row already exists for the (accountant, payment) pair, returns it.

  2. CLOSURE — close_month(period):
     Called once a month by Cloud Scheduler (or manually by the founder).
     For each 'accrued' row in the period:
       - Run fraud rules → 'payable' or 'held_for_review'
     Then for each accountant with payable rows:
       - Sum into a single AccountantPayout(status='pending')
       - Flip ledger rows to status='paid' once the payout is committed
         (NOTE: actual payment provider call happens later in
         payout_service.mark_payout_paid)

FRAUD RULES (Part I §3.3):
  A row is held_for_review when ANY of:
    - The tenant's organization is < MIN_TENANT_AGE_DAYS old (default 90)
    - The tenant has finalized < MIN_TENANT_INVOICE_COUNT invoices (default 5)
    - The accountant onboarded themselves as their own tenant
      (accountant_user_id == any owner of the org)

  Rules are intentionally conservative — false positives are easy to
  approve manually; false negatives leak money.

CONFIGURATION (env-tunable):
    REVENUE_SHARE_DEFAULT_PCT       default 20.0
    REVENUE_SHARE_MIN_TENANT_DAYS   default 90
    REVENUE_SHARE_MIN_INVOICES      default 5

REAL-WORLD ANALOGY:
  At month-end the bookkeeper walks through every commission line:
  "did this client stick around long enough?" "did they actually
  use the service?" "did the agent set up a fake client?". Those
  checks become payable lines or held lines, then the agent gets
  ONE check covering all approved commissions.
"""

import datetime
import os
from typing import List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from aurora_shared.database import (
    SubscriptionPayment,
    AccountantEngagement,
    RevenueShareLedger,
    AccountantPayout,
    Organization,
    Membership,
    Invoice,
    ActionLog,
)


# ─────────────────────────────────────────────────────────────
# Tunables (env-driven so we can adjust without redeploy)
# ─────────────────────────────────────────────────────────────
def DEFAULT_SHARE_PCT() -> float:
    try:
        return float(os.getenv("REVENUE_SHARE_DEFAULT_PCT", "20.0"))
    except ValueError:
        return 20.0


def MIN_TENANT_AGE_DAYS() -> int:
    try:
        return int(os.getenv("REVENUE_SHARE_MIN_TENANT_DAYS", "90"))
    except ValueError:
        return 90


def MIN_TENANT_INVOICE_COUNT() -> int:
    try:
        return int(os.getenv("REVENUE_SHARE_MIN_INVOICES", "5"))
    except ValueError:
        return 5


# ─────────────────────────────────────────────────────────────
# Public — accrue_on_charge_success
# ─────────────────────────────────────────────────────────────
def accrue_on_charge_success(
    *,
    subscription_payment_id: int,
    db: Session,
) -> Optional[RevenueShareLedger]:
    """
    Idempotently create a RevenueShareLedger row for the engaged
    accountant of this payment's organization.

    Returns the ledger row if an active engagement exists; None
    otherwise (organizations without an accountant generate no rev-share).

    Raises ValueError on missing payment or non-succeeded status.
    """
    payment = (
        db.query(SubscriptionPayment)
        .filter(SubscriptionPayment.id == subscription_payment_id)
        .first()
    )
    if not payment:
        raise ValueError(f"subscription_payment_id={subscription_payment_id} not found")
    if payment.status != "succeeded":
        # Defensive — accrual only fires on success
        raise ValueError(f"payment status must be 'succeeded', got {payment.status!r}")

    # Look for the active engagement on this org (only ONE active per org)
    engagement = (
        db.query(AccountantEngagement)
        .filter(
            AccountantEngagement.organization_id == payment.organization_id,
            AccountantEngagement.status == "active",
        )
        .order_by(AccountantEngagement.activated_at.desc())
        .first()
    )
    if not engagement:
        # No engaged accountant → no rev-share. Not an error.
        return None

    # Idempotency: never create two ledger rows for the same (acct, payment)
    existing = (
        db.query(RevenueShareLedger)
        .filter(
            RevenueShareLedger.accountant_user_id == engagement.accountant_user_id,
            RevenueShareLedger.subscription_payment_id == payment.id,
        )
        .first()
    )
    if existing:
        return existing

    share_pct = engagement.revenue_share_pct or DEFAULT_SHARE_PCT()
    gross = int(payment.amount_minor_units or 0)
    share = int(round(gross * share_pct / 100.0))

    row = RevenueShareLedger(
        accountant_user_id=engagement.accountant_user_id,
        organization_id=payment.organization_id,
        subscription_payment_id=payment.id,
        engagement_id=engagement.id,
        gross_amount_minor_units=gross,
        share_pct=share_pct,
        share_amount_minor_units=share,
        currency=payment.currency or "ILS",
        status="accrued",
    )
    db.add(row)

    db.add(ActionLog(
        business_id=None,
        status="rev_share.accrued",
        detail=(
            f"acct={engagement.accountant_user_id} org={payment.organization_id} "
            f"payment={payment.id} gross={gross} share={share} ({share_pct}%)"
        ),
    ))
    db.commit()
    db.refresh(row)
    return row


# ─────────────────────────────────────────────────────────────
# Public — passes_fraud_rules
# ─────────────────────────────────────────────────────────────
def passes_fraud_rules(
    *,
    ledger_row: RevenueShareLedger,
    db: Session,
) -> Tuple[bool, Optional[str]]:
    """
    Apply Part I §3.3 fraud rules to a ledger row.

    Returns (passes, reason). When passes=False, `reason` is a short
    string suitable for `held_reason` and the founder's review queue.
    """
    org = (
        db.query(Organization)
        .filter(Organization.id == ledger_row.organization_id)
        .first()
    )
    if not org:
        return False, f"organization_id={ledger_row.organization_id} not found"

    # Rule 1 — Tenant must be ≥ MIN_TENANT_AGE_DAYS days old
    age = datetime.datetime.utcnow() - (org.created_at or datetime.datetime.utcnow())
    min_days = MIN_TENANT_AGE_DAYS()
    if age.days < min_days:
        return False, f"tenant younger than {min_days} days (age={age.days}d)"

    # Rule 2 — Tenant has finalized ≥ MIN_TENANT_INVOICE_COUNT invoices
    biz_id = org.legacy_business_id
    invoice_count = 0
    if biz_id:
        invoice_count = (
            db.query(func.count(Invoice.id))
            .filter(
                Invoice.business_id == biz_id,
                Invoice.status.in_(("finalized", "sent")),
            )
            .scalar()
            or 0
        )
    min_invoices = MIN_TENANT_INVOICE_COUNT()
    if invoice_count < min_invoices:
        return False, f"tenant has only {invoice_count} finalized invoice(s); need ≥{min_invoices}"

    # Rule 3 — Accountant cannot be a member (owner/employee) of the org
    self_owns = (
        db.query(Membership)
        .filter(
            Membership.user_id == ledger_row.accountant_user_id,
            Membership.organization_id == ledger_row.organization_id,
        )
        .first()
    )
    if self_owns:
        return False, "accountant is also a member of the organization (self-onboarding)"

    return True, None


# ─────────────────────────────────────────────────────────────
# Public — close_month
# ─────────────────────────────────────────────────────────────
def close_month(
    *,
    period: str,
    db: Session,
    dry_run: bool = False,
) -> dict:
    """
    Apply fraud rules + create AccountantPayouts for every accountant
    with payable rows in the given period (format "YYYY-MM").

    Idempotent: re-running for the same period skips already-closed
    rows. Safe to call from Cloud Scheduler on the 1st of every month
    AND manually for ad-hoc reconciliation.

    Returns a summary dict:
      {
        "period":        "YYYY-MM",
        "rows_examined": int,
        "rows_payable":  int,
        "rows_held":     int,
        "payouts_created":[ {accountant_user_id, total, ledger_count}, ... ],
        "dry_run":       bool,
      }
    """
    if not period or len(period) != 7 or period[4] != "-":
        raise ValueError("period must be 'YYYY-MM'")

    year = int(period[:4])
    month = int(period[5:])
    start = datetime.datetime(year, month, 1)
    end = (
        datetime.datetime(year + 1, 1, 1)
        if month == 12
        else datetime.datetime(year, month + 1, 1)
    )

    rows = (
        db.query(RevenueShareLedger)
        .filter(
            RevenueShareLedger.status == "accrued",
            RevenueShareLedger.created_at >= start,
            RevenueShareLedger.created_at < end,
        )
        .all()
    )
    summary = {
        "period": period,
        "rows_examined": len(rows),
        "rows_payable": 0,
        "rows_held": 0,
        "payouts_created": [],
        "dry_run": dry_run,
    }

    # ── Step 1: apply fraud rules row-by-row ──
    payable_by_acct: dict[int, list[RevenueShareLedger]] = {}
    for row in rows:
        passes, reason = passes_fraud_rules(ledger_row=row, db=db)
        if passes:
            row.status = "payable"
            payable_by_acct.setdefault(row.accountant_user_id, []).append(row)
            summary["rows_payable"] += 1
        else:
            row.status = "held_for_review"
            row.held_reason = reason
            summary["rows_held"] += 1

    if dry_run:
        # Don't persist row state changes either
        for r in rows:
            db.expunge(r) if r in db else None
        # We DID mutate fields above on attached objects; rollback to undo
        db.rollback()
        return summary

    db.commit()

    # ── Step 2: per-accountant rollup → AccountantPayout ──
    for acct_id, payable_rows in payable_by_acct.items():
        total = sum(r.share_amount_minor_units for r in payable_rows)

        # Idempotency: if a payout already exists for (acct, period), update it
        existing = (
            db.query(AccountantPayout)
            .filter(
                AccountantPayout.accountant_user_id == acct_id,
                AccountantPayout.period == period,
            )
            .first()
        )
        if existing:
            existing.total_amount_minor_units = (existing.total_amount_minor_units or 0) + total
            existing.ledger_row_count = (existing.ledger_row_count or 0) + len(payable_rows)
            payout = existing
        else:
            payout = AccountantPayout(
                accountant_user_id=acct_id,
                period=period,
                total_amount_minor_units=total,
                ledger_row_count=len(payable_rows),
                status="pending",
                currency="ILS",
            )
            db.add(payout)
            db.flush()

        # Link the rows to this payout
        for r in payable_rows:
            r.payout_id = payout.id

        db.add(ActionLog(
            business_id=None,
            status="rev_share.payout_created",
            detail=(
                f"acct={acct_id} period={period} total={payout.total_amount_minor_units} "
                f"rows={payout.ledger_row_count}"
            ),
        ))
        summary["payouts_created"].append({
            "accountant_user_id": acct_id,
            "total_amount_minor_units": payout.total_amount_minor_units,
            "ledger_row_count": payout.ledger_row_count,
            "payout_id": payout.id,
        })

    db.commit()
    return summary
