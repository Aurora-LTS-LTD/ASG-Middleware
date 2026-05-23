"""
ASG Solutions -- Auth Middleware
================================
FastAPI dependencies that protect endpoints with JWT authentication
and enforce multi-tenant data isolation.

REAL-WORLD ANALOGY:
Think of this as the security guard at the building entrance:
  1. get_current_user()   = "Show me your badge" (checks JWT token)
  2. get_business_filter() = "Which floor can you access?" (data isolation)

HOW IT WORKS:
  Every protected endpoint adds: current_user = Depends(get_current_user)
  This means FastAPI automatically calls get_current_user() BEFORE
  the endpoint function runs. If the token is missing or invalid,
  the request is rejected with 401 Unauthorized.

MULTI-TENANCY:
  "Multi-tenancy" means multiple businesses share the same system,
  but each one can only see their own data.
  - Admin users see ALL data (like a building manager).
  - Business owners see ONLY their own business (like a tenant).
"""

# -----------------------------------------------------------------
# IMPORTS
# -----------------------------------------------------------------
import datetime
import hashlib
import os
import logging

from fastapi import Request, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db, User
from app.services.auth_service import decode_access_token

from jose import JWTError

log = logging.getLogger(__name__)


def _extract_jwt_claims(request: Request) -> dict:
    """Best-effort re-decode of the bearer JWT to read custom claims.
    Returns empty dict on any failure; never raises.
    Used by require_admin to read the `is_emergency_break_glass` / `jti`
    claims that aren't surfaced through get_current_user."""
    auth = (request.headers.get("authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return {}
    token = auth[7:].strip()
    if not token:
        return {}
    try:
        return decode_access_token(token) or {}
    except JWTError:
        return {}
    except Exception:
        return {}


def _client_ip_hash(request: Request) -> str:
    """SHA-256(salt + raw IP). Never returns the raw IP."""
    raw_ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if not raw_ip and request.client:
        raw_ip = request.client.host or ""
    salt = os.getenv("AURORA_IP_HASH_SALT", "aurora-default-salt-rotate-me")
    return hashlib.sha256(f"{salt}:{raw_ip}".encode("utf-8")).hexdigest()

# IAP JWT verification — Google's IAP public-key JWKS endpoint.
# Imported lazily inside _verify_iap_jwt so dev environments without
# google-auth (e.g., a clean venv on a laptop) can still load this
# module. The Cloud Run container ships google-auth transitively via
# google-cloud-storage / -documentai / -bigquery.
_IAP_PUBLIC_KEY_URL = "https://www.gstatic.com/iap/verify/public_key"


# -----------------------------------------------------------------
# DEPENDENCY: get_current_user
# -----------------------------------------------------------------
# PURPOSE:
#   Extract and verify the JWT token from the request header,
#   then look up the user in the database.
#
# HOW IT'S USED:
#   @router.get("/api/v1/invoices")
#   def list_invoices(current_user: User = Depends(get_current_user)):
#       ...  # This function only runs if the token is valid
#
# WHAT IT CHECKS:
#   1. Is there an "Authorization: Bearer xxx" header?
#   2. Is the token valid (not expired, not tampered)?
#   3. Does the user exist in the database?
#   4. Is the user account active?
#
# RETURNS:
#   User -- the authenticated user object from the database
#
# RAISES:
#   HTTPException 401 -- if any check fails
# -----------------------------------------------------------------
def _try_resolve_oidc_admin(token: str, db: Session) -> User | None:
    """
    OIDC service-to-service authentication path (Track 4 Phase A).

    If the bearer token is an RS256-signed Google OIDC token AND its
    `email` claim matches the AURORA_OIDC_SA_ALLOWLIST, synthesise the
    bootstrap admin User (id=1) so that aurora-api treats the call as
    admin without needing a per-SA user row.

    Returns None on any failure (silently — caller falls back to the
    HS256 Aurora-JWT path).

    Env vars:
      AURORA_OIDC_AUDIENCE        Expected `aud` claim (e.g., "https://api-aurora-lts.com").
                                  Accepts a comma-separated list for migration bake windows
                                  (e.g., "https://api-aurora-lts.com,https://api-aurora.com").
      AURORA_OIDC_SA_ALLOWLIST    Comma-separated SA emails that may impersonate admin
    """
    # Peek the alg without verifying signature. Only attempt OIDC if
    # alg=RS256 (HS256 = our Aurora-minted JWT, never RS256).
    try:
        from jose import jwt as jose_jwt
        header = jose_jwt.get_unverified_header(token)
    except Exception:
        return None
    if (header or {}).get("alg") != "RS256":
        return None

    audience = (os.getenv("AURORA_OIDC_AUDIENCE") or "").strip()
    if not audience:
        # Misconfiguration — refuse to attempt verification without
        # an explicit audience. Falls through to HS256, which will
        # also fail (the token IS RS256), and the caller gets 401.
        log.error("[get_current_user] OIDC token presented but AURORA_OIDC_AUDIENCE not set")
        return None

    try:
        from app.services.auth_oidc import verify_google_oidc_token, OidcVerificationError
        claims = verify_google_oidc_token(token, audience)
    except Exception as e:
        # Log both type AND message so we can diagnose audience/iss/sig/exp failures
        # without redeploying. The token itself never appears in the log.
        log.warning(
            "[get_current_user] OIDC verification failed: %s: %s (audience=%s)",
            type(e).__name__, str(e)[:300], audience,
        )
        return None

    email = claims.get("email", "")
    allowlist = [
        e.strip().lower()
        for e in (os.getenv("AURORA_OIDC_SA_ALLOWLIST") or "").split(",")
        if e.strip()
    ]
    if not allowlist:
        log.error("[get_current_user] OIDC token verified but AURORA_OIDC_SA_ALLOWLIST is empty")
        return None
    if email not in allowlist:
        log.warning("[get_current_user] OIDC email %s not in SA allowlist", email)
        return None

    # Synthesise the bootstrap admin — the SA call IS the trust anchor.
    # In a multi-admin future this would be a lookup-by-email or a per-SA
    # user-row mapping (tracked as Track-4 Phase 6 follow-up).
    admin = db.query(User).filter(User.id == 1, User.role == "admin").first()
    if admin is None:
        log.error("[get_current_user] OIDC path: bootstrap admin (id=1, role=admin) not found")
        return None
    if not admin.is_active:
        log.warning("[get_current_user] OIDC path: bootstrap admin is inactive")
        return None

    log.info(
        "[get_current_user] OIDC admin authenticated: sa=%s aud=%s",
        email, audience,
    )
    return admin


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """
    FastAPI dependency: verifies JWT token and returns the User.
    Add Depends(get_current_user) to any endpoint that needs protection.

    Accepts two token classes:
      • Aurora HS256 JWTs (existing — minted by app.services.auth_service)
      • Google RS256 OIDC tokens from allowlisted service accounts
        (Track 4 Phase A — service-to-service from aurora-admin-ui)
    """

    # ── Step 1: Read the Authorization header ──
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid authorization token",
        )

    token = auth_header.split(" ", 1)[1].strip()

    # ── Step 1.5: OIDC service-to-service path (Track 4 Phase A) ──
    # If the token is an RS256 Google OIDC token from an allowlisted
    # SA, short-circuit to the synthesised admin user. Falls through
    # silently on any failure so we can still try the HS256 path.
    oidc_admin = _try_resolve_oidc_admin(token, db)
    if oidc_admin is not None:
        return oidc_admin

    # ── Step 2: Decode and verify the token (HS256 Aurora JWT) ──
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token — please log in again",
        )

    # ── Step 3: Look up the user in the database ──
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # ── Step 4: Check if the user is active ──
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is disabled")

    return user


