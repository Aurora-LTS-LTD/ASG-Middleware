"""
ASG Solutions -- Auth Service
================================
Handles password hashing and JWT token creation/verification.
This is PURE business logic -- no HTTP, no FastAPI, no request objects.

REAL-WORLD ANALOGY:
Think of this as the ID card office at a secure building:
  1. hash_password()      = laminating a new ID card (irreversible)
  2. verify_password()    = scanning an ID card at the door
  3. create_access_token() = issuing a temporary visitor badge (expires in 24h)
  4. decode_access_token() = reading the badge to check who it belongs to

HOW JWT WORKS:
  JWT = JSON Web Token. It's a signed string that contains user info.
  When a user logs in, we give them a token. On every request after that,
  they send the token back. We verify it's valid (not expired, not tampered).
  The server NEVER stores the token -- it just signs and verifies.
"""

# -----------------------------------------------------------------
# IMPORTS
# -----------------------------------------------------------------
import os
import datetime

from jose import jwt, JWTError          # JWT signing + verification
from passlib.context import CryptContext  # Password hashing (bcrypt)


# -----------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------
# JWT_SECRET: the secret key used to sign tokens.
#   If someone gets this key, they can forge tokens.
#   Keep it in .env, NEVER in code.
# JWT_EXPIRATION_HOURS: how long a token is valid.
#   After this time, the user must log in again.
JWT_SECRET = os.getenv("JWT_SECRET", "asg-dev-secret-change-in-production")
JWT_EXPIRATION_HOURS = int(os.getenv("JWT_EXPIRATION_HOURS", "24"))
JWT_ALGORITHM = "HS256"  # HMAC-SHA256 -- fast, secure, industry standard

# CryptContext tells passlib to use bcrypt for hashing.
# bcrypt is the gold standard for password hashing -- it's slow
# ON PURPOSE, making brute-force attacks impractical.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# -----------------------------------------------------------------
# FUNCTION: hash_password
# -----------------------------------------------------------------
# PURPOSE:
#   Take a plain text password and turn it into an irreversible hash.
#   "$2b$12$..." is the bcrypt format -- you can't reverse it.
#
# WHEN USED:
#   - When creating a new user (registration)
#   - When changing a password
#
# PARAMETERS:
#   plain (str) -- the password the user typed (e.g., "admin123")
#
# RETURNS:
#   str -- the hashed password (e.g., "$2b$12$xK9...")
# -----------------------------------------------------------------
def hash_password(plain: str) -> str:
    """Hash a plain-text password using bcrypt."""
    return pwd_context.hash(plain)


# -----------------------------------------------------------------
# FUNCTION: verify_password
# -----------------------------------------------------------------
# PURPOSE:
#   Check if a plain text password matches a stored hash.
#   bcrypt handles the comparison internally -- it hashes the input
#   the same way and compares the results.
#
# WHEN USED:
#   - During login: user sends plain password, we compare to stored hash
#
# PARAMETERS:
#   plain (str)  -- what the user just typed
#   hashed (str) -- what's stored in the database
#
# RETURNS:
#   bool -- True if they match, False if not
# -----------------------------------------------------------------
def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against a stored hash."""
    return pwd_context.verify(plain, hashed)


# -----------------------------------------------------------------
# FUNCTION: create_access_token
# -----------------------------------------------------------------
# PURPOSE:
#   Generate a JWT token for a logged-in user.
#   The token contains: user_id, role, business_id, expiration.
#   It's signed with JWT_SECRET so nobody can tamper with it.
#
# REAL-WORLD ANALOGY:
#   Like issuing a visitor badge at a building entrance:
#   - Name tag (sub = user_id)
#   - Access level (role = "admin" or "business_owner")
#   - Which floor they can visit (business_id)
#   - Expiry time stamped on the badge (exp)
#
# PARAMETERS:
#   user_id (int)      -- the user's database ID
#   role (str)         -- "admin" or "business_owner"
#   business_id (int|None) -- which business they belong to (None for admin)
#
# RETURNS:
#   str -- the JWT token string ("eyJ..." -- a long encoded string)
# -----------------------------------------------------------------
def create_access_token(
    user_id: int,
    role: str,
    business_id: int | None,
    *,
    active_org_ids: list[int] | None = None,
    primary_org_id: int | None = None,
    accountant_of: list[int] | None = None,
    expires_hours: int | None = None,
) -> str:
    """
    Create a signed JWT token for a user.

    EXISTING CALLERS (positional args only) keep working unchanged —
    the new keyword-only args are optional and default to None.

    NEW CLAIMS (Sprint 1 / Identity Foundation):
      - active_org_ids   : list of Organization ids the user has Membership in
      - primary_org_id   : the user's "default" org (used when context is
                            ambiguous, e.g. dashboard with no ?org_id query)
      - accountant_of    : list of Organization ids where the user is an
                            ACTIVE accountant (via AccountantEngagement)

    SECURITY NOTE:
      These claims are READ HINTS only — every privileged endpoint MUST
      re-verify access against the database via require_org_access().
      A leaked/old JWT must never grant access to an org the user has
      since been removed from. Fast claims, slow truth.

    EXPIRATION:
      Defaults to JWT_EXPIRATION_HOURS env (24h). Pass `expires_hours` to
      override for short-lived tokens (e.g. accountant-role tokens use 1h).
    """
    hours = expires_hours if expires_hours is not None else JWT_EXPIRATION_HOURS
    expire = datetime.datetime.utcnow() + datetime.timedelta(hours=hours)

    payload = {
        "sub": str(user_id),
        "role": role,
        "business_id": business_id,
        "exp": expire,
    }

    # Only include the new claims when provided — keeps existing tokens
    # compatible and avoids JWT bloat when the data isn't needed.
    if active_org_ids is not None:
        payload["aoi"] = active_org_ids       # short claim name to keep token small
    if primary_org_id is not None:
        payload["poi"] = primary_org_id
    if accountant_of is not None:
        payload["aco"] = accountant_of

    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token


# -----------------------------------------------------------------
# FUNCTION: decode_access_token
# -----------------------------------------------------------------
# PURPOSE:
#   Take a JWT token and extract the payload (user_id, role, etc.).
#   Also verifies the token hasn't expired and hasn't been tampered with.
#
# REAL-WORLD ANALOGY:
#   Like scanning a visitor badge at a door:
#   - Is the badge valid? (signature check)
#   - Has it expired? (expiration check)
#   - Who does it belong to? (read the payload)
#
# PARAMETERS:
#   token (str) -- the JWT token from the Authorization header
#
# RETURNS:
#   dict -- the payload ({"sub": "1", "role": "admin", ...})
#
# RAISES:
#   JWTError -- if the token is invalid, expired, or tampered with
# -----------------------------------------------------------------
def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT token. Raises JWTError if invalid."""
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    return payload
