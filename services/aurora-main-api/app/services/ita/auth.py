"""
Aurora LTS — ITA JWT Authentication
======================================
Sprint 3 — JWT signing helpers for production ITA calls.

The Israel Tax Authority Software-House protocol (per the published
technical manual) requires every API request to be authenticated with
a JWT signed by the software-house's REGISTERED PRIVATE KEY. The
public key is uploaded to ITA at certification time and ITA verifies
the signature against it on every request.

KEY MATERIAL:
  - Loaded from Secret Manager via app.services.gcp.secrets
  - Secret name configurable via ITA_PRIVATE_KEY_SECRET (default:
    AURORA_ITA_PRIVATE_KEY)
  - Format: PEM-encoded RSA private key (ITA accepts RS256)
  - Aurora's PUBLIC key counterpart is registered at ITA's
    Software-House portal during onboarding

CLAIMS:
  iss   software-house-id       (issuer = ASG / Aurora)
  sub   seller_tax_id           (the taxpayer making the call)
  aud   "ita.gov.il"            (audience)
  iat   issued-at unix ts
  exp   iat + 5 minutes         (short window — replay-safe)
  jti   request_id (UUIDv4)     (single-use idempotency token)

NOTE — STUB-FRIENDLINESS:
  When ITA_BACKEND=mock, NONE of this code runs. Even on production
  startup, the JWT signing call only happens at the moment of an
  outbound ITA request — so a missing private key in dev does not
  break boot.
"""

import datetime
import os
import re
from typing import Optional

from app.services.gcp.secrets import get_secret


def _software_house_id() -> str:
    """ITA-issued ID for the software-house (Aurora). One per certification."""
    return os.getenv("ITA_SOFTWARE_HOUSE_ID", "")


def _private_key_secret_name() -> str:
    return os.getenv("ITA_PRIVATE_KEY_SECRET", "AURORA_ITA_PRIVATE_KEY")


def _audience() -> str:
    return os.getenv("ITA_AUDIENCE", "ita.gov.il")


def _ttl_seconds() -> int:
    return int(os.getenv("ITA_JWT_TTL_SECONDS", "300"))


_TAX_ID_RE = re.compile(r"^\d{9}$")


def _validate_seller_tax_id(seller_tax_id: str) -> str:
    """Israeli ח.פ/ע.מ tax IDs are 9 digits. Reject anything else BEFORE signing,
    so we never emit a JWT whose `sub` ITA will reject as malformed."""
    s = (seller_tax_id or "").strip()
    if not _TAX_ID_RE.match(s):
        raise ValueError(
            f"seller_tax_id must be a 9-digit Israeli tax id (got {seller_tax_id!r})"
        )
    return s


def build_request_id(invoice_id: int, retry_count: int = 0) -> str:
    """
    Deterministic idempotency key for an ITA request:  "<invoice_id>:<retry_count>".

    A retry of the SAME allocation attempt MUST produce the SAME request_id, so
    ITA's duplicate-detection returns the original allocation instead of issuing
    a second one. (A previous version appended a random uuid here, which broke
    that guarantee — every call produced a fresh id, so retries could double-allocate.)
    """
    return f"{invoice_id}:{retry_count}"


def sign_request(*, seller_tax_id: str, request_id: str) -> str:
    """
    Build and sign a JWT for an ITA API call.

    Returns the compact JWT (header.payload.signature) ready to drop
    into the Authorization header as `Bearer <jwt>`.

    Raises if the signing key is not available — caller should treat
    this as a configuration error and fall back to mock if appropriate.
    """
    from jose import jwt  # already in requirements via python-jose

    # Validate the taxpayer id before we sign — a malformed `sub` would be
    # rejected by ITA anyway, and this fails fast with a clear error.
    seller_tax_id = _validate_seller_tax_id(seller_tax_id)

    private_key = get_secret(_private_key_secret_name())
    if not private_key:
        raise RuntimeError(
            f"ITA private signing key not found "
            f"(secret name: {_private_key_secret_name()!r}). "
            f"Set it in Secret Manager (or env in dev)."
        )

    issuer = _software_house_id()
    if not issuer:
        raise RuntimeError(
            "ITA_SOFTWARE_HOUSE_ID is unset. Cannot sign without the "
            "ITA-issued software-house identifier."
        )

    now = datetime.datetime.utcnow()
    claims = {
        "iss": issuer,
        "sub": seller_tax_id,
        "aud": _audience(),
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(seconds=_ttl_seconds())).timestamp()),
        "jti": request_id,
    }

    return jwt.encode(claims, private_key, algorithm="RS256")
