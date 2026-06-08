"""
Aurora LTS — Invoice lifecycle state machine (single source of truth).

Centralizes every invoice STATUS change behind transition(), replacing the
ad-hoc `invoice.status = "..."` writes that were scattered across the REST
router, the allocation queue, and the Telegram/WhatsApp engines. Each move is
validated against ALLOWED_TRANSITIONS, stamps the matching `*_at` column, and
writes an append-only ActionLog audit row.

    draft ──┬──► pending_allocation ──┬──► finalized ──► sent
            │                          │
            └─────────► cancelled ◄────┘     finalized / sent are tax-locked:
                                             reverse via a credit note, never cancel.

`allocation_status` (pending / retry_pending / approved / failed / rejected) is
orthogonal and owned by the allocation flow; `rejected` is its terminal state.
"""
from __future__ import annotations

import datetime
import logging

from aurora_shared.database import Invoice, ActionLog  # noqa: F401 (Invoice for type clarity)

log = logging.getLogger(__name__)

# ── Canonical statuses ──
DRAFT = "draft"
PENDING_ALLOCATION = "pending_allocation"
FINALIZED = "finalized"
SENT = "sent"
CANCELLED = "cancelled"

INVOICE_STATUSES = frozenset({DRAFT, PENDING_ALLOCATION, FINALIZED, SENT, CANCELLED})

# from → set of allowed next statuses
ALLOWED_TRANSITIONS: dict[str, frozenset] = {
    DRAFT: frozenset({PENDING_ALLOCATION, FINALIZED, CANCELLED}),
    PENDING_ALLOCATION: frozenset({FINALIZED, CANCELLED}),
    FINALIZED: frozenset({SENT}),
    SENT: frozenset(),
    CANCELLED: frozenset(),
}

# Tax-locked: cannot be cancelled (immutable; reverse with a credit note).
TAX_LOCKED = frozenset({FINALIZED, SENT})

# status entered → timestamp column stamped (idempotent: only when currently NULL)
_TIMESTAMP_FIELD = {
    PENDING_ALLOCATION: "submitted_at",
    FINALIZED: "finalized_at",
    SENT: "sent_at",
    CANCELLED: "cancelled_at",
}


class InvoiceTransitionError(Exception):
    """Raised when a requested status change is not a legal transition."""

    def __init__(self, frm: str, to: str, hint: str | None = None):
        self.frm = frm
        self.to = to
        self.hint = hint
        msg = f"Illegal invoice transition {frm!r} → {to!r}"
        if hint:
            msg += f" — {hint}"
        super().__init__(msg)


def transition(
    db,
    invoice,
    to_status: str,
    *,
    actor: str = "system",
    reason: str | None = None,
    commit: bool = True,
):
    """Move an invoice to `to_status`, validating + stamping + auditing.

    Raises InvoiceTransitionError on an illegal move. A no-op (same status)
    returns silently so callers can be idempotent.
    """
    frm = invoice.status or DRAFT
    if to_status not in INVOICE_STATUSES:
        raise ValueError(f"Unknown invoice status {to_status!r}")
    if to_status == frm:
        return invoice  # idempotent no-op

    if to_status not in ALLOWED_TRANSITIONS.get(frm, frozenset()):
        hint = None
        if to_status == CANCELLED and frm in TAX_LOCKED:
            hint = "finalized invoices are tax-locked; reverse with a credit note"
        raise InvoiceTransitionError(frm, to_status, hint)

    now = datetime.datetime.utcnow()
    invoice.status = to_status
    ts_field = _TIMESTAMP_FIELD.get(to_status)
    if ts_field is not None and getattr(invoice, ts_field, None) is None:
        setattr(invoice, ts_field, now)

    # Append-only audit — must never break the transition itself.
    try:
        db.add(ActionLog(
            business_id=getattr(invoice, "business_id", None),
            status=f"invoice_{to_status}",
            detail=(
                f"Invoice {invoice.invoice_number} {frm}→{to_status} actor={actor}"
                + (f" reason={reason}" if reason else "")
            ),
        ))
    except Exception:
        log.exception("[lifecycle] audit log failed for invoice %s", getattr(invoice, "id", "?"))

    if commit:
        db.commit()
        db.refresh(invoice)

    log.info("[lifecycle] invoice %s %s→%s (actor=%s)", getattr(invoice, "id", "?"), frm, to_status, actor)
    return invoice


def cancel_invoice(db, invoice, *, reason: str, actor: str = "dashboard"):
    """Void a draft / pending_allocation invoice.

    Finalized/sent invoices are tax-locked → transition() raises
    InvoiceTransitionError with the credit-note hint.
    """
    if invoice.status == CANCELLED:
        return invoice  # idempotent
    return transition(db, invoice, CANCELLED, actor=actor, reason=reason)
