"""
Aurora LTS — Billing / Revenue Engine Package (Sprint 5)
============================================================
Owns the 20% lifetime rev-share that accrues to engaging accountants
on every successful Aurora subscription payment.

Public re-exports:
    from app.services.billing import (
        accrue_on_charge_success,
        close_month,
        approve_payout, mark_payout_paid,
        passes_fraud_rules,
        record_referral,
        DEFAULT_SHARE_PCT,
    )
"""

from app.services.billing.revenue_share import (
    accrue_on_charge_success,
    close_month,
    passes_fraud_rules,
    DEFAULT_SHARE_PCT,
    MIN_TENANT_INVOICE_COUNT,
    MIN_TENANT_AGE_DAYS,
)
from app.services.billing.payout_service import (
    approve_payout,
    mark_payout_paid,
    fail_payout,
    list_payouts_for_accountant,
)
from app.services.billing.referrals import record_referral

__all__ = [
    "accrue_on_charge_success",
    "close_month",
    "approve_payout", "mark_payout_paid", "fail_payout",
    "list_payouts_for_accountant",
    "passes_fraud_rules",
    "record_referral",
    "DEFAULT_SHARE_PCT", "MIN_TENANT_INVOICE_COUNT", "MIN_TENANT_AGE_DAYS",
]
