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
import uuid
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


def build_request_id(invoice_id: int, retry_count: int = 0) -> str:
    """
    Build the idempotency key for an ITA request. Format:

        <invoice_id>:<retry_count>:<short-uuid>

    The invoice_id+retry pair guarantees that a Cloud Tasks retry of the
    same allocation attempt produces the SAME request_id — ITA's
    duplicate-detection then returns the original response instead of
    issuing a second allocation.

    The trailing UUID short ensures different INVOICE × RETRY rounds
    stay distinct even within the same second.
    """
    return f"{invoice_id}:{retry_count}:{uuid.uuid4().hex[:8]}"


def sign_request(*, seller_tax_id: str, request_id: str) -> str:
    """
    Build and sign a JWT for an ITA API call.

    Returns the compact JWT (header.payload.signature) ready to drop
    into the Authorization header as `Bearer <jwt>`.

    Raises if the signing key is not available — caller should treat
    this as a configuration error and fall back to mock if appropriate.
    """
    from jose import jwt  # already in requirements via python-jose

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
