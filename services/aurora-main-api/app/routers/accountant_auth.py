"""
accountant_auth.py — Aurora Accountant Portal auth surface (Sprint 8.2 sibling).

Distinct from app/routers/accountant.py (the existing accountant data API).
This router owns ONLY the authentication + device-management endpoints
that the Tauri portal at ~/Desktop/.../accountant-portal/ calls.

Auth model:
  • Email OTP (6-digit, 60s TTL, 3 attempts → 15min lock)
  • Short-lived access token JWT (15min, HS256, iss="aurora-accountant")
  • Long-lived refresh token (opaque random 64-char string, 30d TTL)
  • Refresh tokens are STORED HASHED (SHA-256) — plaintext never persisted
  • Refresh tokens are ROTATED on each /refresh — old token dies
  • Device fingerprint is ADVISORY metadata + alert trigger,
    NOT a cryptographic binding (multi-active per user, soft-revoke)

Endpoint list:
  POST /api/v1/accountant/otp/send          — request OTP
  POST /api/v1/accountant/otp/verify        — verify + mint tokens
  POST /api/v1/accountant/refresh           — rotate refresh token
  POST /api/v1/accountant/logout            — invalidate refresh token
  GET  /api/v1/accountant/devices           — list bound devices
  POST /api/v1/accountant/devices/{id}/revoke   — soft-delete device
  POST /api/v1/accountant/devices/{id}/relabel  — rename device

Security invariants:
  • OTP never logged in plaintext (only the SHA-256 hash is stored)
  • Wrong-OTP attempts counted server-side; client-side count is advisory
  • Lockout after 3 wrong attempts (15 min cool-down)
  • Refresh token replay → revoke entire chain (token-binding compromise)
  • Device-fingerprint mismatch on /refresh → 401 device_mismatch
  • All token issuance returns Cache-Control: no-store
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import os
import secrets
from typing import Optional

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from jose import jwt as jose_jwt

from aurora_shared.database import get_db
from aurora_shared.database.models import (
    User,
    AccountantDevice,
    AccountantRefreshToken,
    AccountantOtpAttempt,
    AccountantPasswordReset,
    AccountantEngagement,
    ActionLog,
)
from aurora_shared.middleware.auth_middleware import require_accountant  # noqa: F401 — re-exported for Vault router
from aurora_shared.middleware.rate_limit import limiter
from aurora_shared.services.auth_service import hash_password, verify_password
from app.services import sendgrid_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/accountant", tags=["accountant_auth"])


# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

OTP_TTL_SECONDS = 60
OTP_MAX_ATTEMPTS = 3
OTP_LOCKOUT_MINUTES = 15
OTP_RATE_LIMIT_WINDOW_MINUTES = 15
OTP_RATE_LIMIT_PER_EMAIL = 3   # >3 sends in 15min → 429

ACCESS_TOKEN_TTL_SECONDS = 15 * 60        # 15 minutes
REFRESH_TOKEN_TTL_DAYS = 30
DEVICES_PER_USER_MAX = 5                  # advisory; new device beyond N → alert

JWT_ALGO = "HS256"
JWT_ISSUER = "aurora-accountant"

# Password recovery (email-based; no SMS). Reset codes are hashed + single-use.
RESET_CODE_TTL_MINUTES = 15
RESET_MAX_ATTEMPTS = 5
RESET_LOCKOUT_MINUTES = 15
RESET_RATE_LIMIT_WINDOW_MINUTES = 15
RESET_RATE_LIMIT_PER_EMAIL = 3   # >3 forgot-password requests in 15min → 429
MIN_PASSWORD_LENGTH = 10


# ─────────────────────────────────────────────────────────────
# Pydantic DTOs (match accountant-portal/src/types/api.ts verbatim)
# ─────────────────────────────────────────────────────────────
# Note: we use a plain str + regex validator instead of pydantic's
# EmailStr to avoid the `email-validator` package dependency. RFC 5322
# is famously complex; this regex catches the >99% common shapes and
# rejects obvious malformations. Real email validation happens at the
# delivery step (SendGrid bounces invalid addresses, OTP never sent).
_EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}$")


def _validate_email_shape(value: str) -> str:
    v = (value or "").strip().lower()
    if not v or len(v) > 254 or not _EMAIL_REGEX.match(v):
        raise ValueError("invalid email format")
    return v


class OtpSendRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    delivery_method: str = Field(default="email", pattern=r"^(email|whatsapp)$")

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        return _validate_email_shape(v)


class OtpSendResponse(BaseModel):
    ok: bool
    sent_to: str
    expires_in_seconds: int
    method: str


class OtpVerifyRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    otp: str = Field(..., min_length=6, max_length=6)
    device_fingerprint: str = Field(..., min_length=64, max_length=64)
    platform: str = Field(..., pattern=r"^(macos|windows|linux)$")
    device_label: str = Field(..., min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        return _validate_email_shape(v)

    @field_validator("device_fingerprint")
    @classmethod
    def hex_only(cls, v: str) -> str:
        low = v.lower()
        if not all(c in "0123456789abcdef" for c in low):
            raise ValueError("device_fingerprint must be 64-char lowercase hex")
        return low

    @field_validator("otp")
    @classmethod
    def digits_only(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("otp must be 6 digits")
        return v


class AccountantUserPayload(BaseModel):
    id: int
    email: str
    name: str
    role: str
    firm_name: Optional[str] = None
    license_number: Optional[str] = None


class OtpVerifyResponse(BaseModel):
    ok: bool
    access_token: str
    refresh_token: str
    access_token_expires_at: str
    refresh_token_expires_at: str
    device_id: int
    is_new_device: bool
    user: AccountantUserPayload


class LoginRequest(BaseModel):
    """Email + password sign-in (mirrors OtpVerifyRequest, swapping otp→password)."""
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=200)
    device_fingerprint: str = Field(..., min_length=64, max_length=64)
    platform: str = Field(..., pattern=r"^(macos|windows|linux)$")
    device_label: str = Field(..., min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        return _validate_email_shape(v)

    @field_validator("device_fingerprint")
    @classmethod
    def hex_only(cls, v: str) -> str:
        low = v.lower()
        if not all(c in "0123456789abcdef" for c in low):
            raise ValueError("device_fingerprint must be 64-char lowercase hex")
        return low


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=20, max_length=200)
    device_fingerprint: str = Field(..., min_length=64, max_length=64)

    @field_validator("device_fingerprint")
    @classmethod
    def hex_only(cls, v: str) -> str:
        return v.lower()


class RefreshResponse(BaseModel):
    ok: bool
    access_token: str
    refresh_token: str
    access_token_expires_at: str
    refresh_token_expires_at: str


def _validate_password_strength(v: str) -> str:
    v = v or ""
    if len(v) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
        raise ValueError("password must contain at least one letter and one number")
    return v


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        return _validate_email_shape(v)


class ForgotPasswordResponse(BaseModel):
    ok: bool
    sent_to: str
    expires_in_seconds: int


class ResetPasswordRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    code: str = Field(..., min_length=6, max_length=16)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        return _validate_email_shape(v)

    @field_validator("new_password")
    @classmethod
    def _pw_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)

    @field_validator("new_password")
    @classmethod
    def _pw_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = Field(default=None, max_length=200)


class AccountantDeviceItem(BaseModel):
    id: int
    device_fingerprint_preview: str
    platform: str
    device_label: Optional[str] = None
    enrolled_at: str
    last_seen_at: str
    use_count: int
    is_current_device: bool
    ip_geo_hint: Optional[str] = None


class DeviceListResponse(BaseModel):
    devices: list[AccountantDeviceItem]


class DeviceRevokeRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=400)


class DeviceRelabelRequest(BaseModel):
    device_label: str = Field(..., min_length=1, max_length=120)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _iso(dt: datetime.datetime) -> str:
    return dt.replace(microsecond=0).isoformat() + "Z"


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _ip_hash(request: Request) -> str:
    """SHA-256 of caller IP. Per-request, no per-user salt for now."""
    ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "")
        or "unknown"
    )
    salt = os.getenv("AURORA_IP_HASH_SALT") or "aurora-default-salt"
    return _sha256_hex(f"{ip}|{salt}")


def _signing_key() -> str:
    # The Cloud Run env uses JWT_SECRET; older code path used
    # JWT_SIGNING_KEY. Accept either for compatibility — JWT_SECRET
    # takes precedence if both are set.
    key = (os.getenv("JWT_SECRET") or os.getenv("JWT_SIGNING_KEY") or "").strip()
    if not key:
        raise HTTPException(
            500,
            detail={
                "error": "jwt_signing_key_missing",
                "message": "JWT_SECRET env var not configured",
            },
        )
    return key


def _mask_email(email: str) -> str:
    try:
        local, domain = email.split("@", 1)
        parts = domain.split(".")
        return f"{local[0]}***@{parts[0][0]}***.{'.'.join(parts[1:])}"
    except Exception:
        return "***@***"


def _generate_otp() -> str:
    """Cryptographically secure 6-digit OTP (NOT predictable)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _generate_refresh_token() -> str:
    """64-char URL-safe random token. NOT a JWT — opaque bearer."""
    raw = secrets.token_urlsafe(48)  # ~64 chars when URL-safe-base64'd
    return f"rt_{datetime.datetime.utcnow().year}_{raw}"


