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
import logging
import os
import uuid

import httpx

log = logging.getLogger(__name__)

PAYPLUS_BACKEND = (os.getenv("PAYPLUS_BACKEND") or "stub").strip().lower()
PAYPLUS_API_BASE = os.getenv("PAYPLUS_API_BASE") or "https://restapi.payplus.co.il/api/v1.0"


# ─────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────
class PayPlusError(Exception):
    """
    Raised when PayPlus returns a non-success HTTP status or a network
    transport error. Maps to HTTP 503 at the router / billing-sweep level.
    Declined cards are NOT raised — they return {"status": "failed"}.
    """
    def __init__(self, detail: str):
        super().__init__(f"PayPlus error: {detail}")


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────
def _payplus_backend() -> str:
    """Read PAYPLUS_BACKEND at call time (not import time) so test env vars work."""
    return (os.getenv("PAYPLUS_BACKEND") or "stub").strip().lower()


def _payplus_creds() -> tuple[str, str]:
    """
    Return (api_key, terminal_number).

    Raises ValueError before any network call if credentials are absent or
    still contain the placeholder values from .env.example.
    """
    key = (os.getenv("PAYPLUS_API_KEY") or "").strip()
    terminal = (os.getenv("PAYPLUS_TERMINAL_NUMBER") or "").strip()
    if not key or key.upper().startswith("YOUR_"):
        raise ValueError(
            "PAYPLUS_API_KEY is not configured. "
            "Set PAYPLUS_BACKEND=stub for development or supply real credentials "
            "from the PayPlus merchant portal."
        )
    if not terminal or terminal.upper().startswith("YOUR_"):
        raise ValueError(
            "PAYPLUS_TERMINAL_NUMBER is not configured. "
            "Find it in your PayPlus merchant portal → Terminals."
        )
    return key, terminal


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

    if _payplus_backend() == "stub":
        return _stub_tokenize(kind, raw_payload)

    # Production path.
    # The PayPlus iframe already validated the card with PayPlus's servers
    # before the browser POSTed to Aurora. The token in raw_payload is
    # genuine — no server-to-server confirmation call is needed here.
    # Aurora's role is to extract and normalize the display-only metadata
    # that PayPlus's iframe included in its postMessage payload.
    _payplus_creds()  # fail loudly before touching raw_payload if misconfigured

    token = (raw_payload.get("token") or "").strip()
    if not token:
        raise ValueError(
            "PayPlus iframe payload missing 'token'. "
            "Ensure the frontend reads the postMessage from the PayPlus iframe correctly."
        )

    common = {
        "provider": "payplus",
        "provider_token": token,
    }
    if kind == "credit_card":
        def _exp(field: str):
            """Coerce an expiry field to int; a malformed value → ValueError (→ 400), never a raw 500."""
            v = raw_payload.get(field)
            if not v:
                return None
            try:
                return int(v)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"PayPlus payload field '{field}' is malformed") from exc
        return {
            **common,
            "card_last4":     str(raw_payload.get("card_last4") or "")[-4:] or None,
            "card_brand":     (raw_payload.get("card_brand") or "").lower() or None,
            "card_exp_month": _exp("card_exp_month"),
            "card_exp_year":  _exp("card_exp_year"),
        }
    # direct_debit
    return {
        **common,
        "bank_code":     str(raw_payload.get("bank_code") or "")[:4] or None,
        "branch_code":   str(raw_payload.get("branch_code") or "")[:4] or None,
        "account_last4": str(raw_payload.get("account_last4") or "")[-4:] or None,
    }


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

    if _payplus_backend() == "stub":
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

    # Production path — POST to PayPlus ChargeByToken.
    # NOTE: PayPlus rate limit is ~60 req/min per terminal. The billing sweep
    # (internal.py) processes payments sequentially; ensure the sweep cron
    # interval is ≥3 min for batches up to 100 payments.
    api_key, terminal = _payplus_creds()
    api_base = (os.getenv("PAYPLUS_API_BASE") or "https://restapi.payplus.co.il/api/v1.0").rstrip("/")

    # PayPlus amounts are decimal ILS (not agorot). 1000 agorot = 10.00 ILS.
    amount_ils = round(amount_minor_units / 100, 2)

    body = {
        "terminal_number": terminal,
        "api_key": api_key,
        "payload": {
            "purchase_description": description or "Aurora LTS subscription",
        },
        "credit_card_info": {
            "token": provider_token,
            "amount": amount_ils,
            "currency_id": "1",          # 1 = ILS in PayPlus's enum
            "number_of_payments": 1,
        },
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{api_base}/Transaction/ChargeByToken",
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": api_key,
                    "Idempotency-Key": idempotency_key,
                },
            )
    except httpx.RequestError as exc:
        log.error("[payplus] network error during charge idempotency_key=%s: %s",
                  idempotency_key, exc)
        raise PayPlusError(f"Network error: {exc}") from exc

    if resp.status_code not in (200, 201):
        log.error("[payplus] HTTP %s for charge idempotency_key=%s body=%s",
                  resp.status_code, idempotency_key, resp.text[:300])
        raise PayPlusError(f"HTTP {resp.status_code} from PayPlus")

    try:
        data = resp.json()
    except Exception as exc:
        raise PayPlusError(f"Non-JSON response from PayPlus: {resp.text[:200]}") from exc

    results = data.get("results", {})
    status_flag = results.get("status")   # 1 = success, 0 = failure/decline

    if status_flag == 1:
        uid = (
            data.get("data", {})
                .get("transaction_details", {})
                .get("uid")
        )
        log.info("[payplus] charge succeeded uid=%s idempotency_key=%s amount=%.2f ILS",
                 uid, idempotency_key, amount_ils)
        return {
            "status": "succeeded",
            "provider_charge_id": uid,
            "failure_code": None,
            "failure_message": None,
        }

    # status_flag == 0 or missing — card declined or mandate rejected.
    # This is a business outcome, not a system error — do NOT raise.
    failure_code = str(results.get("code") or "unknown")
    failure_msg  = str(results.get("message") or "Charge declined")
    log.warning("[payplus] charge declined code=%s msg=%s idempotency_key=%s",
                failure_code, failure_msg, idempotency_key)
    return {
        "status": "failed",
        "provider_charge_id": None,
        "failure_code": failure_code,
        "failure_message": failure_msg,
    }
