"""
Aurora LTS — SMS Client
========================
Thin abstraction over Inforu (IL-primary) and Twilio (international fallback).
Used exclusively by otp_service._send_via_provider() for phone OTP delivery.

PROVIDER SELECTION (SMS_PROVIDER env var):
  stub    — log only, no network call (default for local dev)
  inforu  — Israeli SMS via api.inforu.co.il (cheapest for +972 numbers)
  twilio  — Twilio REST API (international fallback or if Inforu is down)

REQUIRED ENV VARS (per provider):
  Inforu:  INFORU_API_KEY, INFORU_SENDER_ID
  Twilio:  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER

DESIGN NOTES:
  - All network calls are synchronous (httpx.Client) — this runs inside
    FastAPI sync endpoints via issue_otp().
  - On provider API error: raises OtpDeliveryError (callers catch → HTTP 503).
  - SMS body is always server-controlled, never user-interpolated.
  - Phone number is the only user-supplied value; it is pre-validated
    to E.164 before reaching this module.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger(__name__)


class OtpSmsDeliveryError(Exception):
    """Raised when the configured SMS provider fails to accept the message."""


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def send_sms(phone_e164: str, body: str) -> None:
    """Dispatch one SMS to ``phone_e164``.

    Args:
        phone_e164: Destination in E.164 format (e.g. "+972501234567").
        body:       Message text (server-controlled; max ~160 chars for one segment).

    Raises:
        OtpSmsDeliveryError: If the configured provider returns an error.
    """
    provider = (os.getenv("SMS_PROVIDER") or "stub").strip().lower()

    if provider == "stub":
        log.info("[sms] STUB — would send to %s", _mask(phone_e164))
        return

    if provider == "inforu":
        _inforu_send(phone_e164, body)
    elif provider == "twilio":
        _twilio_send(phone_e164, body)
    else:
        raise OtpSmsDeliveryError(
            f"Unknown SMS_PROVIDER='{provider}'. Valid values: stub | inforu | twilio"
        )


def is_configured() -> bool:
    """True if a real (non-stub) SMS provider is configured with credentials."""
    provider = (os.getenv("SMS_PROVIDER") or "stub").strip().lower()
    if provider == "inforu":
        return bool(os.getenv("INFORU_API_KEY"))
    if provider == "twilio":
        return bool(os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN"))
    return False


# ─────────────────────────────────────────────────────────────
# Inforu  (api.inforu.co.il)
# ─────────────────────────────────────────────────────────────
_INFORU_API = "https://api.inforu.co.il/SendMessageXml.ashx"


def _inforu_send(phone_e164: str, body: str) -> None:
    """POST to Inforu's XML/REST gateway.

    Inforu accepts POST with XML body, returns HTTP 200 on success.
    Error codes are embedded in the XML response.
    Auth: Username (API key) + Password in the XML payload.
    """
    api_key = (os.getenv("INFORU_API_KEY") or "").strip()
    sender_id = (os.getenv("INFORU_SENDER_ID") or "Aurora").strip()

    if not api_key:
        log.warning("[sms/inforu] INFORU_API_KEY not set — SMS NOT sent to %s", _mask(phone_e164))
        raise OtpSmsDeliveryError("INFORU_API_KEY not configured")

    # Normalize: Inforu expects digits only, no leading + or country code prefix
    number_digits = phone_e164.lstrip("+")

    xml_body = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<Inforu>'
        f'<User><Username>{api_key}</Username><Password></Password></User>'
        f'<Content Type="SMS"><Message>{body}</Message></Content>'
        f'<Recipients><PhoneNumber>{number_digits}</PhoneNumber></Recipients>'
        f'<Settings>'
        f'<Sender>{sender_id}</Sender>'
        f'<CustomerMessageId></CustomerMessageId>'
        f'</Settings>'
        f'</Inforu>'
    )

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                _INFORU_API,
                content=xml_body.encode("utf-8"),
                headers={"Content-Type": "application/xml; charset=utf-8"},
            )
        if resp.status_code != 200:
            log.error(
                "[sms/inforu] HTTP %s for %s — body: %s",
                resp.status_code, _mask(phone_e164), resp.text[:200],
            )
            raise OtpSmsDeliveryError(f"Inforu returned HTTP {resp.status_code}")
        # Inforu embeds error codes in the XML: <Status>4</Status> means failed
        if "<Status>4</Status>" in resp.text or "<Status>-" in resp.text:
            log.error("[sms/inforu] API error for %s — %s", _mask(phone_e164), resp.text[:200])
            raise OtpSmsDeliveryError("Inforu rejected the message")
        log.info("[sms/inforu] SMS queued to %s", _mask(phone_e164))
    except httpx.RequestError as exc:
        log.error("[sms/inforu] network error to %s: %s", _mask(phone_e164), exc)
        raise OtpSmsDeliveryError(f"Inforu network error: {exc}") from exc


# ─────────────────────────────────────────────────────────────
# Twilio  (api.twilio.com)
# ─────────────────────────────────────────────────────────────
_TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"


def _twilio_send(phone_e164: str, body: str) -> None:
    """POST to Twilio's Messages REST endpoint (Basic Auth)."""
    account_sid = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
    auth_token  = (os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
    from_number = (os.getenv("TWILIO_FROM_NUMBER") or "").strip()

    if not account_sid or not auth_token or not from_number:
        missing = [k for k, v in {
            "TWILIO_ACCOUNT_SID": account_sid,
            "TWILIO_AUTH_TOKEN": auth_token,
            "TWILIO_FROM_NUMBER": from_number,
        }.items() if not v]
        log.warning("[sms/twilio] Missing env vars %s — SMS NOT sent to %s", missing, _mask(phone_e164))
        raise OtpSmsDeliveryError(f"Twilio not configured (missing: {missing})")

    url = _TWILIO_API.format(account_sid=account_sid)
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                url,
                data={"To": phone_e164, "From": from_number, "Body": body},
                headers={"Authorization": f"Basic {credentials}"},
            )
        if resp.status_code not in (200, 201):
            log.error(
                "[sms/twilio] HTTP %s for %s — %s",
                resp.status_code, _mask(phone_e164), resp.text[:200],
            )
            raise OtpSmsDeliveryError(f"Twilio returned HTTP {resp.status_code}")
        # Status was 2xx → the SMS was accepted. Parsing the body for the sid is
        # best-effort logging only; a non-JSON/empty body must NOT turn an
        # accepted send into a 500.
        try:
            sid = resp.json().get("sid")
        except Exception:  # noqa: BLE001 — JSONDecodeError etc. on an already-accepted send
            sid = None
        log.info("[sms/twilio] SMS queued to %s sid=%s", _mask(phone_e164), sid)
    except httpx.RequestError as exc:
        log.error("[sms/twilio] network error to %s: %s", _mask(phone_e164), exc)
        raise OtpSmsDeliveryError(f"Twilio network error: {exc}") from exc


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _mask(phone: str) -> str:
    """Redact phone for logs: '+972501234567' → '+97*****567'."""
    if not phone or len(phone) < 5:
        return "***"
    return f"{phone[:3]}{'*' * (len(phone) - 6)}{phone[-3:]}"
