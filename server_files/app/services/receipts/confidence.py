"""
Aurora LTS — Receipt Confidence Routing
==========================================
Single source of truth for the question:
    "Given an ExpenseParseResult, what should we do with it?"

Three outcomes:
    AUTO_APPROVE  : confidence is high enough to auto-create a draft
                    Expense and just send a brief confirmation card.
    REVIEW_LIGHT  : confidence is mid; show the parsed card and ask
                    user/accountant for a single ✓/✏️ tap.
    REVIEW_HEAVY  : confidence is low or critical fields are missing;
                    explicitly prompt for the amount.
    DLP_QUARANTINE: PII detected (handled separately, but constant lives here).
    OCR_FAILURE   : Document AI failed; fall back to manual entry.

Thresholds are tunable via env so we can re-calibrate against the
labeled receipt corpus (Track C) without redeploying:
    RECEIPT_CONFIDENCE_AUTO_THRESHOLD     default 0.85
    RECEIPT_CONFIDENCE_REVIEW_THRESHOLD   default 0.60
"""

import os
from enum import Enum
from typing import Optional

from app.services.gcp.document_ai import ExpenseParseResult


class ReceiptRoute(str, Enum):
    """The discrete outcomes the pipeline can produce."""
    AUTO_APPROVE = "auto_approve"
    REVIEW_LIGHT = "review_light"
    REVIEW_HEAVY = "review_heavy"
    OCR_FAILURE = "ocr_failure"
    DLP_QUARANTINE = "dlp_quarantine"


def auto_threshold() -> float:
    """Read-once-per-call so env changes are honoured without restart."""
    try:
        return float(os.getenv("RECEIPT_CONFIDENCE_AUTO_THRESHOLD", "0.85"))
    except ValueError:
        return 0.85


def review_threshold() -> float:
    try:
        return float(os.getenv("RECEIPT_CONFIDENCE_REVIEW_THRESHOLD", "0.60"))
    except ValueError:
        return 0.60


def route_by_confidence(parse: Optional[ExpenseParseResult]) -> ReceiptRoute:
    """
    Decide the route for a given ExpenseParseResult.

    Rules (in order):
      - parse is None or has no critical fields  → REVIEW_HEAVY
      - confidence_min is None (no fields detected at all) → OCR_FAILURE
      - confidence_min >= auto_threshold              → AUTO_APPROVE
      - confidence_min >= review_threshold            → REVIEW_LIGHT
      - else                                          → REVIEW_HEAVY

    Critical fields = supplier_name + total_amount + receipt_date.
    Missing any of them sets confidence_min to 0.0 → REVIEW_HEAVY.
    """
    if parse is None:
        return ReceiptRoute.OCR_FAILURE

    conf = parse.confidence_min
    if conf is None:
        return ReceiptRoute.OCR_FAILURE

    if conf >= auto_threshold():
        return ReceiptRoute.AUTO_APPROVE
    if conf >= review_threshold():
        return ReceiptRoute.REVIEW_LIGHT
    return ReceiptRoute.REVIEW_HEAVY


def to_ocr_status(route: ReceiptRoute) -> str:
    """
    Translate a ReceiptRoute into the canonical Receipt.ocr_status string.
    The DB column is a string for SQLite compatibility; this is the
    single conversion point so no other module hard-codes the values.
    """
    return {
        ReceiptRoute.AUTO_APPROVE:    "parsed",
        ReceiptRoute.REVIEW_LIGHT:    "review_light",
        ReceiptRoute.REVIEW_HEAVY:    "review_heavy",
        ReceiptRoute.OCR_FAILURE:     "failed",
        ReceiptRoute.DLP_QUARANTINE:  "dlp_quarantined",
    }[route]
