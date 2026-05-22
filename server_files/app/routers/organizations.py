"""
ASG Solutions — Organizations Router
======================================
Sprint 1 of the Tax & Document Layer.

ENDPOINTS:
  POST /api/v1/organizations
       Create a new organization (the caller becomes its owner).

  GET  /api/v1/organizations
       List organizations the current user belongs to (or all if admin).

  GET  /api/v1/organizations/{organization_id}
       Read a single organization (owner / employee / accountant access).

  POST /api/v1/organizations/{organization_id}/invitations
       Create an invitation to join the organization
       (role='employee' or 'accountant'). Requires owner role.

  GET  /api/v1/invitations/{code}
       Public lookup of an invitation by its UUIDv4 code.
       Returns minimal info — used by the accept-invitation page.

  POST /api/v1/invitations/{code}/accept
       Accept an invitation as the currently-authenticated user.
       Creates Membership(role='employee') or AccountantEngagement(active).

  GET  /api/v1/me/context
       Return the current user's full multi-tenant context:
       active_org_ids, primary_org_id, role_per_org, accountant_of.

SECURITY:
  - All endpoints (except /invitations/{code} GET) require JWT.
  - Org-scoped endpoints use require_org_access() to verify membership.
  - Tax IDs and PII are server-validated; never trust client input.
  - Every state-changing endpoint writes to ActionLog (KYC dossier).

REAL-WORLD ANALOGY:
  This router is the front desk of a co-working building. It registers
  new tenants (POST /organizations), tells you who works where
  (GET /organizations, /me/context), and processes the visitor passes
  the existing tenants hand out (POST /invitations).
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# NOTE: We deliberately use `str` (with a basic regex) rather than
# pydantic.EmailStr to avoid adding the `email-validator` dependency.
# Real email reachability is verified by the OTP-via-email flow, not
# by Pydantic. The pattern below is RFC-5322 best-effort.
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

from app.database import (
    get_db,
    User,
    Organization,
    Membership,
    AccountantEngagement,
    Invitation,
)
from app.middleware.auth_middleware import (
    get_current_user,
    require_org_access,
)
from app.services.identity import (
    create_organization,
    resolve_user_context,
    create_invitation,
    accept_invitation,
    get_invitation_by_code,
    validate_tax_id_israel,
    normalize_tax_id,
)


# ─────────────────────────────────────────────────────────────
# ROUTER
# ─────────────────────────────────────────────────────────────
router = APIRouter(prefix="/api/v1", tags=["organizations"])


# ═══════════════════════════════════════════════════════════════
# REQUEST / RESPONSE SCHEMAS  (Pydantic)
# ═══════════════════════════════════════════════════════════════
class OrganizationCreate(BaseModel):
    """Payload for creating a new organization."""
    display_name: str = Field(..., min_length=3, max_length=120)
    legal_structure: str = Field(..., description="osek_morshe | osek_patur | chevra_baam")
    tax_id: str = Field(..., description="9-digit Israeli ID, mod-11 checksum")
    business_address: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    industry_code: Optional[str] = None
    business_phone: Optional[str] = None
    business_email: Optional[str] = Field(default=None, pattern=_EMAIL_PATTERN)
    website: Optional[str] = None


class OrganizationOut(BaseModel):
    """Response shape for a single organization."""
    id: int
    display_name: str
    legal_structure: str
    tax_id: str
    tax_id_verified: bool
    kyc_status: str
    status: str
    created_at: str

    @classmethod
    def from_orm(cls, org: Organization) -> "OrganizationOut":  # type: ignore[override]
        return cls(
            id=org.id,
            display_name=org.display_name,
            legal_structure=org.legal_structure,
            tax_id=org.tax_id,
            tax_id_verified=bool(org.tax_id_verified),
            kyc_status=org.kyc_status or "pending",
            status=org.status or "active",
            created_at=org.created_at.isoformat() if org.created_at else "",
        )


class InvitationCreate(BaseModel):
    """Payload for creating an invitation."""
    role: str = Field(..., description="'employee' or 'accountant'")
    target_email: Optional[str] = Field(default=None, pattern=_EMAIL_PATTERN)
    target_phone_e164: Optional[str] = None
    display_name_hint: Optional[str] = None


class InvitationPublicOut(BaseModel):
    """
    PUBLIC view of an invitation — what the recipient sees on the
    accept page. Deliberately omits invited_by_user_id (we just show
    the inviter's display name to avoid PII leaks).
    """
    code: str
    role: str
    organization_display_name: str
    status: str
    expires_at: str


class TaxIdValidationOut(BaseModel):
    """Response for the live tax-id validation endpoint."""
    valid: bool
    normalized: str
    inferred_legal_structure: Optional[str]


# ═══════════════════════════════════════════════════════════════
# ENDPOINT: POST /api/v1/organizations
# ═══════════════════════════════════════════════════════════════
@router.post("/organizations", status_code=201)
def create_org_endpoint(
    payload: OrganizationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new organization. The current user becomes its owner.

    The user's role on the platform may be 'business_owner' or 'admin'.
    Accountants cannot create organizations directly — they receive
    invitations from owners (via POST /api/v1/organizations/{id}/invitations).
    """
    if current_user.role == "accountant":
        raise HTTPException(
            status_code=403,
            detail="Accountants cannot create organizations directly. "
                   "Owners must invite you instead.",
        )

    try:
        org = create_organization(
            display_name=payload.display_name,
            legal_structure=payload.legal_structure,
            tax_id=payload.tax_id,
            owner_user_id=current_user.id,
            db=db,
            business_address=payload.business_address,
            city=payload.city,
            postal_code=payload.postal_code,
            industry_code=payload.industry_code,
            business_phone=payload.business_phone,
            business_email=payload.business_email,
            website=payload.website,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return OrganizationOut.from_orm(org).dict()


# ═══════════════════════════════════════════════════════════════
# ENDPOINT: GET /api/v1/organizations
# ═══════════════════════════════════════════════════════════════
@router.get("/organizations")
def list_organizations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List organizations:
      - admin → ALL organizations
      - business_owner / employee → orgs they have Membership in
      - accountant → orgs they have an active AccountantEngagement for
    """
    if current_user.role == "admin":
        rows = db.query(Organization).order_by(Organization.created_at.desc()).all()
        return [OrganizationOut.from_orm(o).dict() for o in rows]

    ctx = resolve_user_context(current_user, db)
    org_ids = list(set(ctx["active_org_ids"] + ctx["accountant_of"]))
    if not org_ids:
        return []

    rows = (
        db.query(Organization)
        .filter(Organization.id.in_(org_ids))
        .order_by(Organization.created_at.desc())
        .all()
    )
    return [OrganizationOut.from_orm(o).dict() for o in rows]


# ═══════════════════════════════════════════════════════════════
# ENDPOINT: GET /api/v1/organizations/{organization_id}
# ═══════════════════════════════════════════════════════════════
@router.get("/organizations/{organization_id}")
def get_organization(
    organization_id: int,
    db: Session = Depends(get_db),
    # Read-level access (employee/accountant/owner/admin all OK).
    _user: User = Depends(require_org_access(min_role="employee")),
):
    """Read a single organization's profile."""
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return OrganizationOut.from_orm(org).dict()


# ═══════════════════════════════════════════════════════════════
# ENDPOINT: POST /api/v1/organizations/{organization_id}/invitations
# ═══════════════════════════════════════════════════════════════
@router.post("/organizations/{organization_id}/invitations", status_code=201)
def create_invitation_endpoint(
    organization_id: int,
    payload: InvitationCreate,
    db: Session = Depends(get_db),
    # OWNER level required — employees/accountants cannot invite others.
    current_user: User = Depends(require_org_access(min_role="owner")),
):
    """
    Create an invitation for someone to join this organization
    (as employee or accountant).

    The invitation code is returned in the response — the caller is
    responsible for delivering it via email / WhatsApp template / etc.
    """
    try:
        invitation = create_invitation(
            organization_id=organization_id,
            invited_by_user_id=current_user.id,
            role=payload.role,
            target_email=payload.target_email,
            target_phone_e164=payload.target_phone_e164,
            display_name_hint=payload.display_name_hint,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "id": invitation.id,
        "code": invitation.code,
        "role": invitation.role,
        "expires_at": invitation.expires_at.isoformat(),
        "status": invitation.status,
    }


# ═══════════════════════════════════════════════════════════════
# ENDPOINT: GET /api/v1/invitations/{code}     (PUBLIC)
# ═══════════════════════════════════════════════════════════════
@router.get("/invitations/{code}")
def get_invitation_public(
    code: str,
    db: Session = Depends(get_db),
):
    """
    Public lookup. The accept-invitation page calls this BEFORE the
    user is authenticated (so they can decide whether to sign up or
    log in). Deliberately leaks minimal info: org display_name + role.
    """
    invitation = get_invitation_by_code(code, db)
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")

    org = db.query(Organization).filter(Organization.id == invitation.organization_id).first()
    if not org:
        # Should not happen, but guard against orphaned invitations.
        raise HTTPException(status_code=404, detail="Organization not found")

    return InvitationPublicOut(
        code=invitation.code,
        role=invitation.role,
        organization_display_name=org.display_name,
        status=invitation.status,
        expires_at=invitation.expires_at.isoformat(),
    ).dict()


# ═══════════════════════════════════════════════════════════════
# ENDPOINT: POST /api/v1/invitations/{code}/accept
# ═══════════════════════════════════════════════════════════════
@router.post("/invitations/{code}/accept")
def accept_invitation_endpoint(
    code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Accept an invitation as the currently-authenticated user.

    Behavior depends on invitation.role:
      - 'employee'   → creates Membership(role='employee')
      - 'accountant' → creates AccountantEngagement(status='active')
                       and bumps user.role to 'accountant' if needed

    Returns the IDs of the created records so the frontend can route
    the user into the right post-acceptance flow.
    """
    try:
        result = accept_invitation(
            code=code,
            accepting_user_id=current_user.id,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


# ═══════════════════════════════════════════════════════════════
# ENDPOINT: GET /api/v1/me/context
# ═══════════════════════════════════════════════════════════════
@router.get("/me/context")
def get_me_context(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the current user's multi-tenant context. Used by the
    dashboard / accountant portal to know which orgs to display.

    Shape matches resolve_user_context(): active_org_ids, primary_org_id,
    role_per_org, accountant_of, plus a small profile block for header UI.
    """
    ctx = resolve_user_context(current_user, db)
    return {
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "full_name": current_user.full_name,
            "role": current_user.role,
            "language_pref": current_user.language_pref,
            "onboarding_status": current_user.onboarding_status,
        },
        "context": ctx,
    }


# ═══════════════════════════════════════════════════════════════
# ENDPOINT: GET /api/v1/utils/validate-tax-id?value=...
# ═══════════════════════════════════════════════════════════════
@router.get("/utils/validate-tax-id")
def validate_tax_id_endpoint(value: str = ""):
    """
    Live validation endpoint used by the onboarding wizard as the user
    types their Tax ID. Returns:
        {valid, normalized, inferred_legal_structure}

    PUBLIC by design — runs the same algorithm the user could run
    locally; no secret leakage. Convenient for UX.
    """
    from app.services.identity.tax_id import infer_legal_structure_from_tax_id
    normalized = normalize_tax_id(value)
    return TaxIdValidationOut(
        valid=validate_tax_id_israel(normalized),
        normalized=normalized,
        inferred_legal_structure=infer_legal_structure_from_tax_id(normalized),
    ).dict()