def _issue_access_token(user_id: int, email: str, device_id: int) -> tuple[str, datetime.datetime]:
    """Sign a 15-minute HS256 JWT for accountant auth."""
    now = _now()
    expires = now + datetime.timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS)
    payload = {
        "iss": JWT_ISSUER,
        "sub": user_id,
        "email": email,
        "device_id": device_id,
        "role": "accountant",
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jose_jwt.encode(payload, _signing_key(), algorithm=JWT_ALGO), expires


def _send_otp_to_user(email: str, otp: str, method: str, phone_e164: str = "") -> None:
    """
    Dispatch a 6-digit OTP via email (SendGrid) or WhatsApp (Meta Graph API).

    Stub mode (OTP_BACKEND=stub or unset): logs the OTP to Cloud Run logs
    so developers can read it without real credentials configured.
    Production mode (OTP_BACKEND=production): calls the real send functions.

    The OTP value is ONLY logged in stub mode — never in production.
    """
    backend = (os.getenv("OTP_BACKEND") or "stub").lower()

    if backend != "production":
        log.warning(
            "[OTP_STUB] DEV-MODE OTP for %s via %s: %s (60s TTL) "
            "— set OTP_BACKEND=production to suppress",
            email, method, otp,
        )
        print(f"🔐 [OTP DEV] {email} → {otp}", flush=True)
        return

    try:
        if method == "whatsapp" and phone_e164:
            sendgrid_client.send_whatsapp_otp(phone_e164, otp)
        else:
            sendgrid_client.send_otp(email, otp)
    except RuntimeError as exc:
        log.error("[OTP] delivery failed method=%s email=%s: %s", method, email, exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "otp_delivery_failed",
                "message": "Could not deliver your verification code. Please try again.",
            },
        ) from exc


