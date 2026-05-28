"""
Aurora LTS — Payment Links Service  (P2-23)
============================================

Generates signed, time-limited payment URLs for invoices.
The link opens a lightweight hosted checkout page backed by
PayPlus embedded iframe — Aurora never handles raw card data.

FLOW
────
  1. POST /api/v1/invoices/{id}/payment-link
       Server creates a PaymentLink row (status='open')
       Returns { url: "https://api-aurora-lts.com/pay/{token}" }

  2. Customer clicks the link → GET /pay/{token}
       Server verifies token is valid, not expired, not already paid
       Renders the checkout page with PayPlus iframe (amount pre-filled)

  3. PayPlus iframe captures card/bank details → processes payment
       PayPlus calls POST /api/v1/webhooks/payplus-ipn with result

  4. Aurora updates payment record + link status
       Invoice marked 'paid' when payment confirmed

SECURITY
────────
  • Token = HMAC-SHA256(invoice_id + expires_at + nonce, PAYMENT_LINK_SECRET)
  • Tokens are 32-byte URL-safe base64 (opaque to client)
  • TTL: configurable via PAYMENT_LINK_TTL_HOURS (default 72)
  • Single-use: link invalidated after first successful payment
  • No PAN/CVV ever reaches Aurora (PCI SAQ-A scope)
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import logging
import os
import secrets
from typing import Optional

from sqlalchemy.orm import Session

from app.database import ActionLog
from app.database.models import PaymentLink, Invoice, Business
from app.config.secrets import optional_secret

log = logging.getLogger(__name__)

TTL_HOURS_DEFAULT = 72


def _ttl_hours() -> int:
    try:
        return int(os.getenv("PAYMENT_LINK_TTL_HOURS", str(TTL_HOURS_DEFAULT)))
    except ValueError:
        return TTL_HOURS_DEFAULT


def _link_secret() -> str:
    return optional_secret("PAYMENT_LINK_SECRET") or "aurora-dev-link-secret-rotate-me"


def _base_url() -> str:
    return (os.getenv("AURORA_API_BASE_URL") or "https://api-aurora-lts.com").rstrip("/")


# ─────────────────────────────────────────────────────────────
# Token helpers
# ─────────────────────────────────────────────────────────────

def _sign_token(invoice_id: int, nonce: str, expires_at: datetime.datetime) -> str:
    """HMAC-SHA256 over invoice_id:nonce:expires_at_iso."""
    message = f"{invoice_id}:{nonce}:{expires_at.isoformat()}".encode()
    digest = hmac.new(_link_secret().encode(), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _verify_token(token: str, invoice_id: int, nonce: str, expires_at: datetime.datetime) -> bool:
    expected = _sign_token(invoice_id, nonce, expires_at)
    return hmac.compare_digest(token, expected)


# ─────────────────────────────────────────────────────────────
# Create link
# ─────────────────────────────────────────────────────────────

def create_payment_link(
    invoice_id: int,
    db: Session,
    created_by_user_id: int,
    ttl_hours: Optional[int] = None,
) -> dict:
    """
    Create a signed payment link for an invoice.

    Returns:
        {
            "link_id": int,
            "url": "https://api-aurora-lts.com/pay/<token>",
            "expires_at": "<iso8601>",
            "amount_ils": float,
            "invoice_number": str,
        }

    Raises ValueError if the invoice is not in a payable state.
    """
    invoice = db.query(Invoice).filter_by(id=invoice_id).first()
    if not invoice:
        raise ValueError(f"Invoice {invoice_id} not found")
    if invoice.status in ("cancelled", "credit_note"):
        raise ValueError(f"Invoice {invoice_id} is {invoice.status} — cannot create payment link")
    if invoice.status == "paid":
        raise ValueError(f"Invoice {invoice_id} is already paid")

    ttl = ttl_hours or _ttl_hours()
    nonce = secrets.token_hex(16)
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(hours=ttl)
    token = _sign_token(invoice_id, nonce, expires_at)

    link = PaymentLink(
        invoice_id=invoice_id,
        business_id=invoice.business_id,
        token=token,
        nonce=nonce,
        expires_at=expires_at,
        amount_ils=invoice.total_amount,
        currency="ILS",
        status="open",
        created_by_user_id=created_by_user_id,
    )
    db.add(link)
    db.add(ActionLog(
        business_id=invoice.business_id,
        status="payment_link.created",
        detail=f"invoice_id={invoice_id} expires_at={expires_at.isoformat()} ttl_hours={ttl}",
    ))
    db.commit()
    db.refresh(link)

    url = f"{_base_url()}/pay/{token}"
    return {
        "link_id": link.id,
        "url": url,
        "expires_at": expires_at.isoformat(),
        "amount_ils": invoice.total_amount,
        "invoice_number": getattr(invoice, "invoice_number", str(invoice_id)),
    }


# ─────────────────────────────────────────────────────────────
# Verify link (called by the checkout page handler)
# ─────────────────────────────────────────────────────────────

def resolve_payment_link(token: str, db: Session) -> PaymentLink:
    """
    Validate a payment link token and return the PaymentLink row.

    Raises ValueError with a user-safe message on any failure.
    """
    link = db.query(PaymentLink).filter_by(token=token).first()
    if not link:
        raise ValueError("Link not found or already used")
    if link.status != "open":
        raise ValueError(f"This payment link has been {link.status}")
    if datetime.datetime.utcnow() > link.expires_at:
        link.status = "expired"
        db.commit()
        raise ValueError("This payment link has expired")
    if not _verify_token(token, link.invoice_id, link.nonce, link.expires_at):
        raise ValueError("Invalid payment link signature")
    return link


# ─────────────────────────────────────────────────────────────
# Build PayPlus checkout session (called by checkout page)
# ─────────────────────────────────────────────────────────────

def create_payplus_checkout(link: PaymentLink, db: Session) -> dict:
    """
    Create a PayPlus checkout session for this link.
    Returns the PayPlus page_request_uid and iframe_url to embed.
    """
    from app.services.onboarding.payplus_client import _payplus_backend, _payplus_creds
    import httpx

    backend = _payplus_backend()
    if backend == "stub":
        return {
            "page_request_uid": f"stub-{link.id}",
            "iframe_url": f"https://checkout.payplus.co.il/stub?amount={link.amount_ils}",
            "backend": "stub",
        }

    # Production
    api_key, terminal_number = _payplus_creds()
    invoice = db.query(Invoice).filter_by(id=link.invoice_id).first()
    biz = db.query(Business).filter_by(id=link.business_id).first()

    payload = {
        "payment_page_uid": None,   # omit → PayPlus generates
        "charge_method": 1,         # 1 = credit card + bit
        "create_token": False,
        "currency_code": "ILS",
        "sendEmailApproval": False,  # Aurora handles email
        "amount": link.amount_ils,
        "show_cart": True,
        "products": [{
            "name": f"חשבונית {getattr(invoice, 'invoice_number', link.invoice_id)}",
            "quantity": 1,
            "price": link.amount_ils,
                    "vat_type": 0,      # VAT included
        }],
        "customer": {
            "customer_name": getattr(biz, "name", ""),
        },
        "more_info": str(link.invoice_id),   # our internal ref
        "callback_url": f"{_base_url()}/api/v1/webhooks/payplus-ipn",
        "success_url": f"{_base_url()}/pay/{link.token}/success",
        "cancel_url":  f"{_base_url()}/pay/{link.token}/cancel",
    }

    headers = {
        "Authorization": api_key,
        "TerminalNumber": terminal_number,
        "Content-Type": "application/json",
    }

    response = httpx.post(
        "https://restapi.payplus.co.il/api/v1.0/PaymentPages/generateLink",
        json=payload, headers=headers, timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "page_request_uid": data.get("data", {}).get("page_request_uid"),
        "iframe_url": data.get("data", {}).get("payment_page_link"),
        "backend": "payplus",
    }


# ─────────────────────────────────────────────────────────────
# PayPlus IPN webhook handler
# ─────────────────────────────────────────────────────────────

def handle_payplus_ipn(
    payload: dict,
    db: Session,
) -> dict:
    """
    Process a PayPlus Instant Payment Notification.
    Updates PaymentLink status + creates a Payment row on success.
    """
    more_info = str(payload.get("more_info", ""))
    status_code = payload.get("status_code")

    try:
        invoice_id = int(more_info)
    except ValueError:
        log.warning("PayPlus IPN: could not parse invoice_id from more_info=%r", more_info)
        return {"ok": False, "reason": "invalid more_info"}

    link = (
        db.query(PaymentLink)
        .filter(PaymentLink.invoice_id == invoice_id, PaymentLink.status == "open")
        .first()
    )

    if status_code == "000":   # PayPlus success code
        amount = float(payload.get("amount", 0))

        if link:
            link.status = "paid"
            link.paid_at = datetime.datetime.utcnow()
            link.payplus_transaction_id = str(payload.get("transaction_uid", ""))

        # Delegate to the payment_service to record a proper Payment row
        from app.services.payment_service import record_payment
        record_payment(
            invoice_id=invoice_id,
            amount_ils=amount,
            source="payment_link",
            note=f"PayPlus IPN uid={payload.get('transaction_uid')}",
            db=db,
        )

        db.add(ActionLog(
            business_id=link.business_id if link else None,
            status="payment_link.paid",
            detail=f"invoice_id={invoice_id} amount={amount} txn={payload.get('transaction_uid')}",
        ))
        db.commit()
        return {"ok": True, "status": "paid"}
    else:
        if link:
            link.status = "failed"
        db.commit()
        return {"ok": False, "status": "failed", "status_code": status_code}
