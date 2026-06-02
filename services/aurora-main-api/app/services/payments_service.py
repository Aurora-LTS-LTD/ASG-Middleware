"""
Aurora LTS — Invoice Payments Service (P2-07)
================================================
Records partial payments against an Invoice. Computes balance_due
and updates Invoice.status to 'paid' when fully settled.

  record_payment(...) — manual or system-driven payment.
  compute_balance(invoice_id, db) -> float
  apply_bank_match(bank_entry, invoice, db) — called by the
    reconciliation flow (P2-06) when a statement entry auto-links
    or is manually confirmed.

The sum of InvoicePayment.amount for an invoice cannot exceed the
invoice's amount_total (overpayment is rejected — operator should
issue a credit note for overage, P2-05).
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from aurora_shared.database.models import (
    Invoice, InvoicePayment, BankStatementEntry,
)

log = logging.getLogger(__name__)


class PaymentError(RuntimeError):
    """Raised when a payment request violates business rules."""


def compute_balance(invoice_id: int, db: Session) -> float:
    """Remaining amount due = amount_total - sum(payments)."""
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if inv is None:
        raise PaymentError(f"Invoice {invoice_id} not found")
    total = float(inv.amount_total or 0.0)
    paid = float(
        db.query(func.coalesce(func.sum(InvoicePayment.amount), 0.0))
        .filter(InvoicePayment.invoice_id == invoice_id)
        .scalar()
        or 0.0
    )
    return round(total - paid, 2)


def record_payment(
    db: Session,
    *,
    invoice_id: int,
    amount: float,
    paid_at: Optional[datetime.datetime] = None,
    source: str = "manual",
    note: Optional[str] = None,
    bank_entry_id: Optional[int] = None,
    created_by_user_id: Optional[int] = None,
) -> InvoicePayment:
    """
    Persist an InvoicePayment and flip the invoice to 'paid' if the
    new total fully settles the balance.
    """
    if amount <= 0:
        raise PaymentError("amount must be positive")

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if inv is None:
        raise PaymentError(f"Invoice {invoice_id} not found")

    balance = compute_balance(invoice_id, db)
    if amount > balance + 0.01:
        raise PaymentError(
            f"Payment {amount:.2f} would overpay invoice {invoice_id} "
            f"(balance_due={balance:.2f}). Issue a credit note for any overage."
        )

    payment = InvoicePayment(
        invoice_id=invoice_id,
        amount=round(amount, 2),
        currency=inv.currency or "ILS",
        paid_at=paid_at or datetime.datetime.utcnow(),
        source=source[:40],
        bank_entry_id=bank_entry_id,
        note=(note or "").strip()[:500] or None,
        created_by_user_id=created_by_user_id,
    )
    db.add(payment)
    db.flush()

    new_balance = compute_balance(invoice_id, db)
    if new_balance <= 0.005 and inv.status in ("finalized", "sent"):
        inv.status = "paid"
        log.info(
            "[payments] invoice %s fully paid (number=%s)",
            inv.id, inv.invoice_number,
        )

    db.commit()
    db.refresh(payment)
    return payment


def apply_bank_match(
    db: Session,
    *,
    bank_entry: BankStatementEntry,
    invoice: Invoice,
) -> Optional[InvoicePayment]:
    """
    Called when a BankStatementEntry links to an Invoice (auto or
    manual confirmation). Inserts an InvoicePayment IFF one doesn't
    already exist for this (invoice, bank_entry) pair.
    """
    existing = (
        db.query(InvoicePayment)
        .filter(
            InvoicePayment.invoice_id == invoice.id,
            InvoicePayment.bank_entry_id == bank_entry.id,
        )
        .first()
    )
    if existing is not None:
        return existing

    try:
        return record_payment(
            db=db,
            invoice_id=invoice.id,
            amount=float(bank_entry.amount),
            paid_at=bank_entry.posted_at,
            source="bank_statement",
            bank_entry_id=bank_entry.id,
            note=(bank_entry.reference or "")[:500],
        )
    except PaymentError as exc:
        log.warning(
            "[payments] auto-record skipped for invoice=%s bank_entry=%s: %s",
            invoice.id, bank_entry.id, exc,
        )
        return None


__all__ = ["record_payment", "compute_balance", "apply_bank_match", "PaymentError"]
