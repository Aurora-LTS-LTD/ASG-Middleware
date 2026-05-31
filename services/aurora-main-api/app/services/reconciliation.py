"""
Aurora LTS — Payment Reconciliation (P2-06)
==============================================
Matches BankStatementEntry rows against Invoices and books high-
confidence matches automatically.

INPUT TODAY: CSV upload (POST /api/v1/banking/statements/upload).
INPUT FUTURE: Open Banking AISP feed per Israeli regulator standard
              (https://www.boi.org.il/en/communication-and-publications/
               press-releases/open-banking-in-israel/) — operator work
              to provision per-bank AISP credentials.

MATCHING SIGNALS (in decreasing weight):
  1. Exact amount match → required (no money-rounding tolerance yet).
  2. Date window: posted_at within ± `_DATE_TOLERANCE_DAYS` of
     invoice.created_at OR allocation_issued_at.
  3. Counterparty fuzzy match against invoice.beneficiary_name.
  4. Bank "reference" memo containing the invoice_number string.

AUTO-LINK THRESHOLD: confidence ≥ 0.85.
SUGGESTION: 0.50 ≤ confidence < 0.85 (operator confirms in UI).
IGNORE:     confidence < 0.50.
"""
from __future__ import annotations

import datetime
import difflib
import logging
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.database.models import BankStatementEntry, Invoice

log = logging.getLogger(__name__)

_DATE_TOLERANCE_DAYS = 30
_AUTO_LINK_THRESHOLD = 0.85
_SUGGEST_THRESHOLD = 0.50


def _name_similarity(a: Optional[str], b: Optional[str]) -> float:
    """0.0 (different) – 1.0 (identical), case-insensitive ratio."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(
        a=a.strip().lower(), b=b.strip().lower(),
    ).ratio()


def score_candidate(
    entry: BankStatementEntry, invoice: Invoice,
) -> Tuple[float, str]:
    """
    Returns (confidence_0_to_1, human_readable_reason).
    Score is the product of independent signals — any zero signal
    drives the total to zero (e.g. amount mismatch = no match).
    """
    # 1. Amount must match exactly (positive entry against
    #    invoice.amount_total).
    if abs(float(entry.amount) - float(invoice.amount_total or 0.0)) > 0.005:
        return 0.0, "amount_mismatch"

    # 2. Date window
    invoice_anchor = invoice.allocation_issued_at or getattr(invoice, "created_at", None)
    if invoice_anchor is None:
        date_score = 0.5  # mild credit; no reliable anchor
    else:
        delta_days = abs((entry.posted_at - invoice_anchor).days)
        if delta_days > _DATE_TOLERANCE_DAYS:
            return 0.0, f"date_out_of_window_{delta_days}d"
        date_score = max(0.0, 1.0 - (delta_days / _DATE_TOLERANCE_DAYS))

    # 3. Counterparty fuzzy match
    name_score = _name_similarity(entry.counterparty_name, invoice.beneficiary_name)

    # 4. Reference memo carries the invoice number
    ref = (entry.reference or "").lower()
    inv_num = (invoice.invoice_number or "").lower()
    ref_score = 1.0 if inv_num and inv_num in ref else 0.0

    # Weighted combine: amount is already required (>0 guaranteed here).
    # Date 0.40, name 0.40, ref 0.20. amount itself acts as the gate.
    confidence = (0.40 * date_score) + (0.40 * name_score) + (0.20 * ref_score)
    reason = f"amount=OK date={date_score:.2f} name={name_score:.2f} ref={ref_score:.0f}"
    return confidence, reason


def reconcile_entry(
    db: Session, entry: BankStatementEntry,
) -> BankStatementEntry:
    """
    Find the best matching Invoice for `entry`, set match_status +
    matched_invoice_id + match_confidence + match_reason accordingly.
    Returns the (possibly mutated) entry. Caller commits.

    Only considers invoices that are:
      - same business_id
      - status in ('finalized', 'sent') — not drafts
      - not already linked to another bank entry
    """
    # candidate set
    candidates = (
        db.query(Invoice)
        .filter(
            Invoice.business_id == entry.business_id,
            Invoice.status.in_(("finalized", "sent")),
            # exclude invoices already linked to another statement entry
            ~Invoice.id.in_(
                db.query(BankStatementEntry.matched_invoice_id)
                .filter(BankStatementEntry.matched_invoice_id.isnot(None))
            ),
        )
        .all()
    )

    best_invoice: Optional[Invoice] = None
    best_conf = 0.0
    best_reason = ""
    for inv in candidates:
        conf, reason = score_candidate(entry, inv)
        if conf > best_conf:
            best_conf = conf
            best_invoice = inv
            best_reason = reason

    if best_invoice is None or best_conf < _SUGGEST_THRESHOLD:
        entry.match_status = "unmatched"
        entry.matched_invoice_id = None
        entry.match_confidence = best_conf if best_invoice else None
        entry.match_reason = best_reason or "no_candidate_above_threshold"
        return entry

    entry.matched_invoice_id = best_invoice.id
    entry.match_confidence = round(best_conf, 4)
    entry.match_reason = best_reason
    entry.matched_at = datetime.datetime.utcnow()
    entry.match_status = (
        "linked" if best_conf >= _AUTO_LINK_THRESHOLD else "suggested"
    )

    # P2-07: auto-create an InvoicePayment when we auto-link.
    # Suggested-tier (operator confirmation pending) intentionally does
    # NOT create a payment yet — the operator's manual confirm flow
    # will call apply_bank_match() explicitly.
    if entry.match_status == "linked":
        from app.services.payments_service import apply_bank_match
        apply_bank_match(db, bank_entry=entry, invoice=best_invoice)

    return entry


def reconcile_pending(db: Session, business_id: int) -> dict:
    """
    Run reconcile_entry over all unmatched rows for `business_id`.
    Used by the upload endpoint after CSV ingest + by a periodic
    re-reconciliation cron.
    """
    pending = (
        db.query(BankStatementEntry)
        .filter(
            BankStatementEntry.business_id == business_id,
            BankStatementEntry.match_status.in_(("unmatched", "suggested")),
        )
        .all()
    )
    linked = suggested = unmatched = 0
    for entry in pending:
        reconcile_entry(db, entry)
        if entry.match_status == "linked":
            linked += 1
        elif entry.match_status == "suggested":
            suggested += 1
        else:
            unmatched += 1
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("[reconcile] commit failed: %s", exc)
        raise
    return {"linked": linked, "suggested": suggested, "unmatched": unmatched}


__all__ = [
    "reconcile_entry", "reconcile_pending", "score_candidate",
]