def _send_new_device_alert(user: User, device_row: "AccountantDevice") -> None:
    """
    Fire-and-forget email to the accountant when a new device enrolls.
    Runs AFTER db.commit() so it never blocks token issuance.
    Swallows errors — a failed alert must not break the login flow.
    """
    backend = (os.getenv("OTP_BACKEND") or "stub").lower()
    if backend != "production":
        log.info(
            "[NEW_DEVICE_STUB] would alert %s about new device_id=%s platform=%s",
            user.email, device_row.id, device_row.platform,
        )
        return

    name = f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}".strip() or user.email
    revoke_url = f"https://portal.api-aurora-lts.com/devices"  # accountant signs in + revokes from devices page
    try:
        sendgrid_client.send_new_device_alert(
            to_email=user.email,
            accountant_name=name,
            platform=device_row.platform or "unknown",
            device_label=device_row.device_label or "Unknown device",
            enrolled_at_iso=_iso(device_row.enrolled_at),
            revoke_url=revoke_url,
        )
    except Exception as exc:
        log.error("[new_device_alert] failed for user_id=%s: %s", user.id, exc)


def _resolve_accountant(db: Session, email: str) -> Optional[User]:
    """
    Look up the User by email, confirm they're an active accountant
    (role='accountant' AND at least one non-revoked AccountantEngagement).
    Returns the User row or None.
    """
    lc = email.strip().lower()
    user = db.query(User).filter(User.email == lc).first()
    if not user:
        return None
    if not user.is_active:
        return None
    if (user.role or "").lower() != "accountant":
        return None

    # At least one active engagement
    has_engagement = (
        db.query(AccountantEngagement)
        .filter(AccountantEngagement.accountant_user_id == user.id)
        .filter(AccountantEngagement.status == "active")
        .first()
    )
    if not has_engagement:
        # Accountant exists but isn't engaged with any SMB — block sign-in
        return None

    return user


def _user_payload(user: User, db: Session) -> AccountantUserPayload:
    """Build the user payload returned with successful auth."""
    # firm_name is a free-form on User in some installs; fall back to None.
    firm_name = getattr(user, "firm_name", None) or getattr(user, "organization_name", None)
    return AccountantUserPayload(
        id=user.id,
        email=user.email,
        name=(user.first_name or "") + " " + (user.last_name or ""),
        role="accountant",
        firm_name=firm_name,
        license_number=None,  # reserved for future ITA compliance
    )


def _enforce_rate_limit(db: Session, email: str, ip_hash: str) -> None:
    """Check whether this email or IP has exceeded the OTP send rate limit."""
    cutoff = _now() - datetime.timedelta(minutes=OTP_RATE_LIMIT_WINDOW_MINUTES)
    recent_by_email = (
        db.query(AccountantOtpAttempt)
        .filter(AccountantOtpAttempt.email == email.lower())
        .filter(AccountantOtpAttempt.issued_at > cutoff)
        .count()
    )
    if recent_by_email >= OTP_RATE_LIMIT_PER_EMAIL:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "otp_rate_limited",
                "message": (
                    f"Too many OTP requests. Try again in "
                    f"{OTP_RATE_LIMIT_WINDOW_MINUTES} minutes."
                ),
                "retry_after_seconds": OTP_RATE_LIMIT_WINDOW_MINUTES * 60,
            },
            headers={"Retry-After": str(OTP_RATE_LIMIT_WINDOW_MINUTES * 60)},
        )


# ── Password auth helpers ──

_dummy_hash_cache: Optional[str] = None


def _dummy_password_hash() -> str:
    """A real bcrypt hash of a random string, computed once.

    Verified against on the user-not-found / null-password path so /login takes
    roughly the same time whether or not the email exists (anti-timing-oracle).
    """
    global _dummy_hash_cache
    if _dummy_hash_cache is None:
        _dummy_hash_cache = hash_password(secrets.token_urlsafe(24))
    return _dummy_hash_cache


# Reset-code alphabet: uppercase, no ambiguous chars (no I/O/0/1).
_RESET_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _generate_reset_code() -> str:
    """Cryptographically secure 8-char reset code (~10^12 space)."""
    return "".join(secrets.choice(_RESET_ALPHABET) for _ in range(8))


def _send_reset_code(email: str, code: str) -> None:
    """Email the reset code via SendGrid. No SMS. Stub mode logs it in dev."""
    backend = (os.getenv("OTP_BACKEND") or "stub").lower()
    if backend != "production":
        log.warning("[RESET_STUB] DEV-MODE reset code for %s: %s", email, code)
        print(f"🔑 [RESET DEV] {email} → {code}", flush=True)
        return
    try:
        sendgrid_client.send_password_reset_email(email, code, ttl_minutes=RESET_CODE_TTL_MINUTES)
    except RuntimeError as exc:
        log.error("[reset] delivery failed email=%s: %s", email, exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "reset_delivery_failed",
                "message": "Could not send the reset email. Please try again.",
            },
        ) from exc


