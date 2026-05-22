"""
Aurora LTS — Google OIDC Token Verification (Phase A of Track 4 OIDC refactor)
================================================================================
Verifies Google-issued OpenID Connect (OIDC) identity tokens used by
service-to-service Cloud Run calls. The metadata server inside a
Cloud Run container hands out RS256-signed JWTs whose `email` claim
is the calling service-account identity; this module turns those into
a verified claim dict that `require_admin` can authorise on.

USAGE (from app/middleware/auth_middleware.py):

    from app.services.auth_oidc import verify_google_oidc_token
    claims = verify_google_oidc_token(token, expected_audience="https://api-aurora.com")
    email = claims["email"]  # already lowercased + verified

DEPENDENCIES:
    `google-auth` is in the Cloud Run container via the google-cloud-*
    libraries. Local dev venvs may lack it; we import lazily so this
    module loads cleanly without the dep.

SECURITY:
    - RS256 signature verified against Google's public keys
    - `iss` validated as accounts.google.com / https://accounts.google.com
    - `aud` exact-match against expected_audience (caller specifies)
    - `email_verified` must be true
    - `exp` enforced by the library
    Nothing else is trusted from the token.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

log = logging.getLogger(__name__)


# Google's issuer claim variants. Both are emitted by the metadata
# server depending on token version; the library accepts both, but
# we double-check defensively after verification.
_ACCEPTED_ISS = {"accounts.google.com", "https://accounts.google.com"}


class OidcVerificationError(Exception):
    """Raised when an OIDC token fails any verification step."""


def verify_google_oidc_token(token: str, expected_audience: str) -> Dict[str, Any]:
    """
    Verify an RS256 Google-issued OIDC token.

    Args:
        token: the raw JWT string (no `Bearer ` prefix).
        expected_audience: the `aud` claim that signed tokens must
            carry — typically the canonical URL of this service
            (e.g., "https://api-aurora.com"). The caller fetches the
            token with this audience from the metadata server, and
            we require an exact match here.

    Returns:
        A dict of the verified claims (sub, email, email_verified,
        iss, aud, exp, iat, ...). The `email` value is lowercased
        for safe comparison.

    Raises:
        OidcVerificationError on any verification failure.
    """
    if not token or len(token) < 16:
        raise OidcVerificationError("OIDC token missing or too short")
    if not expected_audience:
        raise OidcVerificationError("expected_audience must be configured (AURORA_OIDC_AUDIENCE)")

    # Lazy import so dev venvs that don't have google-auth installed
    # can still load this module (the function will simply not be
    # called in dev mode).
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests
    except ImportError as e:
        raise OidcVerificationError(
            f"google-auth not installed (expected on Cloud Run container): {e}"
        )

    transport = google_requests.Request()

    try:
        # verify_oauth2_token handles: signature verification against
        # Google's JWKS (cached internally), `iss` validation,
        # `aud` exact-match, and `exp` enforcement. It does NOT
        # enforce `email_verified` — we do that below.
        claims: Dict[str, Any] = id_token.verify_oauth2_token(
            token,
            transport,
            audience=expected_audience,
        )
    except ValueError as e:
        # google-auth raises ValueError on any verification failure
        # (bad signature, wrong audience, expired, etc.).
        raise OidcVerificationError(f"Google OIDC verification failed: {e}")
    except Exception as e:  # defensive — JWKS fetch network failure etc.
        raise OidcVerificationError(f"Unexpected OIDC verification error: {e}")

    # Belt-and-suspenders: re-check issuer.
    iss = claims.get("iss")
    if iss not in _ACCEPTED_ISS:
        raise OidcVerificationError(f"Unexpected iss claim: {iss!r}")

    # Service-account tokens from the metadata server always have
    # email_verified=True; defense-in-depth refuses if it's missing
    # or false.
    if claims.get("email_verified") is not True:
        raise OidcVerificationError("OIDC token email_verified is not true")

    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise OidcVerificationError("OIDC token missing email claim")
    claims["email"] = email  # normalise for caller

    # Defensive exp re-check (verify_oauth2_token already enforces but
    # we double-up — cheap and surfaces clock-skew issues clearly).
    exp = claims.get("exp")
    if exp is not None and exp < int(time.time()) - 5:
        raise OidcVerificationError("OIDC token expired")

    return claims
