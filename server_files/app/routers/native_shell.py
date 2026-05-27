"""
native_shell.py — Aurora Mac Shell backend (Sprint 8.2, Appendix M/N successor).

Cryptographically pairs the founder's MacBook (via its Secure Enclave key)
to the Aurora backend. After a successful handshake, the shell receives a
short-lived `native_session_token` JWT that subsequent requests carry as
the `X-Aurora-Native-Session` header. Sensitive endpoints can layer on the
`require_native_shell(action)` dep (see app/middleware/auth_middleware.py)
to gate themselves to handshake-verified Mac shell traffic only.

Four endpoints — all require_admin-gated (IAP + OIDC + role):

  POST /api/v1/admin/exec/native/handshake/start
       Issues a single-shot 60-second challenge (32 random bytes).

  POST /api/v1/admin/exec/native/handshake/finish
       Verifies an ECDSA P-256 signature over the challenge, registers
       the device, mints a 15-minute native_session_token JWT.

  GET  /api/v1/admin/exec/native/devices
       Lists active bound devices for the calling user.

  POST /api/v1/admin/exec/native/devices/{device_pk}/revoke
       Revokes a device. Requires WebAuthn step-up
       (`action="native_device_revoke"`).

Security invariants enforced here:
  • Challenge is single-use (consumed_at non-null on second use)
  • Challenge expires after 60 seconds (configurable via const)
  • SHA-256(public_key_b64) MUST equal claimed device_id (commitment)
  • ECDSA P-256 signature MUST verify against the provided public key
  • Same device_id cannot be re-registered while active (unique index)
  • Revocation requires WebAuthn step-up + writes a CRITICAL ActionLog
  • Cross-user attack: challenges are scoped to user_id; user A cannot
    consume user B's challenge even if they steal the challenge_id
  • The session JWT carries `iss="aurora-native-session"` — distinct
    from regular Aurora JWTs, so a stolen one cannot be used for
    general admin access (it only satisfies require_native_shell)
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import logging
import os
import secrets
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.exceptions import InvalidSignature

from jose import jwt as jose_jwt

from app.database import get_db
from app.database.models import (
    User,
    NativeDeviceKey,
    NativeHandshakeChallenge,
    ActionLog,
)
from app.middleware.auth_middleware import (
    require_admin,
    _resolve_native_session,
)
from app.services.webauthn_service import require_step_up

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/exec/native",
    tags=["native_shell"],
)


# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

# 60-second TTL on every challenge — long enough for a slow round-trip
# from the Mac, short enough that a stolen challenge can't be replayed
# hours later from a different machine.
CHALLENGE_TTL_SECONDS = 60

# 15-minute session JWT TTL — matches what the shell's
# NativeHandshake.swift expects to re-handshake every 14 minutes.
SESSION_JWT_TTL_SECONDS = 900

# The distinct `iss` claim makes session JWTs unforgeable as regular
# Aurora JWTs even if both share JWT_SIGNING_KEY — the middleware
# checks issuer before trusting the claims.
JWT_ISSUER = "aurora-native-session"

# X.963-uncompressed P-256 public key is always 65 bytes: 0x04 prefix
# + 32 bytes X + 32 bytes Y. Anything else is malformed.
P256_PUBKEY_BYTES = 65


# ─────────────────────────────────────────────────────────────
# Pydantic DTOs
# ─────────────────────────────────────────────────────────────

class HandshakeStartRequest(BaseModel):
    device_id: str = Field(..., min_length=64, max_length=64)

    @field_validator("device_id")
    @classmethod
    def hex_only(cls, v: str) -> str:
        v_low = v.lower()
        if not all(c in "0123456789abcdef" for c in v_low):
            raise ValueError("device_id must be 64-char lowercase hex")
        return v_low


class HandshakeStartResponse(BaseModel):
    challenge_id: str
    challenge_b64: str
    expires_at_iso: str


class HandshakeFinishRequest(BaseModel):
    challenge_id: str = Field(..., min_length=8, max_length=64)
    device_id: str = Field(..., min_length=64, max_length=64)
    public_key_b64: str = Field(..., min_length=10, max_length=4096)
    signature_b64: str = Field(..., min_length=10, max_length=4096)
    device_label: Optional[str] = Field(default=None, max_length=120)

    @field_validator("device_id")
    @classmethod
    def hex_only(cls, v: str) -> str:
        v_low = v.lower()
        if not all(c in "0123456789abcdef" for c in v_low):
            raise ValueError("device_id must be 64-char lowercase hex")
        return v_low


class HandshakeFinishResponse(BaseModel):
    ok: bool
    native_session_token: str
    expires_at_iso: str
    device_id: str
    registered_at_iso: str


class NativeDeviceItem(BaseModel):
    id: int
    device_id: str
    device_label: Optional[str] = None
    enrolled_at: str
    last_used_at: Optional[str] = None
    use_count: int
    is_current_session: bool


class NativeDevicesListResponse(BaseModel):
    devices: list[NativeDeviceItem]


class RevokeDeviceRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=400)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _iso(dt: datetime.datetime) -> str:
    """ISO-8601 string with trailing Z (UTC), microseconds dropped."""
    return dt.replace(microsecond=0).isoformat() + "Z"


def _decode_b64_strict(value: str, field_name: str) -> bytes:
    """base64-decode with clear error on malformed input."""
    try:
        return base64.b64decode(value, validate=True)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"invalid_{field_name}_encoding",
                "message": f"{field_name} is not valid base64",
            },
        )


def _verify_commitment(public_key_b64: str, claimed_device_id: str) -> None:
    """SHA-256(public_key X.963 bytes) hex must equal device_id."""
    pub_bytes = _decode_b64_strict(public_key_b64, "public_key_b64")
    if len(pub_bytes) != P256_PUBKEY_BYTES or pub_bytes[0] != 0x04:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_public_key_format",
                "message": (
                    f"Expected {P256_PUBKEY_BYTES}-byte X.963 uncompressed "
                    f"P-256 point starting with 0x04; got {len(pub_bytes)} bytes"
                ),
            },
        )
    digest = hashlib.sha256(pub_bytes).hexdigest()
    if digest != claimed_device_id.lower():
        raise HTTPException(
            status_code=400,
            detail={
                "error": "device_id_mismatch",
                "message": (
                    "device_id does not match SHA-256 of the public key. "
                    "This commitment check protects against a client lying "
                    "about which key it possesses."
                ),
            },
        )


def _verify_ecdsa(public_key_b64: str, challenge_bytes: bytes, signature_b64: str) -> None:
    """
    Verify ECDSA-SHA256 over the challenge using an X.963-encoded P-256 public key.
    Signature is the standard DER-encoded (r, s) sequence produced by Apple's
    `SecKeyCreateSignature(.ecdsaSignatureMessageX962SHA256)`.
    """
    pub_bytes = _decode_b64_strict(public_key_b64, "public_key_b64")
    sig_bytes = _decode_b64_strict(signature_b64, "signature_b64")

    try:
        public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), pub_bytes
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_public_key",
                "message": f"could not parse public key: {str(e)[:200]}",
            },
        )

    try:
        public_key.verify(sig_bytes, challenge_bytes, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        # The specific 401 (not 400) is intentional: distinguishes
        # "your signature is bad" from "your request was malformed".
        raise HTTPException(
            status_code=401,
            detail={
                "error": "signature_invalid",
                "message": (
                    "ECDSA signature did not verify against the provided "
                    "public key. Either the client doesn't possess the "
                    "corresponding private key, or the challenge bytes "
                    "were tampered with in transit."
                ),
            },
        )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "signature_verification_error",
                "message": str(e)[:200],
            },
        )


def _pem_encode(public_key_b64: str) -> str:
    """X.963 base64 → SubjectPublicKeyInfo PEM (for human-readable storage)."""
    pub_bytes = _decode_b64_strict(public_key_b64, "public_key_b64")
    public_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), pub_bytes
    )
    pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return pem.decode("ascii")


def _signing_key() -> str:
    # Cloud Run env uses JWT_SECRET; legacy code path used JWT_SIGNING_KEY.
    # Accept either for compatibility.
    key = (os.getenv("JWT_SECRET") or os.getenv("JWT_SIGNING_KEY") or "").strip()
    if not key:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "jwt_signing_key_missing",
                "message": (
                    "JWT_SECRET env var is not configured. "
                    "Native handshake cannot mint session tokens. "
                    "Check Cloud Run service config + Secret Manager."
                ),
            },
        )
    return key


def _resolve_native_device_id(request: Request) -> Optional[str]:
    """Best-effort read of `request.state.native_device_id` (set by middleware).

    Returns None if the middleware didn't attach one (e.g., the request
    came via the PWA without a native_session_token). Used by the
    devices list to flag the row representing the CURRENT session.
    """
    return getattr(request.state, "native_device_id", None)


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────

@router.post("/handshake/start", response_model=HandshakeStartResponse)
def handshake_start(
    body: HandshakeStartRequest,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HandshakeStartResponse:
    """
    Issue a single-shot 60-second challenge for the Mac shell to sign.

    The challenge is bound to `(current_user.id, device_id_hint)` so that
    User A's challenge cannot be consumed by User B (cross-user attack).
    The `device_id_hint` is what the client CLAIMS — not trusted until
    the finish step proves it via the commitment + signature check.
    """
    _ = _signing_key()  # fail-fast if signing key unconfigured

    challenge_bytes = secrets.token_bytes(32)
    challenge_id = str(uuid.uuid4())
    now = _now()
    expires = now + datetime.timedelta(seconds=CHALLENGE_TTL_SECONDS)

    db.add(
        NativeHandshakeChallenge(
            challenge_id=challenge_id,
            user_id=current_user.id,
            device_id_hint=body.device_id,
            challenge_bytes=challenge_bytes,
            issued_at=now,
            expires_at=expires,
        )
    )
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        log.error("[native/handshake/start] DB commit failed: %s", e)
        raise HTTPException(500, detail={"error": "db_commit_failed"})

    log.info(
        "[native/handshake/start] user_id=%s device_hint=%s… challenge_id=%s",
        current_user.id,
        body.device_id[:16],
        challenge_id,
    )

    return HandshakeStartResponse(
        challenge_id=challenge_id,
        challenge_b64=base64.b64encode(challenge_bytes).decode("ascii"),
        expires_at_iso=_iso(expires),
    )


@router.post("/handshake/finish", response_model=HandshakeFinishResponse)
def handshake_finish(
    body: HandshakeFinishRequest,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> HandshakeFinishResponse:
    """
    Verify the signed challenge, register the device, mint a session JWT.

    Verification order (every step is a hard gate):
      1. Challenge exists and belongs to current_user
      2. Challenge not consumed (one-shot)
      3. Challenge not expired (60s TTL)
      4. SHA-256(public_key_b64) == device_id (commitment)
      5. ECDSA P-256 signature verifies against (public_key, challenge_bytes)

    Only after ALL FIVE pass do we mark the challenge consumed, UPSERT the
    NativeDeviceKey row, and mint the session JWT.
    """
    signing_key = _signing_key()

    # 1-3 — challenge state
    challenge = (
        db.query(NativeHandshakeChallenge)
        .filter(
            NativeHandshakeChallenge.challenge_id == body.challenge_id,
            NativeHandshakeChallenge.user_id == current_user.id,
        )
        .first()
    )
    if not challenge:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "challenge_not_found",
                "message": (
                    "No challenge with that id for this user. Was it consumed, "
                    "expired+swept, or issued for a different user?"
                ),
            },
        )

    if challenge.consumed_at is not None:
        log.warning(
            "[native/handshake/finish] replay attempt — user_id=%s challenge_id=%s consumed_at=%s",
            current_user.id, body.challenge_id, challenge.consumed_at,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "challenge_already_consumed",
                "message": "Each challenge is single-use. Request a fresh one.",
            },
        )

    if challenge.expires_at < _now():
        raise HTTPException(
            status_code=400,
            detail={
                "error": "challenge_expired",
                "message": (
                    f"Challenge TTL is {CHALLENGE_TTL_SECONDS}s. "
                    "Request a fresh /handshake/start."
                ),
            },
        )

    # Sanity — the device_id hint at start MUST match the device_id at
    # finish. (Otherwise a client could start a challenge claiming
    # device A and finish it claiming device B.)
    if challenge.device_id_hint and challenge.device_id_hint.lower() != body.device_id.lower():
        raise HTTPException(
            status_code=400,
            detail={
                "error": "device_id_changed_mid_handshake",
                "message": (
                    "device_id at /finish does not match the hint provided "
                    "at /start. Request a fresh challenge."
                ),
            },
        )

    # 4 — commitment
    _verify_commitment(body.public_key_b64, body.device_id)

    # 5 — signature
    _verify_ecdsa(body.public_key_b64, bytes(challenge.challenge_bytes), body.signature_b64)

    # All gates passed → mark challenge consumed
    challenge.consumed_at = _now()

    # UPSERT device row
    existing = (
        db.query(NativeDeviceKey)
        .filter(
            NativeDeviceKey.device_id == body.device_id,
            NativeDeviceKey.user_id == current_user.id,
            NativeDeviceKey.revoked_at.is_(None),
        )
        .first()
    )
    if existing:
        # Re-handshake of an already-registered device — touch metadata,
        # optionally update label if newly supplied.
        existing.last_used_at = _now()
        existing.use_count = (existing.use_count or 0) + 1
        if body.device_label and not existing.device_label:
            existing.device_label = body.device_label
        registered_at = existing.enrolled_at
        device_row_id = existing.id
        was_new = False
    else:
        # New device — PEM-encode the public key for storage clarity.
        pem = _pem_encode(body.public_key_b64)
        new_row = NativeDeviceKey(
            user_id=current_user.id,
            device_id=body.device_id,
            public_key_pem=pem,
            public_key_b64=body.public_key_b64,
            device_label=body.device_label,
            enrolled_at=_now(),
            last_used_at=_now(),
            use_count=1,
        )
        db.add(new_row)
        db.flush()  # populate new_row.id
        registered_at = new_row.enrolled_at
        device_row_id = new_row.id
        was_new = True

    # Audit log — handshake completion is ALWAYS audit-worthy
    db.add(
        ActionLog(
            status="native_device_handshake_completed" if not was_new else "native_device_enrolled",
            detail=(
                f"user_id={current_user.id} "
                f"device_pk={device_row_id} "
                f"device_id={body.device_id[:16]}… "
                f"label={body.device_label!r} "
                f"was_new={was_new}"
            ),
            triggered_at=_now(),
        )
    )

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        log.error("[native/handshake/finish] DB commit failed: %s", e)
        raise HTTPException(500, detail={"error": "db_commit_failed"})

    # Mint session JWT — HS256 with the same JWT_SIGNING_KEY as regular
    # Aurora JWTs, but a distinct `iss` so the middleware can't confuse
    # the two.
    now = _now()
    expires = now + datetime.timedelta(seconds=SESSION_JWT_TTL_SECONDS)
    payload = {
        "iss": JWT_ISSUER,
        # RFC 7519: `sub` MUST be a StringOrURI. python-jose's
        # jwt.decode raises JWTClaimsError on a non-string sub, so
        # casting to str here keeps native-session tokens compatible
        # with the same decode path used for standard Aurora HS256
        # tokens (see app/services/auth_service.py:create_access_token).
        "sub": str(current_user.id),
        "device_id": body.device_id,
        "device_pk": device_row_id,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    token = jose_jwt.encode(payload, signing_key, algorithm="HS256")

    log.info(
        "[native/handshake/finish] user_id=%s device_id=%s… session_expires=%s was_new=%s",
        current_user.id,
        body.device_id[:16],
        _iso(expires),
        was_new,
    )

    return HandshakeFinishResponse(
        ok=True,
        native_session_token=token,
        expires_at_iso=_iso(expires),
        device_id=body.device_id,
        registered_at_iso=_iso(registered_at),
    )


@router.get("/devices", response_model=NativeDevicesListResponse)
def list_devices(
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> NativeDevicesListResponse:
    """
    List active bound devices for the current user, newest first.
    Tags the row whose `device_id` matches the current `X-Aurora-Native-Session`
    so the cockpit UI can highlight "this is your current session."
    """
    # Best-effort resolve of the caller's native session — sets
    # request.state.native_device_id if a valid X-Aurora-Native-Session
    # header is present. Failure is silent (the list still returns,
    # just with no rows tagged is_current_session=true).
    _resolve_native_session(request, db)

    rows = (
        db.query(NativeDeviceKey)
        .filter(
            NativeDeviceKey.user_id == current_user.id,
            NativeDeviceKey.revoked_at.is_(None),
        )
        .order_by(NativeDeviceKey.enrolled_at.desc())
        .all()
    )

    current_device = _resolve_native_device_id(request)

    return NativeDevicesListResponse(
        devices=[
            NativeDeviceItem(
                id=r.id,
                device_id=r.device_id,
                device_label=r.device_label,
                enrolled_at=_iso(r.enrolled_at),
                last_used_at=_iso(r.last_used_at) if r.last_used_at else None,
                use_count=r.use_count or 0,
                is_current_session=(current_device is not None and r.device_id == current_device),
            )
            for r in rows
        ]
    )


@router.post("/devices/{device_pk}/revoke")
def revoke_device(
    device_pk: int,
    body: RevokeDeviceRequest,
    request: Request,
    current_user: User = Depends(require_admin),
    _step_up=Depends(require_step_up("native_device_revoke")),
    db: Session = Depends(get_db),
) -> dict:
    """
    Revoke a bound device. Requires WebAuthn step-up (Touch ID).

    After revocation, any session JWT bearing this device_id is
    rejected by `_resolve_native_session` (which re-checks
    `revoked_at IS NULL` on every request) — so revocation takes
    effect within seconds, no need to flush JWTs.
    """
    row = (
        db.query(NativeDeviceKey)
        .filter(
            NativeDeviceKey.id == device_pk,
            NativeDeviceKey.user_id == current_user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(404, detail={"error": "device_not_found"})

    if row.revoked_at is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "device_already_revoked",
                "revoked_at": _iso(row.revoked_at),
            },
        )

    row.revoked_at = _now()
    row.revoked_reason = body.reason

    db.add(
        ActionLog(
            status="CRITICAL_native_device_revoked",
            detail=(
                f"user_id={current_user.id} "
                f"device_pk={row.id} "
                f"device_id={row.device_id[:16]}… "
                f"reason={body.reason!r}"
            ),
            triggered_at=_now(),
        )
    )

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        log.error("[native/devices/revoke] DB commit failed: %s", e)
        raise HTTPException(500, detail={"error": "db_commit_failed"})

    log.warning(
        "[native/devices/revoke] user_id=%s device_id=%s… reason=%s",
        current_user.id,
        row.device_id[:16],
        body.reason,
    )

    return {"ok": True, "revoked_at": _iso(row.revoked_at), "device_id": row.device_id}