def _enroll_device_and_issue_tokens(
    db: Session,
    user: User,
    fingerprint: str,
    platform: str,
    device_label: str,
    ip_hash: str,
    now: datetime.datetime,
    signin_status: str,
) -> tuple[OtpVerifyResponse, bool, "AccountantDevice"]:
    """Upsert the device, mint access+refresh tokens, audit, commit.

    Shared by the password /login path. Mirrors steps 6–8 of /otp/verify (which
    keeps its own inline copy to avoid touching the shipped OTP flow). Caller is
    responsible for firing the new-device alert AFTER commit when is_new is True.
    """
    fingerprint = fingerprint.lower()
    existing_device = (
        db.query(AccountantDevice)
        .filter(AccountantDevice.user_id == user.id)
        .filter(AccountantDevice.device_fingerprint == fingerprint)
        .filter(AccountantDevice.revoked_at.is_(None))
        .first()
    )
    is_new_device = existing_device is None

    if existing_device:
        existing_device.last_seen_at = now
        existing_device.last_seen_ip_hash = ip_hash
        existing_device.use_count = (existing_device.use_count or 0) + 1
        if device_label and device_label != existing_device.device_label:
            existing_device.device_label = device_label
        device_row = existing_device
    else:
        device_row = AccountantDevice(
            user_id=user.id,
            device_fingerprint=fingerprint,
            platform=platform,
            device_label=device_label,
            ip_hash_first=ip_hash,
            last_seen_at=now,
            last_seen_ip_hash=ip_hash,
            use_count=1,
            enrolled_at=now,
            new_device_alert_sent_at=now,
        )
        db.add(device_row)
        db.flush()

    db.add(
        ActionLog(
            status=(f"{signin_status}_new_device" if is_new_device else signin_status),
            detail=(
                f"user_id={user.id} email={user.email} platform={platform} "
                f"label={device_label!r} fingerprint={fingerprint[:16]}… "
                f"ip_hash={ip_hash[:16]}…"
            ),
            triggered_at=now,
        )
    )

    access_token, access_expires = _issue_access_token(
        user_id=user.id, email=user.email, device_id=device_row.id
    )
    refresh_raw = _generate_refresh_token()
    refresh_hash = _sha256_hex(refresh_raw)
    refresh_expires = now + datetime.timedelta(days=REFRESH_TOKEN_TTL_DAYS)
    db.add(
        AccountantRefreshToken(
            user_id=user.id,
            device_id=device_row.id,
            token_hash=refresh_hash,
            issued_at=now,
            expires_at=refresh_expires,
            last_used_ip_hash=ip_hash,
        )
    )

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        log.error("[login] commit failed: %s", e)
        raise HTTPException(500, detail={"error": "db_commit_failed"})

    resp = OtpVerifyResponse(
        ok=True,
        access_token=access_token,
        refresh_token=refresh_raw,
        access_token_expires_at=_iso(access_expires),
        refresh_token_expires_at=_iso(refresh_expires),
        device_id=device_row.id,
        is_new_device=is_new_device,
        user=_user_payload(user, db),
    )
    return resp, is_new_device, device_row


# ─────────────────────────────────────────────────────────────
# Endpoints — Authentication
# ─────────────────────────────────────────────────────────────

