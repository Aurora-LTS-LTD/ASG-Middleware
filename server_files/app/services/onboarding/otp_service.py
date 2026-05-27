"""
ASG / Aurora Solutions — OTP Service
=======================================
Issues 6-digit One-Time Passwords for phone/email verification during
onboarding. Hashes the code with bcrypt before persisting — plaintext
codes never hit the database.

CRYPTO POSTURE:
  - Code generation: cryptographically-strong random (secrets.randbelow)
  - Storage: bcrypt hash via passlib (the same library used for User passwords)
  - Verification: passlib.verify — constant-time comparison against hash
  - TTL: 10 minutes default for signup; 5 minutes for step-up
  - Lockout: 5 wrong attempts → status='locked'; user must request a new OTP
  - Single-use: status flips to 'consumed' atomically on successful verify

DELIVERY WATERFALL:
  Dev (OTP_BACKEND=stub):
    - Code returned in response as `dev_only_code` for local testing.
    - No network calls made.

  Prod (OTP_BACKEND=production):
    Phone channel:
      Tier 1 → WhatsApp (sendgrid_client.send_whatsapp_otp)
               — skipped if WHATSAPP_OTP_TEMPLATE_NAME not set
      Tier 2 → SMS (sms_client.send_sms)
               — requires SMS_PROVIDER + provider credentials
      On all tiers exhausted → raises OtpDeliveryError → HTTP 503

    Email channel:
      Tier 1 → SendGrid (sendgrid_client.send_onboarding_otp_email)
               — requires SENDGRID_API_KEY
      On failure → raises OtpDeliveryError → HTTP 503

SECURITY NOTE — LOCKOUT DoS VECTOR:
  The 5-attempt lockout is per OtpVerification row (per target/user).
  A malicious actor who knows a user's phone number can lock their OTP
  row by submitting 5 wrong codes before the legitimate user can.
  Mitigation: IP-level rate limiting via slowapi (P0-09 task) must be
  deployed before the onboarding funnel goes live.

TECH DEBT — bcrypt overkill:
  bcrypt adds 200-400ms per OTP issue for negligible security gain on
  6-digit codes (keyspace = 10^6). Rate limiting is the real protection.
  HMAC-SHA256(server_secret, code) would be faster and sufficient.
  Changing now would invalidate all pending OtpVerification rows.
  Track as Sprint 8.5 tech debt.
"""

import datetime
import logging
import os
import secrets
from typing import Optional

log = logging.getLogger(__name__)

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.database import (
    OtpVerification,
    User,
    ActionLog,
)


# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────
OTP_TTL_SECONDS = 600                  # 10 minutes for signup OTPs
STEP_UP_TTL_SECONDS = 300              # 5 minutes for sensitive change OTPs
MAX_ATTEMPTS = 5                       # Lockout threshold per OtpVerification row
RESEND_COOLDOWN_SECONDS = 60           # Min seconds between consecutive issues for the same target

# ─────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────
class OtpDeliveryError(Exception):
    """Raised when all delivery tiers for a channel have failed."""
    def __init__(self, channel: str, detail: str = ""):
        self.channel = channel
        super().__init__(f"OTP delivery failed for channel='{channel}'. {detail}")


# Use the same bcrypt context as the auth_service password hashing.
# bcrypt is overkill for 6-digit codes (the keyspace is tiny), but it
# means we benefit from passlib's existing battle-tested wiring.
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _otp_backend() -> str:
    """Read OTP_BACKEND from env (default 'stub')."""
    return (os.getenv("OTP_BACKEND") or "stub").strip().lower()


