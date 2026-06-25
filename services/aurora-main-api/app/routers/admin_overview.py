"""
Aurora LTS — Admin Overview + Finance router (CEO Dashboard v3.0)
=================================================================
  GET /api/v1/admin/overview        — Executive Overview KPIs + alerts.
  GET /api/v1/admin/finance/summary — revenue / expenses / profit / MRR / ARR.

Computed live from existing models (Organization, Subscription,
SubscriptionPayment, Expense). Every aggregate is wrapped so sparse pilot-stage
data (mostly zeros) never 500s — it just reports 0. Read-only, IAP-gated.
"""
from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from aurora_shared.database import (
    get_db, User, Organization, Subscription, SubscriptionPayment,
)
from aurora_shared.database.models import Expense, ExecEvent
from aurora_shared.services.permissions import require_permission

router = APIRouter(prefix="/api/v1/admin", tags=["admin-overview"])


def _month_start() -> datetime.datetime:
    now = datetime.datetime.utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _safe(fn, default=0):
    try:
        v = fn()
        return v if v is not None else default
    except Exception:
        return default


def _mrr_minor_units(db: Session) -> int:
    """Monthly-normalized recurring revenue across active subscriptions (minor units)."""
    rows = _safe(lambda: db.query(
        Subscription.billing_cycle, Subscription.cycle_amount_minor_units
    ).filter(Subscription.status == "active").all(), default=[])
    total = 0
    divisor = {"monthly": 1, "quarterly": 3, "annual": 12}
    for cycle, amount in rows or []:
        total += int((amount or 0) / divisor.get((cycle or "monthly"), 1))
    return total


def _finance(db: Session) -> dict:
    ms = _month_start()
    revenue_minor = _safe(lambda: db.query(func.coalesce(func.sum(SubscriptionPayment.amount_minor_units), 0))
                          .filter(SubscriptionPayment.status == "succeeded",
                                  SubscriptionPayment.succeeded_at >= ms).scalar())
    expenses_minor = _safe(lambda: db.query(func.coalesce(func.sum(Expense.total_amount_minor_units), 0))
                           .filter(Expense.status == "confirmed",
                                   Expense.expense_date >= ms.date()).scalar())
    mrr_minor = _mrr_minor_units(db)
    rev = (revenue_minor or 0) / 100.0
    exp = (expenses_minor or 0) / 100.0
    mrr = (mrr_minor or 0) / 100.0
    return {
        "currency": "ILS",
        "revenue_this_month": round(rev, 2),
        "expenses_this_month": round(exp, 2),
        "profit_this_month": round(rev - exp, 2),
        "mrr": round(mrr, 2),
        "arr": round(mrr * 12, 2),
        "data_thin": (revenue_minor or 0) == 0 and mrr_minor == 0,  # UI shows placeholders
    }


@router.get("/overview")
def overview(
    current_user: User = Depends(require_permission("overview", "read")),
    db: Session = Depends(get_db),
) -> dict:
    ms = _month_start()
    not_archived = Organization.archived_at.is_(None)

    total = _safe(lambda: db.query(func.count(Organization.id)).filter(not_archived).scalar())
    active = _safe(lambda: db.query(func.count(Organization.id))
                   .filter(not_archived, Organization.status == "active").scalar())
    suspended = _safe(lambda: db.query(func.count(Organization.id))
                      .filter(not_archived, Organization.status == "suspended").scalar())
    pilot = _safe(lambda: db.query(func.count(Organization.id))
                  .filter(not_archived, Organization.is_pilot.is_(True)).scalar())
    paying = _safe(lambda: db.query(func.count(func.distinct(Subscription.organization_id)))
                   .filter(Subscription.status == "active").scalar())
    new_this_month = _safe(lambda: db.query(func.count(Organization.id))
                           .filter(Organization.created_at >= ms).scalar())

    alerts = []
    try:
        rows = (db.query(ExecEvent)
                .filter(ExecEvent.severity.in_(["warning", "critical"]))
                .order_by(ExecEvent.created_at.desc()).limit(5).all())
        alerts = [{"id": r.id, "severity": r.severity, "title": r.title,
                   "detail": r.detail,
                   "created_at": r.created_at.isoformat() if r.created_at else None}
                  for r in rows]
    except Exception:
        alerts = []

    return {
        "customers": {
            "total": int(total), "active": int(active), "pilot": int(pilot),
            "paying": int(paying), "suspended": int(suspended),
            "new_this_month": int(new_this_month),
        },
        "finance": _finance(db),
        "alerts": alerts,
    }


@router.get("/finance/summary")
def finance_summary(
    current_user: User = Depends(require_permission("finance", "read")),
    db: Session = Depends(get_db),
) -> dict:
    return _finance(db)