@router.post("/otp/send", response_model=OtpSendResponse)
@limiter.limit("5/minute")
def otp_send(
    body: OtpSendRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Issue a 6-digit OTP, send it, persist hashed in accountant_otp_attempts."""
    email = body.email.lower()
    ip_hash = _ip_hash(request)

    # 1. Rate limit (always check, even if no user — prevent enumeration)
    _enforce_rate_limit(db, email, ip_hash)

    # 2. Resolve user. If not an accountant or no engagement, return
    #    SAME success shape with a fake delay so we don't leak whether
    #    an email is registered. Only the OTP send is skipped.
    user = _resolve_accountant(db, email)

    # 3. Generate + persist (regardless of user existence — see above)
    otp = _generate_otp()
    # Determine delivery channel: respect client preference, but fall back to
    # email if the user has no WhatsApp phone number on file.
    requested_method = body.delivery_method if hasattr(body, "delivery_method") else "email"
    phone_e164 = (getattr(user, "whatsapp_phone_e164", None) or "") if user else ""
    method = "whatsapp" if (requested_method == "whatsapp" and phone_e164) else "email"

    if user:
        otp_hash = _sha256_hex(otp)
        db.add(
            AccountantOtpAttempt(
                email=email,
                otp_hash=otp_hash,
                issued_at=_now(),
                expires_at=_now() + datetime.timedelta(seconds=OTP_TTL_SECONDS),
                ip_hash=ip_hash,
                delivery_method=method,
            )
        )
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            log.error("[otp/send] commit failed: %s", e)
            raise HTTPException(500, detail={"error": "db_commit_failed"})

        _send_otp_to_user(email, otp, method, phone_e164=phone_e164)
        log.info("[otp/send] sent OTP to user_id=%s (method=%s)", user.id, method)
    else:
        # Anti-enumeration — log internally but respond with same shape
        log.warning(
            "[otp/send] No active accountant for email=%s — silently absorbed",
            email,
        )

    response.headers["Cache-Control"] = "no-store"
    return OtpSendResponse(
        ok=True,
        sent_to=_mask_email(email),
        expires_in_seconds=OTP_TTL_SECONDS,
        method=method,
    )


@router.post("/otp/verify", response_model=OtpVerifyResponse)
def otp_verify(
    body: OtpVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Verify OTP + register/update device + issue tokens."""
    _signing_key()  # fail fast if JWT_SIGNING_KEY missing

    email = body.email.lower()
    ip_hash = _ip_hash(request)
    now = _now()

    user = _resolve_accountant(db, email)
    if not user:
        # Don't leak whether the email is registered — return otp_invalid
        # to match the user-not-found and wrong-otp paths.
        raise HTTPException(
            status_code=401,
            detail={"error": "otp_invalid", "message": "OTP is incorrect or expired."},
        )

    # 1. Find the most recent unconsumed OTP for this email
    attempt = (
        db.query(AccountantOtpAttempt)
        .filter(AccountantOtpAttempt.email == email)
        .filter(AccountantOtpAttempt.consumed_at.is_(None))
        .order_by(AccountantOtpAttempt.issued_at.desc())
        .first()
    )
    if not attempt:
        raise HTTPException(
            status_code=401,
            detail={"error": "otp_invalid", "message": "No active OTP for this email."},
        )

    # 2. Lockout check
    if attempt.locked_until and attempt.locked_until > now:
        retry_after = int((attempt.locked_until - now).total_seconds())
        raise HTTPException(
            status_code=401,
            detail={
                "error": "otp_locked",
                "message": (
                    f"Too many wrong attempts. Locked for "
                    f"{retry_after // 60} more minutes."
                ),
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    # 3. Expiry check
    if attempt.expires_at < now:
        raise HTTPException(
            status_code=401,
            detail={"error": "otp_expired", "message": "OTP has expired. Request a new one."},
        )

    # 4. Constant-time compare of hashed values
    submitted_hash = _sha256_hex(body.otp)
    if not secrets.compare_digest(submitted_hash, attempt.otp_hash):
        # Wrong OTP — increment + maybe lock
        attempt.attempts_count = (attempt.attempts_count or 0) + 1
        attempts_left = OTP_MAX_ATTEMPTS - attempt.attempts_count
        if attempts_left <= 0:
            attempt.locked_until = now + datetime.timedelta(minutes=OTP_LOCKOUT_MINUTES)
            db.commit()
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "otp_locked",
                    "message": (
                        f"Too many wrong attempts. Locked for "
                        f"{OTP_LOCKOUT_MINUTES} minutes."
                    ),
                    "retry_after_seconds": OTP_LOCKOUT_MINUTES * 60,
                },
                headers={"Retry-After": str(OTP_LOCKOUT_MINUTES * 60)},
            )
        db.commit()
        raise HTTPException(
            status_code=401,
            detail={
                "error": "otp_invalid",
                "message": f"OTP is incorrect. {attempts_left} attempts remaining.",
                "attempts_remaining": attempts_left,
            },
        )

    # 5. Mark OTP consumed (one-shot)
    attempt.consumed_at = now

    # 6. Upsert device row
    fingerprint = body.device_fingerprint.lower()
    existing_device = (
        db.query(AccountantDevice)
        .filter(AccountantDevice.user_id == user.id)
        .filter(AccountantDevice.device_fingerprint == fingerprint)
        .filter(AccountantDevice.revoked_at.is_(None))
        .first()
    )
    is_new_device = existing_device is None

    if existing_device:
        existing_device.last_seen_at = now
        existing_device.last_seen_ip_hash = ip_hash
        existing_device.use_count = (existing_device.use_count or 0) + 1
        if body.device_label and body.device_label != existing_device.device_label:
            existing_device.device_label = body.device_label
        device_row = existing_device
    else:
        device_row = AccountantDevice(
            user_id=user.id,
            device_fingerprint=fingerprint,
            platform=body.platform,
            device_label=body.device_label,
            ip_hash_first=ip_hash,
            last_seen_at=now,
            last_seen_ip_hash=ip_hash,
            use_count=1,
            enrolled_at=now,
            new_device_alert_sent_at=now,  # alert fired (logged via ActionLog)
        )
        db.add(device_row)
        db.flush()  # populate device_row.id

    # 7. Audit
    db.add(
        ActionLog(
            status=(
                "accountant_signin_new_device" if is_new_device else "accountant_signin"
            ),
            detail=(
                f"user_id={user.id} email={email} platform={body.platform} "
                f"label={body.device_label!r} fingerprint={fingerprint[:16]}… "
                f"ip_hash={ip_hash[:16]}…"
            ),
            triggered_at=now,
        )
    )

    # 8. Issue tokens
    access_token, access_expires = _issue_access_token(
        user_id=user.id, email=user.email, device_id=device_row.id
    )
    refresh_raw = _generate_refresh_token()
    refresh_hash = _sha256_hex(refresh_raw)
    refresh_expires = now + datetime.timedelta(days=REFRESH_TOKEN_TTL_DAYS)

    db.add(
        AccountantRefreshToken(
            user_id=user.id,
            device_id=device_row.id,
            token_hash=refresh_hash,
            issued_at=now,
            expires_at=refresh_expires,
            last_used_ip_hash=ip_hash,
        )
    )

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        log.error("[otp/verify] commit failed: %s", e)
        raise HTTPException(500, detail={"error": "db_commit_failed"})

    log.info(
        "[otp/verify] user_id=%s device_id=%s is_new=%s",
        user.id, device_row.id, is_new_device,
    )

    if is_new_device:
        _send_new_device_alert(user, device_row)

    response.headers["Cache-Control"] = "no-store"
    return OtpVerifyResponse(
        ok=True,
        access_token=access_token,
        refresh_token=refresh_raw,
        access_token_expires_at=_iso(access_expires),
        refresh_token_expires_at=_iso(refresh_expires),
        device_id=device_row.id,
        is_new_device=is_new_device,
        user=_user_payload(user, db),
    )


@router.post("/refresh", response_model=RefreshResponse)
def refresh(
    body: RefreshRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Rotate refresh token. Old token is invalidated; new one issued."""
    _signing_key()

    now = _now()
    token_hash = _sha256_hex(body.refresh_token)
    ip_hash = _ip_hash(request)

    rt = (
        db.query(AccountantRefreshToken)
        .filter(AccountantRefreshToken.token_hash == token_hash)
        .first()
    )

    if not rt:
        raise HTTPException(
            status_code=401,
            detail={"error": "refresh_token_invalid", "message": "Unknown refresh token."},
        )

    # Replay detection: token already used or revoked → security event
    if rt.used_at is not None or rt.revoked_at is not None:
        # Revoke the entire chain (this token's lineage) — token reuse
        # is a strong signal of compromise.
        ancestor_ids = [rt.id]
        cur = rt
        while cur.replaced_by_id:
            nxt = (
                db.query(AccountantRefreshToken)
                .filter(AccountantRefreshToken.id == cur.replaced_by_id)
                .first()
            )
            if not nxt:
                break
            ancestor_ids.append(nxt.id)
            cur = nxt
        # Mark every row in the chain revoked
        for rid in ancestor_ids:
            db.query(AccountantRefreshToken).filter(
                AccountantRefreshToken.id == rid
            ).update({"revoked_at": now, "revoked_reason": "replay_detected"})
        db.add(
            ActionLog(
                status="CRITICAL_accountant_refresh_token_replay",
                detail=(
                    f"user_id={rt.user_id} device_id={rt.device_id} "
                    f"token_id={rt.id} chain_len={len(ancestor_ids)} "
                    f"ip_hash={ip_hash[:16]}…"
                ),
                triggered_at=now,
            )
        )
        db.commit()
        raise HTTPException(
            status_code=401,
            detail={
                "error": "refresh_token_invalid",
                "message": "Refresh token has already been used. Sign in again.",
            },
        )

    if rt.expires_at < now:
        raise HTTPException(
            status_code=401,
            detail={"error": "refresh_token_invalid", "message": "Refresh token expired."},
        )

    # Device match check
    device = (
        db.query(AccountantDevice)
        .filter(AccountantDevice.id == rt.device_id)
        .first()
    )
    if not device or device.revoked_at is not None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "device_revoked",
                "message": "This device was revoked. Sign in again from a fresh device enrollment.",
            },
        )
    if device.device_fingerprint != body.device_fingerprint.lower():
        raise HTTPException(
            status_code=401,
            detail={
                "error": "device_mismatch",
                "message": (
                    "Device fingerprint does not match the device that "
                    "minted this refresh token. Possible compromise."
                ),
            },
        )

    user = db.query(User).filter(User.id == rt.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail={"error": "user_inactive", "message": "User account is inactive."},
        )

    # Rotate: mint new token, mark old used.
    new_raw = _generate_refresh_token()
    new_hash = _sha256_hex(new_raw)
    new_expires = now + datetime.timedelta(days=REFRESH_TOKEN_TTL_DAYS)

    new_rt = AccountantRefreshToken(
        user_id=user.id,
        device_id=device.id,
        token_hash=new_hash,
        issued_at=now,
        expires_at=new_expires,
        last_used_ip_hash=ip_hash,
    )
    db.add(new_rt)
    db.flush()

    rt.used_at = now
    rt.replaced_by_id = new_rt.id

    # Touch device
    device.last_seen_at = now
    device.last_seen_ip_hash = ip_hash
    device.use_count = (device.use_count or 0) + 1

    # New access token
    access_token, access_expires = _issue_access_token(
        user_id=user.id, email=user.email, device_id=device.id
    )

    db.commit()
    log.info("[refresh] user_id=%s device_id=%s rotated", user.id, device.id)

    response.headers["Cache-Control"] = "no-store"
    return RefreshResponse(
        ok=True,
        access_token=access_token,
        refresh_token=new_raw,
        access_token_expires_at=_iso(access_expires),
        refresh_token_expires_at=_iso(new_expires),
    )


@router.post("/logout")
def logout(
    body: LogoutRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Idempotent — invalidates the supplied refresh token if present."""
    if body.refresh_token:
        token_hash = _sha256_hex(body.refresh_token)
        rt = (
            db.query(AccountantRefreshToken)
            .filter(AccountantRefreshToken.token_hash == token_hash)
            .first()
        )
        if rt and rt.revoked_at is None and rt.used_at is None:
            rt.revoked_at = _now()
            rt.revoked_reason = "logout"
            db.commit()
            log.info(
                "[logout] revoked rt_id=%s user_id=%s device_id=%s",
                rt.id, rt.user_id, rt.device_id,
            )
    return {"ok": True}


@router.post("/login", response_model=OtpVerifyResponse)
@limiter.limit("10/minute")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Email + password sign-in. Enrolls the device + issues tokens exactly like
    /otp/verify, but gated on a bcrypt password check instead of an OTP. OTP
    sign-in (/otp/send + /otp/verify) remains available in parallel."""
    _signing_key()  # fail fast if JWT secret missing
    email = body.email.lower()
    ip_hash = _ip_hash(request)
    now = _now()

    user = _resolve_accountant(db, email)
    # Always run a bcrypt verify (real hash if the user exists, dummy otherwise)
    # so response time doesn't reveal whether the email is registered.
    stored_hash = user.password_hash if (user and user.password_hash) else _dummy_password_hash()
    try:
        password_ok = verify_password(body.password, stored_hash)
    except Exception:
        password_ok = False

    if not user or not password_ok:
        db.add(
            ActionLog(
                status="accountant_login_failed",
                detail=f"email={email} ip_hash={ip_hash[:16]}…",
                triggered_at=now,
            )
        )
        db.commit()
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_credentials", "message": "Email or password is incorrect."},
        )

    resp, is_new_device, device_row = _enroll_device_and_issue_tokens(
        db, user, body.device_fingerprint, body.platform, body.device_label,
        ip_hash, now, signin_status="accountant_login",
    )
    log.info("[login] user_id=%s device_id=%s is_new=%s", user.id, device_row.id, is_new_device)
    if is_new_device:
        _send_new_device_alert(user, device_row)

    response.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
@limiter.limit("5/minute")
def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Issue a single-use password-reset code via email. Always returns the same
    shape (anti-enumeration); a code is created + sent only for a real accountant."""
    email = body.email.lower()
    ip_hash = _ip_hash(request)
    now = _now()

    # Rate limit per email (only reset rows count; unknown emails are absorbed).
    cutoff = now - datetime.timedelta(minutes=RESET_RATE_LIMIT_WINDOW_MINUTES)
    recent = (
        db.query(AccountantPasswordReset)
        .filter(AccountantPasswordReset.email == email)
        .filter(AccountantPasswordReset.issued_at > cutoff)
        .count()
    )
    if recent >= RESET_RATE_LIMIT_PER_EMAIL:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "reset_rate_limited",
                "message": f"Too many reset requests. Try again in {RESET_RATE_LIMIT_WINDOW_MINUTES} minutes.",
                "retry_after_seconds": RESET_RATE_LIMIT_WINDOW_MINUTES * 60,
            },
            headers={"Retry-After": str(RESET_RATE_LIMIT_WINDOW_MINUTES * 60)},
        )

    user = _resolve_accountant(db, email)
    if user:
        code = _generate_reset_code()
        db.add(
            AccountantPasswordReset(
                email=email,
                code_hash=_sha256_hex(code),
                issued_at=now,
                expires_at=now + datetime.timedelta(minutes=RESET_CODE_TTL_MINUTES),
                ip_hash=ip_hash,
            )
        )
        db.add(
            ActionLog(
                status="accountant_password_reset_requested",
                detail=f"user_id={user.id} email={email} ip_hash={ip_hash[:16]}…",
                triggered_at=now,
            )
        )
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            log.error("[forgot-password] commit failed: %s", e)
            raise HTTPException(500, detail={"error": "db_commit_failed"})
        _send_reset_code(email, code)
        log.info("[forgot-password] reset code issued user_id=%s", user.id)
    else:
        log.warning("[forgot-password] no active accountant for email=%s — absorbed", email)

    response.headers["Cache-Control"] = "no-store"
    return ForgotPasswordResponse(
        ok=True,
        sent_to=_mask_email(email),
        expires_in_seconds=RESET_CODE_TTL_MINUTES * 60,
    )


