"""
Aurora LTS — WebAuthn step-up service (Appendix J Sprint 3 — T2.5 carry).

Gates state-mutating admin actions (Copilot Approve & Build, DSAR-erase,
payout approve / mark-paid, break-glass revoke, category delete) behind
Touch ID / Face ID via the Apple Keychain / Secure Enclave.

Flow:
  1. /webauthn/register/start  → server issues a registration challenge
                                  bound to the founder's User row.
  2. /webauthn/register/finish → client returns the attestation;
                                  server verifies + persists a
                                  WebauthnCredential row.
  3. /webauthn/assert/start?action=copilot_provision
                               → server issues an assertion challenge
                                  bound to (user_id, action) for 60s.
  4. /webauthn/assert/finish   → client returns the assertion;
                                  server verifies + mints a SHORT-TTL
                                  step-up TOKEN bound to (user_id, action,
                                  credential_id).
  5. /copilot/approve (or any sensitive action) verifies the step-up
     token via verify_step_up_token().

The step-up TOKEN is a signed JWT with claims:
  iss=aurora-step-up, sub=<user_id>, action=<verb>,
  cred_id=<credential_id>, jti=<uuid>, exp=<now+60s>.

Signed with WEBAUTHN_STEP_UP_SECRET env var (a 256-bit random string
mounted from Secret Manager). Separate from JWT_SECRET so a leak of one
doesn't compromise the other.

This module is INTENTIONALLY DEFENSIVE:
  • If AURORA_EXEC_REQUIRE_STEP_UP=0, callers should short-circuit
    BEFORE calling verify_step_up_token (router-level check, see
    /copilot/approve in admin_exec.py).
  • Library imports are lazy so dev venvs without webauthn package
    still load this module.
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import os
import secrets
import threading
import time
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.database.models import WebauthnCredential, User

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Config — derived from environment at first use
# ─────────────────────────────────────────────────────────────

def _rp_id() -> str:
    """Relying Party ID — MUST be the registrable domain of admin URL."""
    return os.getenv("AURORA_WEBAUTHN_RP_ID", "console.api-aurora-lts.com")


def _rp_name() -> str:
    return os.getenv("AURORA_WEBAUTHN_RP_NAME", "Aurora LTS Executive")


def _origin() -> str:
    return os.getenv("AURORA_WEBAUTHN_ORIGIN", "https://console.api-aurora-lts.com")


def _step_up_secret() -> bytes:
    """HMAC secret used to sign step-up tokens."""
    raw = os.getenv("WEBAUTHN_STEP_UP_SECRET", "")
    if not raw:
        # Dev-mode fallback — DO NOT use in production. We pin to a
        # known value so the same instance can sign + verify, but a
        # warning is logged.
        log.warning(
            "WEBAUTHN_STEP_UP_SECRET not set — using DEV fallback (NOT FOR PROD)"
        )
        raw = "dev-step-up-secret-rotate-me-via-secret-manager"
    return raw.encode("utf-8")


# Step-up token lifetime (mintage → expiry)
STEP_UP_TOKEN_TTL_S = 60

# Challenge cache (in-memory, per-process). Acceptable for single-CEO
# single-Cloud Run-instance scale. If we ever go multi-instance we'd
# move this to Memorystore Redis.
_CHALLENGE_TTL_S = 300  # 5 min — user has time to complete biometric prompt
_challenge_lock = threading.Lock()
_challenge_cache: Dict[str, Dict[str, Any]] = {}


# ─────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────

class WebauthnError(Exception):
    pass


class StepUpVerificationError(Exception):
    pass


# ─────────────────────────────────────────────────────────────
# Challenge cache helpers
# ─────────────────────────────────────────────────────────────

def _challenge_key(user_id: int, kind: str) -> str:
    return f"{kind}:{user_id}"


def _store_challenge(user_id: int, kind: str, payload: Dict[str, Any]) -> None:
    payload = dict(payload)
    payload["_expires_at"] = time.monotonic() + _CHALLENGE_TTL_S
    with _challenge_lock:
        _challenge_cache[_challenge_key(user_id, kind)] = payload


def _pop_challenge(user_id: int, kind: str) -> Optional[Dict[str, Any]]:
    with _challenge_lock:
        item = _challenge_cache.pop(_challenge_key(user_id, kind), None)
    if not item:
        return None
    if item.get("_expires_at", 0) < time.monotonic():
        return None
    return item


def _new_challenge_bytes() -> bytes:
    return secrets.token_bytes(32)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


# ─────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────

def begin_registration(user: User) -> Dict[str, Any]:
    """Issue a registration challenge for a new passkey.

    Returns the PublicKeyCredentialCreationOptions dict the browser
    feeds to `navigator.credentials.create({ publicKey: ... })`.
    """
    try:
        from webauthn import generate_registration_options
        from webauthn.helpers.structs import (
            AuthenticatorSelectionCriteria,
            ResidentKeyRequirement,
            UserVerificationRequirement,
        )
    except ImportError as e:
        raise WebauthnError(f"webauthn library not installed: {e}")

    challenge = _new_challenge_bytes()

    user_id_bytes = str(user.id).encode("utf-8")
    user_name = (user.email or f"user-{user.id}")[:128]
    user_display_name = (getattr(user, "full_name", None) or user_name)[:128]

    options = generate_registration_options(
        rp_id=_rp_id(),
        rp_name=_rp_name(),
        user_id=user_id_bytes,
        user_name=user_name,
        user_display_name=user_display_name,
        challenge=challenge,
        timeout=60_000,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )

    # Cache challenge for the finish step
    _store_challenge(user.id, "register", {"challenge": challenge})

    # Return as a JSON-friendly dict the browser can consume directly
    return json.loads(options.model_dump_json(by_alias=True))


def finish_registration(
    user: User,
    credential_dict: Dict[str, Any],
    device_label: Optional[str],
    db: Session,
) -> WebauthnCredential:
    """Verify the attestation and persist a new credential."""
    try:
        from webauthn import verify_registration_response
        from webauthn.helpers.structs import RegistrationCredential
    except ImportError as e:
        raise WebauthnError(f"webauthn library not installed: {e}")

    challenge_blob = _pop_challenge(user.id, "register")
    if not challenge_blob:
        raise WebauthnError("Registration challenge expired or not found. Restart enrollment.")

    try:
        credential = RegistrationCredential.model_validate(credential_dict)
        result = verify_registration_response(
            credential=credential,
            expected_challenge=challenge_blob["challenge"],
            expected_rp_id=_rp_id(),
            expected_origin=_origin(),
            require_user_verification=True,
        )
    except Exception as e:
        raise WebauthnError(f"Registration verification failed: {type(e).__name__}: {str(e)[:240]}")

    row = WebauthnCredential(
        user_id=user.id,
        credential_id=_b64url(result.credential_id),
        public_key=_b64url(result.credential_public_key),
        sign_count=result.sign_count or 0,
        device_label=device_label,
        aaguid=str(result.aaguid) if result.aaguid else None,
        transports=json.dumps([t.value for t in (credential.response.transports or [])]) if hasattr(credential.response, "transports") else None,
        last_used_at=None,
        last_used_ip_hash=None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ─────────────────────────────────────────────────────────────
# Assertion (step-up)
# ─────────────────────────────────────────────────────────────

def begin_assertion(user: User, action: str, db: Session) -> Dict[str, Any]:
    """Issue an assertion challenge bound to (user_id, action).

    Returns PublicKeyCredentialRequestOptions for the browser.
    """
    try:
        from webauthn import generate_authentication_options
        from webauthn.helpers.structs import (
            UserVerificationRequirement,
            PublicKeyCredentialDescriptor,
        )
    except ImportError as e:
        raise WebauthnError(f"webauthn library not installed: {e}")

    creds = (
        db.query(WebauthnCredential)
        .filter(WebauthnCredential.user_id == user.id)
        .filter(WebauthnCredential.revoked_at.is_(None))
        .all()
    )
    if not creds:
        raise WebauthnError(
            "No registered passkeys for this user. Register one at /executive/copilot first visit."
        )

    challenge = _new_challenge_bytes()

    allow_list = [
        PublicKeyCredentialDescriptor(id=_b64url_decode(c.credential_id))
        for c in creds
    ]

    options = generate_authentication_options(
        rp_id=_rp_id(),
        challenge=challenge,
        allow_credentials=allow_list,
        timeout=60_000,
        user_verification=UserVerificationRequirement.REQUIRED,
    )

    _store_challenge(user.id, f"assert:{action}", {
        "challenge": challenge,
        "action": action,
    })

    return json.loads(options.model_dump_json(by_alias=True))


def finish_assertion(
    user: User,
    action: str,
    credential_dict: Dict[str, Any],
    db: Session,
) -> Tuple[str, int]:
    """Verify the assertion and mint a step-up TOKEN.

    Returns (step_up_token, credential_id).
    """
    try:
        from webauthn import verify_authentication_response
        from webauthn.helpers.structs import AuthenticationCredential
    except ImportError as e:
        raise WebauthnError(f"webauthn library not installed: {e}")

    challenge_blob = _pop_challenge(user.id, f"assert:{action}")
    if not challenge_blob:
        raise WebauthnError("Assertion challenge expired or not found. Restart step-up.")

    try:
        cred = AuthenticationCredential.model_validate(credential_dict)
    except Exception as e:
        raise WebauthnError(f"Malformed credential payload: {str(e)[:200]}")

    # Look up the stored credential by its public credential_id
    submitted_cred_id_b64url = _b64url(cred.raw_id)
    stored = (
        db.query(WebauthnCredential)
        .filter(WebauthnCredential.user_id == user.id)
        .filter(WebauthnCredential.credential_id == submitted_cred_id_b64url)
        .filter(WebauthnCredential.revoked_at.is_(None))
        .first()
    )
    if not stored:
        raise WebauthnError("Unknown credential — not registered or revoked")

    try:
        result = verify_authentication_response(
            credential=cred,
            expected_challenge=challenge_blob["challenge"],
            expected_rp_id=_rp_id(),
            expected_origin=_origin(),
            credential_public_key=_b64url_decode(stored.public_key),
            credential_current_sign_count=stored.sign_count or 0,
            require_user_verification=True,
        )
    except Exception as e:
        raise WebauthnError(f"Assertion verification failed: {type(e).__name__}: {str(e)[:240]}")

    # Update sign_count + last_used_at
    stored.sign_count = result.new_sign_count or stored.sign_count
    stored.last_used_at = datetime.datetime.utcnow()
    db.commit()

    token = _mint_step_up_token(
        user_id=user.id,
        action=action,
        credential_id=stored.id,
    )
    return token, stored.id


# ─────────────────────────────────────────────────────────────
# Step-up token mint + verify (HMAC-SHA256)
# ─────────────────────────────────────────────────────────────

def _mint_step_up_token(*, user_id: int, action: str, credential_id: int) -> str:
    """Sign a step-up token as a compact JWT (alg=HS256)."""
    try:
        from jose import jwt as _jose_jwt
    except ImportError as e:
        raise WebauthnError(f"python-jose not installed: {e}")

    now = int(time.time())
    claims = {
        "iss": "aurora-step-up",
        "sub": str(user_id),
        "action": action,
        "cred_id": credential_id,
        "jti": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + STEP_UP_TOKEN_TTL_S,
    }
    return _jose_jwt.encode(claims, _step_up_secret(), algorithm="HS256")


def verify_step_up_token(
    *,
    token: str,
    expected_action: str,
    user_id: int,
    db: Session,
) -> int:
    """Verify a step-up token. Returns the credential_id used to sign.

    Raises StepUpVerificationError on any failure.
    """
    if not token:
        raise StepUpVerificationError("missing step_up_token")

    try:
        from jose import jwt as _jose_jwt
        from jose.exceptions import JWTError, ExpiredSignatureError
    except ImportError as e:
        raise StepUpVerificationError(f"jose not installed: {e}")

    try:
        claims = _jose_jwt.decode(
            token,
            _step_up_secret(),
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub", "action", "cred_id"]},
        )
    except ExpiredSignatureError:
        raise StepUpVerificationError("step-up token expired")
    except JWTError as e:
        raise StepUpVerificationError(f"step-up token invalid: {e}")

    if claims.get("iss") != "aurora-step-up":
        raise StepUpVerificationError("wrong issuer")
    if claims.get("action") != expected_action:
        raise StepUpVerificationError(
            f"step-up token action mismatch: token={claims.get('action')} expected={expected_action}"
        )
    try:
        sub_user = int(claims.get("sub") or "0")
    except ValueError:
        raise StepUpVerificationError("step-up token sub not int")
    if sub_user != user_id:
        raise StepUpVerificationError("step-up token sub mismatch")

    cred_id = int(claims.get("cred_id") or 0)
    if not cred_id:
        raise StepUpVerificationError("step-up token missing cred_id")

    # Sanity: credential still exists + not revoked
    cred = (
        db.query(WebauthnCredential)
        .filter(WebauthnCredential.id == cred_id)
        .filter(WebauthnCredential.user_id == user_id)
        .first()
    )
    if not cred:
        raise StepUpVerificationError("credential no longer registered")
    if cred.revoked_at is not None:
        raise StepUpVerificationError("credential revoked")

    return cred_id


# ─────────────────────────────────────────────────────────────
# FastAPI dependency factory
# ─────────────────────────────────────────────────────────────

def require_step_up(action: str):
    """Build a FastAPI dep that enforces a step-up token bound to `action`.

    Usage:
        @router.post("/dangerous", dependencies=[Depends(require_step_up("dsar_erase"))])

    Or as a regular dep that returns the credential_id:
        cred_id: int = Depends(require_step_up("payout_approve"))

    Honors AURORA_EXEC_REQUIRE_STEP_UP=0 escape hatch — when off, the
    dep is a no-op and returns 0 (no credential).
    """
    from fastapi import Depends, Header, HTTPException
    from app.database import get_db
    from app.middleware.auth_middleware import get_current_user

    def _dep(
        x_aurora_step_up: Optional[str] = Header(default=None, alias="X-Aurora-Step-Up"),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> int:
        if os.getenv("AURORA_EXEC_REQUIRE_STEP_UP", "0") != "1":
            return 0
        try:
            return verify_step_up_token(
                token=x_aurora_step_up or "",
                expected_action=action,
                user_id=current_user.id,
                db=db,
            )
        except StepUpVerificationError as e:
            raise HTTPException(
                status_code=403,
                detail=f"Step-up required for '{action}': {e}",
            )

    return _dep