# -----------------------------------------------------------------
# FUNCTION: get_business_filter
# -----------------------------------------------------------------
# PURPOSE:
#   Determine which business_id the current user can access.
#   This enforces data isolation (multi-tenancy).
#
# REAL-WORLD ANALOGY:
#   - Admin = building manager with master key → returns None (no filter)
#   - Business owner = tenant → returns their business_id (only their data)
#
# HOW IT'S USED:
#   biz_filter = get_business_filter(current_user)
#   query = db.query(Invoice)
#   if biz_filter is not None:
#       query = query.filter(Invoice.business_id == biz_filter)
#
# PARAMETERS:
#   current_user (User) -- the authenticated user from get_current_user()
#
# RETURNS:
#   int | None -- business_id to filter by, or None for "see everything"
# -----------------------------------------------------------------
def get_business_filter(current_user: User) -> int | None:
    """Return business_id filter for data isolation. None = see all (admin)."""
    if current_user.role == "admin":
        return None  # Admin sees everything
    return current_user.business_id  # Business owner sees only their own


# -----------------------------------------------------------------
# FUNCTION: require_admin
# -----------------------------------------------------------------
# PURPOSE:
#   A stricter check -- only admin users can proceed.
#   Used for sensitive operations like creating new users.
#
# HOW IT'S USED:
#   @router.post("/api/v1/auth/register")
#   def register(current_user: User = Depends(require_admin)):
#       ...  # Only admins can reach this
#
# RETURNS:
#   User -- the admin user
#
# RAISES:
#   HTTPException 403 -- if the user is not an admin
# -----------------------------------------------------------------
def _verify_iap_jwt(jwt_assertion: str, expected_audience: str) -> str:
    """
    Verify an IAP-signed JWT against Google's IAP public-key JWKS.

    Returns the verified `email` claim on success.
    Raises `ValueError` (or library exceptions) on any failure.

    expected_audience format:
        /projects/<PROJECT_NUMBER>/global/backendServices/<NUMERIC_BACKEND_ID>

    Imports are lazy: dev environments without google-auth installed
    can still load this module; only the production code path
    (AURORA_ADMIN_REQUIRE_IAP=1) attempts the import.
    """
    # Lazy import — google-auth lives in the Cloud Run container
    # (transitively via google-cloud-* libs) but is not in dev venvs.
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests

    decoded = id_token.verify_token(
        jwt_assertion,
        google_requests.Request(),
        audience=expected_audience,
        certs_url=_IAP_PUBLIC_KEY_URL,
    )
    email = (decoded.get("email") or "").strip().lower()
    if not email:
        raise ValueError("IAP JWT verified but has no email claim")
    return email


