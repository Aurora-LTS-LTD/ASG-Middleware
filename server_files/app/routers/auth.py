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
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, User
from app.services.auth_service import (
    hash_password,
    verify_password,
    create_access_token,
)
from app.middleware.auth_middleware import get_current_user, require_admin
from app.middleware.rate_limit import limiter


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
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        business_id=user.business_id,
    )

    print(f"[AUTH] Login successful: {user.email} (role: {user.role})")

    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "full_name": user.full_name,
        "user_id": user.id,
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
    from app.services.identity import (
        get_or_create_organization_for_business,
        add_membership,
    )
    from app.database import Business

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
    }
