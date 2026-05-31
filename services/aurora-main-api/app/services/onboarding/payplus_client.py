"""
ASG / Aurora Solutions — PayPlus Payment Gateway Client
==========================================================
Israeli payment processor with strong direct-debit (הוראת קבע) support.
Selected for Aurora because direct-debit is a critical payment rail
for the target SMB / accountant-channel demographic.

PCI-DSS POSTURE:
  - Card data NEVER traverses Aurora's servers.
  - The browser submits PAN/CVV directly to PayPlus's iframe.
  - PayPlus returns an opaque token.
  - We persist only: token + last4 + brand + exp month/year.
  - Aurora stays at SAQ-A or SAQ-A-EP scope (out of full Level 1).

DIRECT DEBIT (הוראת קבע):
  PayPlus exposes a mandate-management API. We capture bank/branch/
  account_last4 metadata, send the user through PayPlus's mandate
  acceptance flow, and store the resulting mandate token. Periodic
  charges then "pull" against the token without further user action.

THIS MODULE:
  - PAYPLUS_BACKEND='stub' (default): synthetic tokens, no network calls.
    Returns deterministic-shape responses that the rest of the system
    can integrate against. Lets the founder demo the full flow end-to-end
    before PayPlus credentials arrive.
  - PAYPLUS_BACKEND='production': real REST calls to
    https://restapi.payplus.co.il/api/v1.0/* — implementation lands when
    PAYPLUS_API_KEY + PAYPLUS_TERMINAL_NUMBER are populated.

REAL-WORLD ANALOGY:
  PayPlus is the cash-register company. They handle the secure card
  reader (the iframe) and the bank-debit forms. Aurora just gets back
  a receipt-key (token) and a thumbs-up (success) or thumbs-down (failure).
"""

import datetime
import os
import uuid


PAYPLUS_BACKEND = (os.getenv("PAYPLUS_BACKEND") or "stub").strip().lower()
PAYPLUS_API_BASE = os.getenv("PAYPLUS_API_BASE") or "https://restapi.payplus.co.il/api/v1.0"


# ─────────────────────────────────────────────────────────────
# payplus_tokenize
# ─────────────────────────────────────────────────────────────
def payplus_tokenize(
    *,
    kind: str,                         # 'credit_card' | 'direct_debit'
    raw_payload: dict,                 # whatever the iframe / form posted back
) -> dict:
    """
    Exchange iframe / mandate form data for a stable opaque token.

    In stub mode: synthesizes a token + extracts safe metadata
    (last4, brand, exp) for storage. NEVER reads/echoes PAN or CVV.

    In production mode (future): POSTs to PayPlus's vault endpoint and
    returns the provider's actual token plus the metadata fields
    PayPlus surfaces.

    Returns:
        {
          "provider":        "payplus",
          "provider_token":  "<opaque>",
          "card_last4":      "1234"       (credit_card only)
          "card_brand":      "visa"       (credit_card only)
          "card_exp_month":  4            (credit_card only)
          "card_exp_year":   2030         (credit_card only)
          "bank_code":       "012"        (direct_debit only)
          "branch_code":     "045"        (direct_debit only)
          "account_last4":   "9876"       (direct_debit only)
        }
    """
    if kind not in ("credit_card", "direct_debit"):
        raise ValueError("kind must be 'credit_card' or 'direct_debit'")

    if PAYPLUS_BACKEND == "stub":
        return _stub_tokenize(kind, raw_payload)

    # Production path — placeholder for the eventual httpx call
    raise NotImplementedError(
        "Live PayPlus tokenization lands when PAYPLUS_API_KEY is provisioned."
    )


def _stub_tokenize(kind: str, raw: dict) -> dict:
    """Deterministic-shape stub. Never touches real card data."""
    common = {
        "provider": "payplus",
        "provider_token": f"stub_{uuid.uuid4().hex}",
    }
    if kind == "credit_card":
        # Pull display-only fields from the request — these come from the
        # PayPlus iframe, NOT from a raw card form on Aurora's origin.
        return {
            **common,
            "card_last4": str(raw.get("card_last4", "0000"))[-4:],
            "card_brand": (raw.get("card_brand") or "visa").lower(),
            "card_exp_month": int(raw.get("card_exp_month") or 12),
            "card_exp_year": int(raw.get("card_exp_year") or 2030),
        }
    # direct_debit
    return {
        **common,
        "bank_code":     str(raw.get("bank_code", "012"))[:4],
        "branch_code":   str(raw.get("branch_code", "045"))[:4],
        "account_last4": str(raw.get("account_last4", "0000"))[-4:],
    }


# ─────────────────────────────────────────────────────────────
# payplus_charge
# ─────────────────────────────────────────────────────────────
def payplus_charge(
    *,
    provider_token: str,
    amount_minor_units: int,
    currency: str = "ILS",
    idempotency_key: str,
    description: str = "",
) -> dict:
    """
    Charge a stored token. Stub mode synthesizes a success response
    deterministically — useful for end-to-end testing of the full flow
    before live processing.

    Returns:
        {
          "status":             "succeeded" | "failed",
          "provider_charge_id": "<opaque>",
          "failure_code":       None | "<code>",
          "failure_message":    None | "<msg>",
        }
    """
    if amount_minor_units <= 0:
        raise ValueError("amount_minor_units must be > 0")
    if not provider_token:
        raise ValueError("provider_token is required")
    if not idempotency_key:
        raise ValueError("idempotency_key is required (prevents double-charge on retry)")

    if PAYPLUS_BACKEND == "stub":
        # Deterministic outcome: stub_TOKEN succeeds; tokens starting with
        # "fail_" simulate decline. This lets tests drive both branches.
        if provider_token.startswith("fail_"):
            return {
                "status": "failed",
                "provider_charge_id": None,
                "failure_code": "card_declined",
                "failure_message": "Stub-simulated decline",
            }
        return {
            "status": "succeeded",
            "provider_charge_id": f"stub_charge_{uuid.uuid4().hex[:12]}",
            "failure_code": None,
            "failure_message": None,
        }

    # Production path — placeholder
    raise NotImplementedError(
        "Live PayPlus charges land when PAYPLUS_API_KEY is provisioned."
    )