def require_admin(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """
    FastAPI dependency: ensures the current user is an admin AND, when
    running behind IAP (production), has a verified IAP identity in
    the allowlist OR presents a valid break-glass JWT.

    Three-layer defense:

      1. JWT role check (existing) — fails fast for non-admins.
      2. **Break-glass bypass** (Track 3, NEW) — if the JWT carries
         `is_emergency_break_glass=true` AND its jti is in the
         break_glass_tokens table AND not revoked AND not expired,
         we skip IAP enforcement (the use is CRITICAL-audited).
         This is the Tier-1.5 panic key.
      3. IAP JWT verification (SEC-206) — when
         AURORA_ADMIN_REQUIRE_IAP=1, every non-break-glass admin
         call must arrive via IAP. The IAP-signed JWT in
         `X-Goog-Iap-Jwt-Assertion` is verified against Google's
         IAP JWKS, then the verified email is checked against
         AURORA_ADMIN_IAP_ALLOWLIST.

    Env vars (unchanged from SEC-206):
      AURORA_ADMIN_REQUIRE_IAP        "1" enables IAP enforcement
      AURORA_ADMIN_IAP_ALLOWLIST      comma-separated allowlist of emails
      AURORA_ADMIN_BACKEND_NUMBER     numeric backend service id
      AURORA_PROJECT_NUMBER           project number (default 9801563953)
    """
    # Step 1 — Existing JWT role check (kept first for fail-fast).
    if current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin access required",
        )

    # Step 1.5 — OIDC service-to-service bypass (Track 4 Phase A).
    # If the Bearer token's alg is RS256 we got here via the OIDC
    # path in get_current_user — that means a Google-signed identity
    # from an allowlisted service account. The SA call doesn't go
    # through the IAP LB gate (it's Cloud Run → Cloud Run), so we
    # must NOT require the X-Goog-Iap-Jwt-Assertion header.
    # get_current_user already verified the SA via Google's JWKS and
    # the allowlist; that's a strong identity claim on its own.
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.lower().startswith("bearer "):
        try:
            from jose import jwt as _jose_jwt
            _hdr = _jose_jwt.get_unverified_header(auth_header.split(" ", 1)[1].strip())
            if (_hdr or {}).get("alg") == "RS256":
                # OIDC path: identity already proven, IAP not applicable.
                return current_user
        except Exception:
            # Header peek failure is non-fatal; fall through to the
            # existing HS256+IAP enforcement path.
            pass

    # Step 2 — Break-glass bypass (NEW, Track 3).
    # If the JWT carries `is_emergency_break_glass=true`, look up the
    # jti in break_glass_tokens. If valid (registered, not revoked,
    # not expired), we BYPASS IAP enforcement entirely and audit the
    # use at CRITICAL severity.
    claims = _extract_jwt_claims(request)
    if claims.get("is_emergency_break_glass") is True:
        jti = (claims.get("jti") or "").strip()
        if not jti:
            log.warning("[require_admin] break-glass token missing jti claim (user=%s)", current_user.id)
            raise HTTPException(status_code=403, detail="Break-glass token missing jti")

        # Lazy import to avoid module-load-time circular import risk.
        from app.database.models import BreakGlassToken, ActionLog

        token_row = (
            db.query(BreakGlassToken)
            .filter(BreakGlassToken.jti == jti)
            .first()
        )
        if not token_row:
            log.warning("[require_admin] break-glass token unknown (jti=%s user=%s)", jti, current_user.id)
            raise HTTPException(status_code=403, detail="Break-glass token unknown")
        if token_row.revoked_at is not None:
            log.warning("[require_admin] break-glass token revoked (jti=%s)", jti)
            raise HTTPException(status_code=403, detail="Break-glass token revoked")
        if token_row.expires_at < datetime.datetime.utcnow():
            log.warning("[require_admin] break-glass token expired (jti=%s)", jti)
            raise HTTPException(status_code=403, detail="Break-glass token expired")

        # Record use + CRITICAL audit.
        now = datetime.datetime.utcnow()
        ip_hash = _client_ip_hash(request)
        token_row.last_used_at = now
        token_row.last_used_ip_hash = ip_hash
        token_row.use_count = (token_row.use_count or 0) + 1

        db.add(ActionLog(
            status="CRITICAL_break_glass_used",
            detail=(
                f"jti={jti} "
                f"path={request.url.path} "
                f"method={request.method} "
                f"ip_hash={ip_hash[:16]}... "
                f"use_count={token_row.use_count}"
            ),
            triggered_at=now,
        ))
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            log.error("[require_admin] break-glass audit write failed: %s", e)

        # Hard-log to stderr/Cloud Logging at CRITICAL so it shows up
        # in alerts even before the BigQuery audit cron runs.
        log.critical(
            "[BREAK_GLASS_USED] jti=%s path=%s method=%s ip_hash_prefix=%s use_count=%s user_id=%s",
            jti, request.url.path, request.method, ip_hash[:16], token_row.use_count, current_user.id,
        )

        # BYPASS IAP enforcement — break-glass identity is sufficient.
        return current_user

    # Step 3 — IAP enforcement (defense in depth on top of LB-layer IAP).
    if os.getenv("AURORA_ADMIN_REQUIRE_IAP", "0") == "1":
        iap_jwt = (request.headers.get("X-Goog-Iap-Jwt-Assertion") or "").strip()
        if not iap_jwt:
            log.warning(
                "[require_admin] IAP required but X-Goog-Iap-Jwt-Assertion header missing (user=%s)",
                current_user.id,
            )
            raise HTTPException(
                status_code=403,
                detail="IAP authentication required for admin endpoints",
            )

        backend_number = os.getenv("AURORA_ADMIN_BACKEND_NUMBER", "").strip()
        project_number = os.getenv("AURORA_PROJECT_NUMBER", "9801563953").strip()
        if not backend_number:
            # Fail closed on misconfiguration.
            log.error("[require_admin] AURORA_ADMIN_BACKEND_NUMBER not configured")
            raise HTTPException(
                status_code=500,
                detail="IAP backend configuration missing",
            )

        expected_audience = (
            f"/projects/{project_number}/global/backendServices/{backend_number}"
        )

        try:
            verified_email = _verify_iap_jwt(iap_jwt, expected_audience)
        except Exception as e:
            log.warning(
                "[require_admin] IAP JWT verification failed: %s (user=%s)",
                type(e).__name__,
                current_user.id,
            )
            raise HTTPException(
                status_code=403,
                detail="IAP JWT verification failed",
            )

        # Step 4 — Allowlist check on the *verified* email
        # (verified by Google's signature, not user-supplied).
        allowlist = [
            e.strip().lower()
            for e in os.getenv("AURORA_ADMIN_IAP_ALLOWLIST", "").split(",")
            if e.strip()
        ]
        if verified_email not in allowlist:
            log.warning(
                "[require_admin] verified IAP email %s not in allowlist (user=%s)",
                verified_email, current_user.id,
            )
            raise HTTPException(
                status_code=403,
                detail="Admin email not in IAP allowlist",
            )

    return current_user


