"""
Aurora LTS — Receipts Package
================================
Sprint 2 — owns the OCR pipeline orchestrator + confidence routing.

Public re-exports for ergonomic imports elsewhere:
    from app.services.receipts import (
        process_receipt, ReceiptParseOutcome, ReceiptOutcomeStatus,
        route_by_confidence, ReceiptRoute,
        confirm_expense, reject_expense,
    )
"""

from app.services.receipts.confidence import (
    ReceiptRoute,
    route_by_confidence,
    auto_threshold,
    review_threshold,
)
from app.services.receipts.pipeline import (
    process_receipt,
    ReceiptParseOutcome,
    ReceiptOutcomeStatus,
    confirm_expense,
    reject_expense,
)

__all__ = [
    "process_receipt",
    "ReceiptParseOutcome",
    "ReceiptOutcomeStatus",
    "ReceiptRoute",
    "route_by_confidence",
    "auto_threshold",
    "review_threshold",
    "confirm_expense",
    "reject_expense",
]
