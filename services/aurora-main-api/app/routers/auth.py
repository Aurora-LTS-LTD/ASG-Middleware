"""
ASG Solutions -- Auth Router
================================
Handles user authentication: login and registration.

ENDPOINTS:
  POST /api/v1/auth/login     -- Log in and get a JWT token
  POST /api/v1/auth/register  -- Create a new user (admin only)
  GET  /api/v1/auth/me        -- Get current user info

SECURITY NOTES:
  - Login is PUBLIC (anyone can try to log in).
  - Register is ADMIN-ONLY (only admins can create users).
  - The "me" endpoint requires a valid JWT token.
  - Passwords are NEVER returned in any response.
"""

# -----------------------------------------------------------------
# IMPORTS
# -----------------------------------------------------------------
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from aurora_shared.database import get_db, User
from aurora_shared.services.auth_service import (
    hash_password,
    verify_password,
    create_access_token,
)
from aurora_shared.middleware.auth_middleware import get_current_user, require_admin
from aurora_shared.middleware.rate_limit import limiter


# -----------------------------------------------------------------
# PYDANTIC SCHEMAS
# -----------------------------------------------------------------
# These define the SHAPE of the data the API accepts and returns.

class LoginRequest(BaseModel):
    """What you send to log in."""
    email: str
    password: str


class LoginResponse(BaseModel):
    """What you get back after a successful login."""
    access_token: str
    token_type: str = "bearer"
    role: str
    full_name: str
    user_id: int
    # True when the user signed in with a temporary/bootstrap password and
    # must set a new one before continuing. The client routes to its
    # "set new password" screen and blocks everything else until done.
    must_change_password: bool = False


class RegisterRequest(BaseModel):
    """What you send to create a new user (admin only)."""
    email: str
    password: str
    full_name: str
    role: str = "business_owner"        # "admin" or "business_owner"
    business_id: Optional[int] = None   # Required for business_owner
    language_pref: str = "ar"           # "ar" | "he" | "en"


class UserResponse(BaseModel):
    """What the API returns for a user (never includes password)."""
    id: int
    email: str
    full_name: str
    role: str
    business_id: Optional[int]
    is_active: bool
    language_pref: str


# -----------------------------------------------------------------
# CREATE THE ROUTER
# -----------------------------------------------------------------
router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


# =================================================================
# ENDPOINT 1: POST /api/v1/auth/login -- Log In
# =================================================================
@router.post("/login")
@limiter.limit("10/minute")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """
    Authenticate a user and return a JWT token.

    Steps:
      1. Find the user by email
      2. Verify the password against the stored hash
      3. Generate a JWT token with the user's info
      4. Return the token (client stores it for future requests)
    """

    # ── Step 1: Find user by email ──
    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        # Don't reveal whether the email exists or not (security best practice)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # ── Step 2: Verify password ──
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # ── Step 3: Check if account is active ──
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is disabled")

    # ── Step 4: Create JWT token ──
    # Login SUCCEEDS even with a temporary password — the forced rotation is
    # enforced downstream (the change-password endpoint clears the flag; the
    # native handshake is refused while it's set). We just surface the flag.
    must_change = bool(getattr(user, "must_change_password", False))
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        business_id=user.business_id,
        must_change_password=must_change,
    )

    print(f"[AUTH] Login successful: {user.email} (role: {user.role})")

    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "full_name": user.full_name,
        "user_id": user.id,
        "must_change_password": must_change,
    }