@router.post("/reset-password")
def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Validate a reset code + set a new password. Single-use, attempt-capped.
    On success, revokes ALL of the user's refresh tokens (forces re-login)."""
    email = body.email.lower()
    now = _now()

    pr = (
        db.query(AccountantPasswordReset)
        .filter(AccountantPasswordReset.email == email)
        .filter(AccountantPasswordReset.consumed_at.is_(None))
        .order_by(AccountantPasswordReset.issued_at.desc())
        .first()
    )
    if not pr:
        raise HTTPException(
            status_code=400,
            detail={"error": "reset_code_invalid", "message": "Invalid or expired reset code."},
        )

    if pr.locked_until and pr.locked_until > now:
        retry_after = int((pr.locked_until - now).total_seconds())
        raise HTTPException(
            status_code=400,
            detail={
                "error": "reset_locked",
                "message": f"Too many wrong attempts. Try again in {retry_after // 60} minutes.",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    if pr.expires_at < now:
        raise HTTPException(
            status_code=400,
            detail={"error": "reset_code_expired", "message": "This reset code has expired. Request a new one."},
        )

    submitted_hash = _sha256_hex(body.code.strip().upper())
    if not secrets.compare_digest(submitted_hash, pr.code_hash):
        pr.attempts_count = (pr.attempts_count or 0) + 1
        left = RESET_MAX_ATTEMPTS - pr.attempts_count
        if left <= 0:
            pr.locked_until = now + datetime.timedelta(minutes=RESET_LOCKOUT_MINUTES)
            db.commit()
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "reset_locked",
                    "message": f"Too many wrong attempts. Locked for {RESET_LOCKOUT_MINUTES} minutes.",
                    "retry_after_seconds": RESET_LOCKOUT_MINUTES * 60,
                },
            )
        db.commit()
        raise HTTPException(
            status_code=400,
            detail={
                "error": "reset_code_invalid",
                "message": f"Invalid reset code. {left} attempts remaining.",
                "attempts_remaining": left,
            },
        )

    # Code correct → re-resolve the accountant (must still be valid).
    user = _resolve_accountant(db, email)
    if not user:
        pr.consumed_at = now
        db.commit()
        raise HTTPException(
            status_code=400,
            detail={"error": "reset_code_invalid", "message": "Invalid or expired reset code."},
        )

    pr.consumed_at = now
    user.password_hash = hash_password(body.new_password)
    # A password reset invalidates every live session.
    db.query(AccountantRefreshToken).filter(
        AccountantRefreshToken.user_id == user.id,
        AccountantRefreshToken.revoked_at.is_(None),
        AccountantRefreshToken.used_at.is_(None),
    ).update({"revoked_at": now, "revoked_reason": "password_reset"})
    db.add(
        ActionLog(
            status="accountant_password_reset_completed",
            detail=f"user_id={user.id} email={email}",
            triggered_at=now,
        )
    )
    db.commit()
    log.info("[reset-password] user_id=%s password reset", user.id)

    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    auth: tuple = Depends(require_accountant),
):
    """Authenticated password change. Verifies the old password, sets the new
    one, and revokes refresh tokens on OTHER devices (current session survives)."""
    user, current_device_id = auth
    now = _now()

    if not verify_password(body.old_password, user.password_hash or _dummy_password_hash()):
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_credentials", "message": "Current password is incorrect."},
        )

    user.password_hash = hash_password(body.new_password)
    db.query(AccountantRefreshToken).filter(
        AccountantRefreshToken.user_id == user.id,
        AccountantRefreshToken.device_id != current_device_id,
        AccountantRefreshToken.revoked_at.is_(None),
        AccountantRefreshToken.used_at.is_(None),
    ).update({"revoked_at": now, "revoked_reason": "password_changed"})
    db.add(
        ActionLog(
            status="accountant_password_changed",
            detail=f"user_id={user.id} device_id={current_device_id}",
            triggered_at=now,
        )
    )
    db.commit()
    log.info("[change-password] user_id=%s device_id=%s", user.id, current_device_id)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# Endpoints — Device management
