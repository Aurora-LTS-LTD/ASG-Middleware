"""
ASG / Aurora Solutions — Subscription Service
================================================
Aurora Onboarding Module / Phase 6b.

Owns plan pricing, trial setup, and the scheduling of the first charge.
Money is always handled in MINOR UNITS (agorot for ILS) to avoid float
drift. Convert to display only at the edges.

PRICING (initial; tunable via env without code changes later):
  starter monthly    : ₪99   = 9_900 agorot
  pro     monthly    : ₪199  = 19_900 agorot
  enterprise monthly : ₪399  = 39_900 agorot

  Cycle multipliers (with discount):
    monthly   : ×1   (no discount)
    quarterly : ×3  with 5% discount  →  amount * 3 * 0.95
    annual    : ×12 with 15% discount →  amount * 12 * 0.85

TRIAL:
  14 days. Subscription created with status='trialing', trial_ends_at
  populated. A SubscriptionPayment row is created in 'scheduled' status
  with attempted_at = trial_ends_at. The scheduled-charge worker
  (Sprint 5) sweeps and charges via PayPlus on/after that timestamp.

AUTO-INVOICING:
  Per Aurora spec, the existing invoice_service is triggered on
  successful charge — NOT on activation, because trial periods don't
  produce a tax invoice (₪0 invoices are not required by ITA and would
  pollute the Software-House binder evidence).
"""

import datetime
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from aurora_shared.database import (
    Subscription,
    SubscriptionPayment,
    PaymentMethod,
    Organization,
    ActionLog,
)


# ─────────────────────────────────────────────────────────────
# Pricing tables
# ─────────────────────────────────────────────────────────────
PLAN_AMOUNTS_MINOR_UNITS = {
    "starter":    9_900,    # ₪99
    "pro":       19_900,    # ₪199
    "enterprise":39_900,    # ₪399
}

CYCLE_DISCOUNT_PCT = {
    "monthly":   0.0,
    "quarterly": 5.0,
    "annual":   15.0,
}

CYCLE_MONTHS = {
    "monthly":   1,
    "quarterly": 3,
    "annual":    12,
}

TRIAL_DAYS = 14


# ─────────────────────────────────────────────────────────────
# compute_plan_amount
# ─────────────────────────────────────────────────────────────
def compute_plan_amount(plan: str, billing_cycle: str) -> dict:
    """
    Compute the cycle amount in minor units, given a plan + cycle.

    Returns:
        {
          "monthly_base": int,         # base monthly price (agorot)
          "cycle_multiplier": int,     # 1 / 3 / 12
          "gross_amount": int,         # base * cycle_multiplier
          "discount_pct": float,       # 0 / 5 / 15
          "discount_amount": int,      # rounded
          "cycle_amount": int,         # gross - discount
          "currency": "ILS",
        }

    Raises ValueError on unknown plan or cycle.
    """
    if plan not in PLAN_AMOUNTS_MINOR_UNITS:
        raise ValueError(f"Unknown plan '{plan}'. Choose: {list(PLAN_AMOUNTS_MINOR_UNITS.keys())}")
    if billing_cycle not in CYCLE_DISCOUNT_PCT:
        raise ValueError(f"Unknown billing_cycle '{billing_cycle}'. Choose: {list(CYCLE_DISCOUNT_PCT.keys())}")

    monthly_base = PLAN_AMOUNTS_MINOR_UNITS[plan]
    multiplier = CYCLE_MONTHS[billing_cycle]
    gross = monthly_base * multiplier
    discount_pct = CYCLE_DISCOUNT_PCT[billing_cycle]
    discount_amount = int(round(gross * discount_pct / 100))
    cycle_amount = gross - discount_amount

    return {
        "monthly_base":     monthly_base,
        "cycle_multiplier": multiplier,
        "gross_amount":     gross,
        "discount_pct":     discount_pct,
        "discount_amount":  discount_amount,
        "cycle_amount":     cycle_amount,
        "currency":         "ILS",
    }


