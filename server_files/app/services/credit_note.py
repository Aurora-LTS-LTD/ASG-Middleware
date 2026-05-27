"""
Aurora LTS — Credit Notes (חשבונית זיכוי) — P2-05
====================================================
Israeli tax law (חוק מע"מ) requires a formal credit note to cancel
or partially reverse a previously-finalized invoice. The credit note
itself is an invoice — it has its own invoice_number, allocation
number, PDF — but with a negative amount that references the
original.

The pre-2026 reform path:
  Original  finalized  → ₪10,000 + ₪1,800 VAT = ₪11,800
  Mistake found, partial reversal needed.
  Credit note → kind='credit_note', amount_net=-3000, vat=-540,
                total=-3540, original_invoice_id=ORIGINAL.id
  ITA sees both rows in the next VAT report: net effect = ₪7,260.

ISSUE RULES (enforced here):
  1. Original must be in status='finalized' or 'sent' — drafts can be
     edited or cancelled directly without a credit note.
  2. Credit note must not exceed the original (sum of credit notes
     against an original cannot push amount_net below 0).
  3. Original cannot itself be a credit note (no chained reversals).
  4. Credit notes ≥ ₪25k still need an ITA allocation number — handled
     by the existing pipeline because the credit note IS an invoice.
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.database.models import Invoice
from app.services.tax_compliance import calculate_vat
from app.services.invoice_service import generate_invoice_number

log = logging.getLogger(__name__)


class CreditNoteError(RuntimeError):
    """Raised when the requested credit note violates business rules."""


def issue_credit_note(
    db: Session,
    *,
    original_invoice_id: int,
    amount_net_to_credit: float,
    reason: Optional[str] = None,
) -> dict:
    """
    Create a credit-note invoice that nets out the supplied amount
    against `original_invoice_id`. Returns the new invoice dict.

    `amount_net_to_credit` must be POSITIVE — this function negates
    it internally for storage.
    """
    if amount_net_to_credit <= 0:
        raise CreditNoteError(
            "amount_net_to_credit must be positive — pass the magnitude "
            "of the credit, not a negative value."
        )

    original: Optional[Invoice] = (
        db.query(Invoice).filter(Invoice.id == original_invoice_id).first()
    )
    if original is None:
        raise CreditNoteError(f"Invoice {original_invoice_id} not found")

    if getattr(original, "kind", "standard") != "standard":
        raise CreditNoteError(
            "Cannot credit-note a credit note — chained reversals are not "
            "supported. Issue an UN-credit against the original instead."
        )

    if original.status not in ("finalized", "sent"):
        raise CreditNoteError(
            f"Original invoice {original.id} is in status={original.status!r}. "
            "Only finalized or sent invoices can be credit-noted (drafts "
            "should be edited or cancelled directly)."
        )

    # ── Aggregate prior credits against this original ──
    prior_credits_sum = (
        db.query(Invoice)
        .filter(
            Invoice.original_invoice_id == original.id,
            Invoice.kind == "credit_note",
        )
        .with_entities(Invoice.amount_net)
        .all()
    )
    already_credited = -sum((row[0] or 0.0) for row in prior_credits_sum)
    # `row[0]` is negative for credit notes, so negating their sum
    # gives the total *magnitude* already credited.

    remaining = float(original.amount_net or 0.0) - already_credited
    if amount_net_to_credit > remaining + 1e-9:
        raise CreditNoteError(
            f"Credit amount {amount_net_to_credit:.2f} exceeds remaining "
            f"creditable {remaining:.2f} (original net "
            f"{original.amount_net:.2f}, already credited {already_credited:.2f})."
        )

    # ── Compute negative amounts using the SAME VAT helper ──
    vat_info = calculate_vat(amount_net_to_credit)
    neg_net = -vat_info["amount_net"]
    neg_vat = -vat_info["vat_amount"]
    neg_total = -vat_info["amount_total"]

    # ── Number the credit note: a normal invoice_number from the
    #    same business pool. The kind discriminator + reference is
    #    what marks it as a credit note, not the number scheme.
    existing_count = (
        db.query(Invoice)
        .filter(Invoice.business_id == original.business_id)
        .count()
    )
    credit_number = generate_invoice_number(original.business_id, existing_count)

    description = (
        (reason.strip()[:500] if reason else "")
        or f"חשבונית זיכוי על {original.invoice_number}"
    )

    cn = Invoice(
        business_id=original.business_id,
        invoice_number=credit_number,
        beneficiary_name=original.beneficiary_name,
        beneficiary_tax_id=original.beneficiary_tax_id,
        beneficiary_contact=original.beneficiary_contact,
        amount_net=neg_net,
        vat_rate=vat_info["vat_rate"],
        vat_amount=neg_vat,
        amount_total=neg_total,
        currency=original.currency,
        status="draft",                     # follows the standard finalize flow
        description=description,
        kind="credit_note",
        original_invoice_id=original.id,
        # allocation requirement re-evaluated against the magnitude
        requires_allocation=1 if amount_net_to_credit >= 25_000 else 0,
        allocation_status="pending" if amount_net_to_credit >= 25_000 else "not_required",
    )
    db.add(cn)
    try:
        db.commit()
        db.refresh(cn)
    except Exception as exc:
        db.rollback()
        log.error("[credit_note] DB commit failed: %s", exc)
        raise CreditNoteError(f"credit_note_persist_failed: {exc}")

    log.info(
        "[credit_note] issued id=%s number=%s magnitude=%.2f "
        "original_id=%s original_number=%s",
        cn.id, cn.invoice_number, amount_net_to_credit,
        original.id, original.invoice_number,
    )

    from app.services.invoice_service import invoice_to_dict
    return invoice_to_dict(cn)


__all__ = ["issue_credit_note", "CreditNoteError"]