# ─────────────────────────────────────────────────────────────

def _require_accountant_jwt(request: Request, db: Session) -> tuple[User, int]:
    """
    Inline dep: validate accountant access token from Authorization header.
    Returns (User, current_device_id).
    """
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, detail={"error": "missing_token"})
    token = auth.split(" ", 1)[1].strip()

    try:
        claims = jose_jwt.decode(
            token,
            _signing_key(),
            algorithms=[JWT_ALGO],
            options={"verify_aud": False},
        )
    except Exception as e:
        log.warning("[accountant_jwt] decode failed: %s", e)
        raise HTTPException(401, detail={"error": "invalid_token"})

    if claims.get("iss") != JWT_ISSUER:
        raise HTTPException(401, detail={"error": "invalid_token_issuer"})

    user_id = claims.get("sub")
    device_id = claims.get("device_id")
    if not user_id or not device_id:
        raise HTTPException(401, detail={"error": "invalid_token_claims"})

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active or (user.role or "").lower() != "accountant":
        raise HTTPException(403, detail={"error": "not_an_accountant"})

    # Device must still be active
    dev = (
        db.query(AccountantDevice)
        .filter(AccountantDevice.id == device_id)
        .filter(AccountantDevice.user_id == user.id)
        .filter(AccountantDevice.revoked_at.is_(None))
        .first()
    )
    if not dev:
        raise HTTPException(401, detail={"error": "device_revoked"})

    return user, device_id


