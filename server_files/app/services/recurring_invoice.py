"""
Aurora LTS — Recurring Invoice Engine (P2-01)
===============================================
A small service that mints draft invoices on a schedule.

DATA MODEL:
  RecurringInvoiceSchedule (P2-01 migration):
    id / business_id / beneficiary_* / amount_net / description /
    cadence (weekly|monthly|quarterly|yearly) / next_due_at /
    last_run_at / active / created_at / created_by_user_id

EXECUTION MODEL:
  A Cloud Scheduler cron POSTs /api/v1/recurring-invoices/tick every
  hour. The endpoint is service-to-service (API-key auth, P1-22).
  tick_due_schedules(db, now) is the worker:

    1. Query schedules WHERE active AND next_due_at <= now.
    2. For each: create a draft Invoice via the existing
       create_draft_invoice() — full VAT + tax-compliance pipeline
       runs.
    3. Advance next_due_at by the cadence (using
       dateutil.relativedelta for month-aware steps).
    4. Set last_run_at = now.
    5. Commit once per schedule so a single bad row can't poison
       the batch.

IDEMPOTENCY:
  next_due_at is advanced AFTER the invoice is committed. A crash
  between commit and advance would mint a duplicate on the next tick,
  but the existing invoice_number generator + business-scoped count
  guarantee unique numbers (no DB constraint violation, just one
  extra invoice). Operators can void duplicates via the existing
  void flow.

CADENCE STEPS (relativedelta):
  weekly     → weeks=1
  monthly    → months=1     (Jan 31 + 1 month = Feb 28/29, correct)
  quarterly  → months=3
  yearly     → years=1
"""
from __future__ import annotations

import datetime
import logging
from typing import List, Tuple

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from app.database.models import RecurringInvoiceSchedule
from app.services.invoice_service import create_draft_invoice

log = logging.getLogger(__name__)


# Cadence → relativedelta step
_CADENCE_STEPS = {
    "weekly":    relativedelta(weeks=1),
    "monthly":   relativedelta(months=1),
    "quarterly": relativedelta(months=3),
    "yearly":    relativedelta(years=1),
}

VALID_CADENCES = frozenset(_CADENCE_STEPS.keys())


def advance_next_due(current: datetime.datetime, cadence: str) -> datetime.datetime:
    """Compute the next due date given the current one + cadence."""
    step = _CADENCE_STEPS.get(cadence)
    if step is None:
        raise ValueError(f"Unknown cadence: {cadence!r} (allowed: {sorted(VALID_CADENCES)})")
    return current + step


def tick_due_schedules(
    db: Session,
    now: datetime.datetime | None = None,
) -> Tuple[int, int, List[dict]]:
    """
    Run one tick of the recurring-invoice engine.

    Returns (created_count, error_count, created_invoices) where each
    item in created_invoices is the result of invoice_to_dict for the
    newly-minted invoice (small payload, includes invoice_number + id).
    """
    if now is None:
        now = datetime.datetime.utcnow()

    due = (
        db.query(RecurringInvoiceSchedule)
        .filter(
            RecurringInvoiceSchedule.active.is_(True),
            RecurringInvoiceSchedule.next_due_at <= now,
        )
        .order_by(RecurringInvoiceSchedule.next_due_at.asc())
        .all()
    )

    created: List[dict] = []
    errors = 0

    for schedule in due:
        try:
            invoice = create_draft_invoice(
                db=db,
                business_id=schedule.business_id,
                beneficiary_name=schedule.beneficiary_name,
                beneficiary_tax_id=schedule.beneficiary_tax_id,
                beneficiary_contact=schedule.beneficiary_contact,
                amount_net=schedule.amount_net,
                description=schedule.description,
            )
            # advance schedule state
            schedule.last_run_at = now
            schedule.next_due_at = advance_next_due(
                schedule.next_due_at, schedule.cadence
            )
            db.commit()
            created.append(invoice)
            log.info(
                "[recurring] schedule_id=%s minted invoice=%s next_due=%s",
                schedule.id, invoice.get("invoice_number"),
                schedule.next_due_at.isoformat(),
            )
        except Exception as exc:
            errors += 1
            db.rollback()
            log.error(
                "[recurring] schedule_id=%s mint failed: %s — leaving next_due_at "
                "unchanged so we retry on the next tick",
                schedule.id, exc,
            )

    return len(created), errors, created


__all__ = ["tick_due_schedules", "advance_next_due", "VALID_CADENCES"]