# ─────────────────────────────────────────────────────────────
# create_subscription
# ─────────────────────────────────────────────────────────────
def create_subscription(
    *,
    organization_id: int,
    plan: str,
    billing_cycle: str,
    payment_method_id: int,
    db: Session,
    with_trial: bool = True,
) -> Subscription:
    """
    Create a Subscription for the given Organization. Defaults to
    trialing-mode with TRIAL_DAYS days of free trial.

    Idempotent: if the org already has a Subscription, returns it.

    Raises ValueError on:
      - unknown plan / cycle
      - missing org / payment method
      - payment method belongs to a different org
    """
    pricing = compute_plan_amount(plan, billing_cycle)

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if not org:
        raise ValueError(f"organization_id={organization_id} not found")

    pm = db.query(PaymentMethod).filter(PaymentMethod.id == payment_method_id).first()
    if not pm:
        raise ValueError(f"payment_method_id={payment_method_id} not found")
    if pm.organization_id != organization_id:
        raise ValueError("payment method belongs to a different organization")

    existing = db.query(Subscription).filter(Subscription.organization_id == organization_id).first()
    if existing:
        return existing

    now = datetime.datetime.utcnow()
    trial_ends_at = now + datetime.timedelta(days=TRIAL_DAYS) if with_trial else None
    period_start = now
    period_end = trial_ends_at if with_trial else (
        now + datetime.timedelta(days=30 * pricing["cycle_multiplier"])
    )

    sub = Subscription(
        organization_id=organization_id,
        plan=plan,
        billing_cycle=billing_cycle,
        cycle_amount_minor_units=pricing["cycle_amount"],
        currency="ILS",
        discount_pct=pricing["discount_pct"],
        status="trialing" if with_trial else "active",
        payment_method_id=payment_method_id,
        trial_ends_at=trial_ends_at,
        started_at=now,
        current_period_start=period_start,
        current_period_end=period_end,
    )
    db.add(sub)

    db.add(ActionLog(
        business_id=org.legacy_business_id,
        status="subscription.created",
        detail=(
            f"sub org_id={organization_id} plan={plan} cycle={billing_cycle} "
            f"amount={pricing['cycle_amount']} trial_ends={trial_ends_at}"
        ),
    ))
    db.commit()
    db.refresh(sub)
    return sub


# ─────────────────────────────────────────────────────────────
# schedule_first_charge
# ─────────────────────────────────────────────────────────────
def schedule_first_charge(
    *,
    subscription_id: int,
    db: Session,
) -> SubscriptionPayment:
    """
    Create the first SubscriptionPayment row for a trialing subscription.
    Status='scheduled', attempted_at=trial_ends_at.

    The scheduled-charge worker (Sprint 5) sweeps these. On success,
    the success handler calls invoice_service to mint the tax invoice.

    Idempotent: returns the existing scheduled payment if one already
    exists for this subscription.
    """
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise ValueError(f"subscription_id={subscription_id} not found")

    existing = (
        db.query(SubscriptionPayment)
        .filter(
            SubscriptionPayment.subscription_id == subscription_id,
            SubscriptionPayment.status == "scheduled",
        )
        .first()
    )
    if existing:
        return existing

    # If trialing, schedule for trial_ends_at; else schedule for now
    # (an immediate charge for a no-trial activation flow).
    attempted_at = sub.trial_ends_at if sub.status == "trialing" else datetime.datetime.utcnow()
    period_start = sub.current_period_start or datetime.datetime.utcnow()
    period_end = sub.current_period_end or (period_start + datetime.timedelta(days=30))

    payment = SubscriptionPayment(
        subscription_id=sub.id,
        organization_id=sub.organization_id,
        amount_minor_units=sub.cycle_amount_minor_units,
        currency=sub.currency,
        status="scheduled",
        idempotency_key=f"sub-{sub.id}-firstcharge-{uuid.uuid4().hex[:12]}",
        period_start=period_start,
        period_end=period_end,
        attempted_at=attempted_at,
    )
    db.add(payment)

    db.add(ActionLog(
        business_id=None,
        status="subscription_payment.scheduled",
        detail=(
            f"sub_id={sub.id} amount={payment.amount_minor_units} "
            f"attempted_at={payment.attempted_at}"
        ),
    ))
    db.commit()
    db.refresh(payment)
    return payment