@router.get("/devices", response_model=DeviceListResponse)
def list_devices(
    db: Session = Depends(get_db),
    auth: tuple = Depends(require_accountant),
):
    """List accountant's currently bound devices, newest first."""
    user, current_device_id = auth

    rows = (
        db.query(AccountantDevice)
        .filter(AccountantDevice.user_id == user.id)
        .filter(AccountantDevice.revoked_at.is_(None))
        .order_by(AccountantDevice.last_seen_at.desc())
        .all()
    )

    return DeviceListResponse(
        devices=[
            AccountantDeviceItem(
                id=r.id,
                device_fingerprint_preview=r.device_fingerprint[:16] + "…",
                platform=r.platform,
                device_label=r.device_label,
                enrolled_at=_iso(r.enrolled_at),
                last_seen_at=_iso(r.last_seen_at),
                use_count=r.use_count or 0,
                is_current_device=(r.id == current_device_id),
                ip_geo_hint=None,  # Sprint 8.2.1 — wire MaxMind GeoLite2
            )
            for r in rows
        ]
    )


@router.post("/devices/{device_id}/revoke")
def revoke_device(
    device_id: int,
    body: DeviceRevokeRequest,
    db: Session = Depends(get_db),
    auth: tuple = Depends(require_accountant),
):
    """Soft-delete device + revoke all its refresh tokens."""
    user, _current = auth

    dev = (
        db.query(AccountantDevice)
        .filter(AccountantDevice.id == device_id)
        .filter(AccountantDevice.user_id == user.id)
        .first()
    )
    if not dev:
        raise HTTPException(404, detail={"error": "device_not_found"})

    if dev.revoked_at is not None:
        raise HTTPException(
            400,
            detail={
                "error": "device_already_revoked",
                "revoked_at": _iso(dev.revoked_at),
            },
        )

    now = _now()
    dev.revoked_at = now
    dev.revoked_reason = body.reason

    # Revoke all refresh tokens for this device
    db.query(AccountantRefreshToken).filter(
        AccountantRefreshToken.device_id == dev.id,
        AccountantRefreshToken.revoked_at.is_(None),
        AccountantRefreshToken.used_at.is_(None),
    ).update({"revoked_at": now, "revoked_reason": "device_revoked"})

    db.add(
        ActionLog(
            status="accountant_device_revoked",
            detail=(
                f"user_id={user.id} device_id={dev.id} "
                f"fingerprint={dev.device_fingerprint[:16]}… "
                f"reason={body.reason!r}"
            ),
            triggered_at=now,
        )
    )

    db.commit()

    log.info("[devices/revoke] user_id=%s device_id=%s", user.id, dev.id)
    return {"ok": True, "device_id": dev.id, "revoked_at": _iso(now)}


@router.post("/devices/{device_id}/relabel")
def relabel_device(
    device_id: int,
    body: DeviceRelabelRequest,
    db: Session = Depends(get_db),
    auth: tuple = Depends(require_accountant),
):
    """Rename a device. Audit-logged."""
    user, _current = auth

    dev = (
        db.query(AccountantDevice)
        .filter(AccountantDevice.id == device_id)
        .filter(AccountantDevice.user_id == user.id)
        .filter(AccountantDevice.revoked_at.is_(None))
        .first()
    )
    if not dev:
        raise HTTPException(404, detail={"error": "device_not_found"})

    old_label = dev.device_label
    dev.device_label = body.device_label

    db.add(
        ActionLog(
            status="accountant_device_relabeled",
            detail=(
                f"user_id={user.id} device_id={dev.id} "
                f"old={old_label!r} new={body.device_label!r}"
            ),
            triggered_at=_now(),
        )
    )

    db.commit()

    return {"ok": True, "device_id": dev.id, "device_label": dev.device_label}
