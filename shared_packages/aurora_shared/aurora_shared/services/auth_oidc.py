"""
Aurora LTS — Google OIDC Token Verification (Phase A of Track 4 OIDC refactor)
================================================================================
Verifies Google-issued OpenID Connect (OIDC) identity tokens used by
service-to-service Cloud Run calls. The metadata server inside a
Cloud Run container hands out RS256-signed JWTs whose `email` claim
is the calling service-account identity; this module turns those into
a verified claim dict that `require_admin` can authorise on.

USAGE (from app/middleware/auth_middleware.py):

    from aurora_shared.services.auth_oidc import verify_google_oidc_token
    claims = verify_google_oidc_token(token, expected_audience="https://api-aurora-lts.com")
    email = claims["email"]  # already lowercased + verified

    # Multi-audience (e.g., during a domain migration bake window):
    claims = verify_google_oidc_token(
        token,
        expected_audience="https://api-aurora-lts.com,https://api-aurora.com",
    )
    # OR pass a list directly:
    claims = verify_google_oidc_token(
        token,
        expected_audience=["https://api-aurora-lts.com", "https://api-aurora.com"],
    )

DEPENDENCIES:
    `google-auth` is in the Cloud Run container via the google-cloud-*
    libraries. Local dev venvs may lack it; we import lazily so this
    module loads cleanly without the dep.

SECURITY:
    - RS256 signature verified against Google's public keys
    - `iss` validated as accounts.google.com / https://accounts.google.com
    - `aud` exact-match against expected_audience (or any value in the
      audience list — Google's lib does the per-entry exact-match)
    - `email_verified` must be true
    - `exp` enforced by the library
    Nothing else is trusted from the token.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Union

log = logging.getLogger(__name__)


# Google's issuer claim variants. Both are emitted by the metadata
# server depending on token version; the library accepts both, but
# we double-check defensively after verification.
_ACCEPTED_ISS = {"accounts.google.com", "https://accounts.google.com"}


class OidcVerificationError(Exception):
    """Raised when an OIDC token fails any verification step."""


def _normalize_audience(value: Union[str, List[str]]) -> List[str]:
    """
    Accept either a single audience string, a comma-separated string
    (useful for `AURORA_OIDC_AUDIENCE` env var during migration bake
    windows), or an explicit list. Returns a clean list with no empty
    entries and trailing whitespace stripped.
    """
    if isinstance(value, list):
        items = value
    else:
        items = (value or "").split(",")
    cleaned = [a.strip() for a in items if a and a.strip()]
    return cleaned


def verify_google_oidc_token(
    token: str,
    expected_audience: Union[str, List[str]],
) -> Dict[str, Any]:
    """
    Verify an RS256 Google-issued OIDC token.

    Args:
        token: the raw JWT string (no `Bearer ` prefix).
        expected_audience: the `aud` claim that signed tokens must
            carry — typically the canonical URL of this service
            (e.g., "https://api-aurora-lts.com"). Accepts:
              * a single string ("https://api-aurora-lts.com")
              * a comma-separated string for migration bake windows
                ("https://api-aurora-lts.com,https://api-aurora.com")
              * an explicit list of strings
            Google's `verify_oauth2_token` does the per-entry
            exact-match; passing multiple values is the safe pattern
            during a domain cutover.

    Returns:
        A dict of the verified claims (sub, email, email_verified,
        iss, aud, exp, iat, ...). The `email` value is lowercased
        for safe comparison.

    Raises:
        OidcVerificationError on any verification failure.
    """
    if not token or len(token) < 16:
        raise OidcVerificationError("OIDC token missing or too short")

    audiences = _normalize_audience(expected_audience)
    if not audiences:
        raise OidcVerificationError(
            "expected_audience must be configured (AURORA_OIDC_AUDIENCE)"
        )

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
        # `aud` exact-match (or any-of-list-match when audience is a
        # list), and `exp` enforcement. It does NOT enforce
        # `email_verified` — we do that below.
        # Pass single string if one audience, list if multiple — the
        # library accepts either.
        aud_arg: Union[str, List[str]] = (
            audiences[0] if len(audiences) == 1 else audiences
        )
        claims: Dict[str, Any] = id_token.verify_oauth2_token(
            token,
            transport,
            audience=aud_arg,
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
