"""
ASG / Aurora Solutions — OTP Service
=======================================
Issues 6-digit One-Time Passwords for phone/email verification during
onboarding. Hashes the code with bcrypt before persisting — plaintext
codes never hit the database.

CRYPTO POSTURE:
  - Code generation: cryptographically-strong random (secrets.randbelow)
  - Storage: bcrypt hash via passlib (the same library used for User passwords)
  - Verification: passlib.verify — constant-time-ish comparison against hash
  - TTL: 10 minutes default for signup; 5 minutes for step-up
  - Lockout: 5 wrong attempts → status='locked'; user must request a new OTP
  - Single-use: status flips to 'consumed' atomically on successful verify

DELIVERY (DEV vs PROD):
  Dev (OTP_BACKEND=stub):
    - The 6-digit code is also returned in the response payload,
      tagged with a clear `dev_only_code` field.
    - This lets the founder test the full flow end-to-end without a
      real SMS / email provider attached.

  Prod (OTP_BACKEND=inforu | twilio | sendgrid):
    - The code is dispatched via the configured provider; the API
      response excludes `dev_only_code`.
    - Implementation lands when the founder picks a provider.
"""

import datetime
import os
import secrets
from typing import Optional

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from aurora_shared.database import (
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


def _send_via_provider(channel: str, target: str, code: str) -> None:
    """
    Dispatch the code via the configured channel/provider.
    Currently a stub that just prints to the server log — real provider
    integration (Inforu for SMS, SendGrid for email) lands in a later slice.
    """
    backend = _otp_backend()
    if backend == "stub":
        # Log only the channel/target; the code is never logged at INFO level.
        print(f"[OTP] STUB dispatch  channel={channel}  target={target[:3]}***{target[-2:]}")
        return

    # Future: switch on backend and dispatch via real provider.
    # For now, raise so misconfiguration is loud:
    raise NotImplementedError(
        f"OTP_BACKEND='{backend}' is not implemented yet. Use 'stub' until "
        "an SMS/email provider is wired."
    )


# ─────────────────────────────────────────────────────────────
# issue_otp
# ─────────────────────────────────────────────────────────────
def issue_otp(
    *,
    user_id: int,
    channel: str,
    target: str,
    purpose: str = "signup",
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
    _send_via_provider(channel, target, code)

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