def require_admin_iap_strict(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """
    Like `require_admin`, but REJECTS break-glass tokens. Use this
    dependency on endpoints that MUST go through real IAP — e.g.,
    the break-glass revocation endpoint itself (so a stolen
    break-glass token cannot revoke other tokens or itself).

    Behavior:
      - JWT role check
      - If JWT has `is_emergency_break_glass=true`, REJECT 403
      - Standard IAP enforcement
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    claims = _extract_jwt_claims(request)
    if claims.get("is_emergency_break_glass") is True:
        log.warning(
            "[require_admin_iap_strict] break-glass token rejected on strict endpoint (user=%s path=%s)",
            current_user.id, request.url.path,
        )
        raise HTTPException(
            status_code=403,
            detail="Break-glass tokens cannot access this endpoint; sign in via IAP",
        )

    # Step 1.5 — OIDC service-to-service bypass (Appendix M fix).
    # Same rationale as `require_admin`: an RS256 bearer means the caller
    # is a Google-allowlisted SA (verified by Google's signature in
    # `_try_resolve_oidc_admin`). The SA call is Cloud Run → Cloud Run
    # and never traverses the IAP gate, so we must not require the
    # X-Goog-Iap-Jwt-Assertion header. Break-glass tokens use HS256 so
    # this RS256 check naturally rejects them — the strict semantic
    # (no break-glass on this endpoint) is preserved.
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.lower().startswith("bearer "):
        try:
            from jose import jwt as _jose_jwt
            _hdr = _jose_jwt.get_unverified_header(auth_header.split(" ", 1)[1].strip())
            if (_hdr or {}).get("alg") == "RS256":
                return current_user
        except Exception:
            pass

    # Run the same IAP enforcement as require_admin (Step 3 above),
    # extracted to avoid duplicating the block here.
    if os.getenv("AURORA_ADMIN_REQUIRE_IAP", "0") == "1":
        iap_jwt = (request.headers.get("X-Goog-Iap-Jwt-Assertion") or "").strip()
        if not iap_jwt:
            raise HTTPException(403, "IAP authentication required for admin endpoints")
        backend_number = os.getenv("AURORA_ADMIN_BACKEND_NUMBER", "").strip()
        project_number = os.getenv("AURORA_PROJECT_NUMBER", "9801563953").strip()
        if not backend_number:
            raise HTTPException(500, "IAP backend configuration missing")
        expected_audience = (
            f"/projects/{project_number}/global/backendServices/{backend_number}"
        )
        try:
            verified_email = _verify_iap_jwt(iap_jwt, expected_audience)
        except Exception:
            raise HTTPException(403, "IAP JWT verification failed")
        allowlist = [
            e.strip().lower()
            for e in os.getenv("AURORA_ADMIN_IAP_ALLOWLIST", "").split(",")
            if e.strip()
        ]
        if verified_email not in allowlist:
            raise HTTPException(403, "Admin email not in IAP allowlist")

    return current_user


# -----------------------------------------------------------------
# DEPENDENCY FACTORY: require_org_access (Sprint 1 — Identity Foundation)
# -----------------------------------------------------------------
# PURPOSE:
#   Generate a FastAPI dependency that:
#     1. Verifies the JWT (delegates to get_current_user).
#     2. Reads `organization_id` from the request path or query.
#     3. Confirms the user has access at >= min_role to that org,
#        OR is an active accountant for it,
#        OR is a global admin.
#
# WHY A FACTORY:
#   FastAPI dependencies are *parameterless callables*. To allow
#   per-endpoint role thresholds we wrap configuration in a factory:
#
#     @router.get("/api/v1/orgs/{organization_id}/sensitive")
#     def sensitive(
#         organization_id: int,
#         user: User = Depends(require_org_access(min_role="owner")),
#     ): ...
#
# REAL-WORLD ANALOGY:
#   Like adjustable security at a building entrance. require_admin()
#   is "executive floor only". require_org_access(min_role="owner")
#   is "must own this office to enter".
#
# SECURITY POSTURE:
#   - Always re-checks access against the database. JWT claims are
#     hints, not truth.
#   - 'admin' bypasses the org check (manages all orgs).
#   - 'accountant' fallback is allowed only when min_role='employee'
#     (read-only access). For owner-level operations, accountants are
#     explicitly denied.
#
# RAISES:
#   - 401 if not authenticated (via get_current_user)
#   - 400 if the request lacks organization_id in path or query
#   - 403 if authenticated but not authorized
# -----------------------------------------------------------------
def require_org_access(min_role: str = "employee"):
    """
    Build a FastAPI dependency that enforces organization-scoped access.

    Args:
        min_role: 'employee' (default, read-level) or 'owner' (write-level)

    Returns:
        A callable suitable for `Depends(...)`.
    """
    if min_role not in ("employee", "owner"):
        raise ValueError(f"min_role must be 'employee' or 'owner', got {min_role!r}")

    def _dep(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        # Extract organization_id from path params or query string.
        # Path takes precedence over query (path is more specific).
        org_id_raw = (
            request.path_params.get("organization_id")
            or request.query_params.get("organization_id")
        )
        if org_id_raw is None:
            raise HTTPException(
                status_code=400,
                detail="organization_id is required (path or query parameter)",
            )

        try:
            org_id = int(org_id_raw)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail="organization_id must be an integer",
            )

        # Lazy-import to avoid a circular dep between middleware and services.
        from app.services.identity import user_can_access_org

        if not user_can_access_org(
            current_user, org_id, db, min_role=min_role
        ):
            raise HTTPException(
                status_code=403,
                detail=f"Access to organization {org_id} requires '{min_role}' role",
            )

        return current_user

    return _dep