# =================================================================
# ENDPOINT 2: POST /api/v1/auth/register -- Create User (Admin Only)
# =================================================================
@router.post("/register")
@limiter.limit("5/minute")
def register(
    payload: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Create a new user account. Only admins can do this.

    SPRINT 1.8 DUAL-WRITE BEHAVIOR:
      If role='business_owner' AND business_id is provided, we also:
        1. Ensure the paired Organization exists (auto-backfill on the
           fly via get_or_create_organization_for_business).
        2. Create a Membership(role='owner', is_primary=True) linking
           the user to that Organization.
      This keeps the legacy User.business_id pointer and the new
      Membership table in lockstep during the expand/contract migration.

    Steps:
      1. Check the caller is an admin (handled by require_admin dependency)
      2. Verify email isn't already taken
      3. Validate role + (if business_owner) that the Business exists
      4. Hash the password and create the user record
      5. Dual-write: ensure paired Organization + Membership
      6. Return the user info (without password)
    """
    # Lazy import — avoid circular at module load
    from aurora_shared.services.identity import (
        get_or_create_organization_for_business,
        add_membership,
    )
    from aurora_shared.database import Business

    # ── Step 2: Check for duplicate email ──
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # ── Step 3: Validate role ──
    if payload.role not in ("admin", "business_owner"):
        raise HTTPException(
            status_code=400,
            detail="Role must be 'admin' or 'business_owner'",
        )

    # ── Step 3b: Pre-flight check the Business exists when business_id given ──
    if payload.business_id is not None:
        if not db.query(Business).filter(Business.id == payload.business_id).first():
            raise HTTPException(
                status_code=400,
                detail=f"business_id={payload.business_id} not found",
            )

    # ── Step 4: Hash password and create user ──
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        business_id=payload.business_id,
        language_pref=payload.language_pref,
    )
    db.add(user)
    db.flush()

    # ── Step 5: DUAL-WRITE — Organization + Membership ──
    org = None
    membership = None
    if payload.role == "business_owner" and payload.business_id is not None:
        org = get_or_create_organization_for_business(payload.business_id, db)
        membership = add_membership(
            user_id=user.id,
            organization_id=org.id,
            role="owner",
            invited_by_user_id=current_user.id,
            db=db,
        )

    db.commit()
    db.refresh(user)

    print(
        f"[AUTH] New user registered: {user.email} (role: {user.role}) "
        f"by admin {current_user.email} "
        f"[org_id={org.id if org else None} membership_id={membership.id if membership else None}]"
    )

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "business_id": user.business_id,
        "is_active": user.is_active,
        "language_pref": user.language_pref,
        # Sprint 1.8 dual-write additions
        "organization_id": org.id if org else None,
        "membership_id": membership.id if membership else None,
    }


# =================================================================
# ENDPOINT 3: GET /api/v1/auth/me -- Current User Info
# =================================================================
@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    """
    Return the current user's info based on their JWT token.
    Useful for the dashboard to know who's logged in.
    """
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "business_id": current_user.business_id,
        "is_active": current_user.is_active,
        "language_pref": current_user.language_pref,
        "must_change_password": bool(getattr(current_user, "must_change_password", False)),
    }


# =================================================================
# ENDPOINT 4: POST /api/v1/auth/change-password -- Rotate Password
# =================================================================
# Authenticated password change. The temporary/bootstrap password forces a
# rotation on first login (see User.must_change_password) — this is the ONLY
# endpoint that clears that flag. Mirrors the accountant portal's
# change-password (accountant_auth.py) but for M1 admin/owner users.
#
# Admin password rule is 12+ chars (matches scripts/bootstrap_admin.py),
# stricter than the accountant portal's 10.
MIN_ADMIN_PASSWORD_LENGTH = 12


def _validate_admin_password(v: str) -> str:
    v = v or ""
    if len(v) < MIN_ADMIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_ADMIN_PASSWORD_LENGTH} characters"
        )
    if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
        raise ValueError("Password must contain at least one letter and one number")
    return v


class ChangePasswordRequest(BaseModel):
    """Rotate the signed-in user's password."""
    old_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=MIN_ADMIN_PASSWORD_LENGTH, max_length=200)

    @field_validator("new_password")
    @classmethod
    def _pw_strength(cls, v: str) -> str:
        return _validate_admin_password(v)


@router.post("/change-password")
@limiter.limit("5/minute")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Verify the current password, set a new one, and clear the
    must_change_password flag. Authenticated via the Bearer JWT the client
    received from /login — including the temporary-password session, which
    is exactly why this endpoint is NOT behind the force-reset gate.
    """
    # ── Verify the current password ──
    if not verify_password(payload.old_password, current_user.password_hash):
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_credentials",
                    "message": "Current password is incorrect"},
        )

    # ── Reject a no-op rotation (new must differ from old) ──
    if payload.new_password == payload.old_password:
        raise HTTPException(
            status_code=400,
            detail={"error": "password_unchanged",
                    "message": "New password must be different from the current one"},
        )

    # ── Rotate + clear the forced-reset flag (the temp password dies here) ──
    current_user.password_hash = hash_password(payload.new_password)
    current_user.must_change_password = False
    db.commit()

    # Never log the password — only that a rotation happened.
    print(f"[AUTH] Password changed: {current_user.email} (role: {current_user.role})")

    return {"ok": True}