def _generate_code() -> str:
    """Cryptographically-random 6-digit code (000000 to 999999, zero-padded)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _ttl_for_purpose(purpose: str) -> int:
    return STEP_UP_TTL_SECONDS if purpose == "step_up" else OTP_TTL_SECONDS


def _sms_body(code: str, lang: str = "he") -> str:
    """Build a short SMS body for the given language."""
    _BODIES = {
        "he": f"קוד האימות שלך ב-Aurora LTS: {code}. תקף ל-10 דקות. אל תשתף אותו.",
        "ar": f"رمز التحقق الخاص بك في Aurora LTS: {code}. صالح لمدة 10 دقائق. لا تشاركه.",
        "en": f"Your Aurora LTS verification code: {code}. Valid 10 min. Do not share.",
    }
    return _BODIES.get(lang, _BODIES["en"])


def _send_via_provider(channel: str, target: str, code: str, lang: str = "he") -> None:
    """
    Dispatch the OTP via the appropriate channel.

    Email:  SendGrid (send_onboarding_otp_email) → OtpDeliveryError on failure
    Phone:  Tier 1 WhatsApp (if env vars set) → Tier 2 SMS → OtpDeliveryError if both fail
    Stub:   Log only; no network calls.
    """
    backend = _otp_backend()
    if backend == "stub":
        log.info("[OTP] STUB dispatch channel=%s target=%s", channel, _mask_target(target))
        return

    if channel == "email":
        try:
            from app.services.sendgrid_client import send_onboarding_otp_email
            send_onboarding_otp_email(target, code, ttl_minutes=10, lang=lang)
        except RuntimeError as exc:
            raise OtpDeliveryError("email", str(exc)) from exc
        return

    if channel == "phone":
        _dispatched = False

        # Tier 1 — WhatsApp (only if all three env vars are present)
        wa_vars = ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_OTP_TEMPLATE_NAME")
        if all(os.getenv(v) for v in wa_vars):
            try:
                from app.services.sendgrid_client import send_whatsapp_otp
                send_whatsapp_otp(target, code)
                _dispatched = True
            except RuntimeError as exc:
                log.warning("[OTP] WhatsApp tier failed (%s) — falling back to SMS", exc)

        # Tier 2 — SMS (Inforu or Twilio depending on SMS_PROVIDER)
        if not _dispatched:
            try:
                from app.services.sms_client import send_sms, OtpSmsDeliveryError
                send_sms(target, _sms_body(code, lang))
                _dispatched = True
            except OtpSmsDeliveryError as exc:
                raise OtpDeliveryError("phone", str(exc)) from exc

        return

    raise OtpDeliveryError(channel, f"Unknown channel '{channel}'")


# ─────────────────────────────────────────────────────────────
# issue_otp
# ─────────────────────────────────────────────────────────────
def issue_otp(
    *,
    user_id: int,
    channel: str,
    target: str,
    purpose: str = "signup",
    lang: str = "he",
    db: Session,
    request_ip: Optional[str] = None,
) -> dict:
    """
    Generate a 6-digit OTP for the given user/target, persist its bcrypt
    hash, and dispatch via the configured provider.

    Returns:
        {
          "id":            "<uuid>",
          "expires_in":    600,
          "channel":       "phone" | "email",
          "target_masked": "+972*****567",
          "dev_only_code": "<code>"  # ONLY when OTP_BACKEND=stub
        }

    Raises ValueError on:
      - bad channel
      - missing/inactive user
      - resend within cooldown window for the same target
    """
    if channel not in ("phone", "email"):
        raise ValueError("channel must be 'phone' or 'email'")
    if not target or len(target) < 3:
        raise ValueError("target is required")

    # E.164 validation for phone channel — blocks toll-fraud on non-IL numbers
    if channel == "phone":
        import phonenumbers
        try:
            parsed = phonenumbers.parse(target, None)
            if not phonenumbers.is_valid_number(parsed):
                raise ValueError(f"Invalid phone number: {_mask_target(target)}")
            # Restrict to Israeli numbers (+972) in production to limit toll fraud
            if _otp_backend() != "stub" and parsed.country_code != 972:
                raise ValueError("Only Israeli (+972) phone numbers are supported")
        except phonenumbers.NumberParseException as exc:
            raise ValueError(f"Phone number parse error: {exc}") from exc

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()  # noqa: E712
    if not user:
        raise ValueError(f"user_id={user_id} not found or inactive")

    # ── Cooldown: prevent flood-resend by reusing the same channel/target ──
    cooldown_cutoff = datetime.datetime.utcnow() - datetime.timedelta(seconds=RESEND_COOLDOWN_SECONDS)
    recent = (
        db.query(OtpVerification)
        .filter(
            OtpVerification.user_id == user.id,
            OtpVerification.channel == channel,
            OtpVerification.target == target,
            OtpVerification.created_at > cooldown_cutoff,
        )
        .with_for_update()
        .first()
    )
    if recent:
        seconds_left = int(
            RESEND_COOLDOWN_SECONDS
            - (datetime.datetime.utcnow() - recent.created_at).total_seconds()
        )
        raise ValueError(
            f"Resend cooldown active. Wait {max(seconds_left, 1)} seconds."
        )

    # ── Mark any prior pending OTPs for the same target as expired ──
    db.query(OtpVerification).filter(
        OtpVerification.user_id == user.id,
        OtpVerification.channel == channel,
        OtpVerification.target == target,
        OtpVerification.status == "pending",
    ).update({"status": "expired"})

    # ── Generate, hash, persist ──
    code = _generate_code()
    code_hash = _pwd.hash(code)
    ttl = _ttl_for_purpose(purpose)
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=ttl)

    otp_row = OtpVerification(
        user_id=user.id,
        channel=channel,
        target=target,
        code_hash=code_hash,
        purpose=purpose,
        expires_at=expires_at,
        attempts=0,
        status="pending",
        last_ip=request_ip,
    )
    db.add(otp_row)

    db.add(ActionLog(
        business_id=None,
        status="otp.issued",
        detail=(
            f"otp_id={otp_row.id} user_id={user.id} channel={channel} "
            f"purpose={purpose} target={_mask_target(target)}"
        ),
    ))
    db.commit()

    # ── Dispatch (stub or real) ──
    _send_via_provider(channel, target, code, lang=lang)

    payload = {
        "id": otp_row.id,
        "expires_in": ttl,
        "channel": channel,
        "target_masked": _mask_target(target),
    }

    # In dev/stub mode, surface the code so the founder can complete the
    # flow end-to-end. NEVER do this in production — the env-toggle gates it.
    if _otp_backend() == "stub":
        payload["dev_only_code"] = code

    return payload


# ─────────────────────────────────────────────────────────────
# verify_otp
# ─────────────────────────────────────────────────────────────
def verify_otp(
    *,
    user_id: int,
    channel: str,
    target: str,
    code: str,
    db: Session,
    request_ip: Optional[str] = None,
) -> bool:
    """
    Verify a user-submitted 6-digit code against the most-recent pending
    OtpVerification row for the (user, channel, target) tuple.

    Behavior:
      - Correct code, not expired, not locked → status='consumed', return True
      - Wrong code → bump attempts; lock on hit max → return False
      - Expired   → mark 'expired', return False
      - No pending row → return False

    Side effects on success:
      - The corresponding User column (email_verified_at or phone_verified_at)
        is stamped to now.
    """
    if channel not in ("phone", "email"):
        return False
    if not code or len(code) != 6 or not code.isdigit():
        return False

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False

    otp_row = (
        db.query(OtpVerification)
        .filter(
            OtpVerification.user_id == user_id,
            OtpVerification.channel == channel,
            OtpVerification.target == target,
            OtpVerification.status == "pending",
        )
        .order_by(OtpVerification.created_at.desc())
        .first()
    )
    if not otp_row:
        return False

    now = datetime.datetime.utcnow()

    # ── Expiry check ──
    if otp_row.expires_at < now:
        otp_row.status = "expired"
        db.add(ActionLog(
            business_id=None,
            status="otp.expired",
            detail=f"otp_id={otp_row.id} user_id={user_id}",
        ))
        db.commit()
        return False

    # ── Verify the bcrypt hash ──
    is_correct = _pwd.verify(code, otp_row.code_hash)

    if not is_correct:
        otp_row.attempts = (otp_row.attempts or 0) + 1
        if otp_row.attempts >= MAX_ATTEMPTS:
            otp_row.status = "locked"
            db.add(ActionLog(
                business_id=None,
                status="otp.locked",
                detail=f"otp_id={otp_row.id} user_id={user_id} attempts={otp_row.attempts}",
            ))
        else:
            db.add(ActionLog(
                business_id=None,
                status="otp.failed",
                detail=f"otp_id={otp_row.id} user_id={user_id} attempts={otp_row.attempts}",
            ))
        db.commit()
        return False

    # ── Success ──
    otp_row.status = "consumed"
    otp_row.consumed_at = now
    if request_ip:
        otp_row.last_ip = request_ip

    if channel == "phone":
        user.phone_verified_at = now
    else:
        user.email_verified_at = now

    db.add(ActionLog(
        business_id=None,
        status="otp.verified",
        detail=f"otp_id={otp_row.id} user_id={user_id} channel={channel}",
    ))
    db.commit()
    return True


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _mask_target(target: str) -> str:
    """
    Redact a phone/email for logs and API responses.
        '+972501234567'         → '+97*****567'
        'someone@example.com'   → 'so***@example.com'
    """
    if not target:
        return ""
    if "@" in target:
        local, _, domain = target.partition("@")
        if len(local) <= 2:
            return f"{local[0]}*@{domain}"
        return f"{local[:2]}***@{domain}"
    if len(target) <= 5:
        return "*" * len(target)
    return f"{target[:3]}***{target[-3:]}"
