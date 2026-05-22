"""
ASG Solutions — Organization Service
======================================
Sprint 1 of the Tax & Document Layer.

Owns:
  - Creating organizations (called from web onboarding + WhatsApp ONBOARDING:* FSM)
  - Managing memberships (User <-> Organization with a role)
  - Resolving the user's "context" — which orgs can they access, in what role,
    and as accountant for which orgs

REAL-WORLD ANALOGY:
  This is the HR department of the system. It knows who works for which
  company, in what role, and who is allowed in which room.

DESIGN PRINCIPLE — "least privilege by default":
  resolve_user_context() returns ONLY orgs the user is currently active in.
  No "show me everything" mode here; that lives in admin endpoints behind
  require_admin().
"""

import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.database import (
    Business,
    Organization,
    Membership,
    AccountantEngagement,
    User,
    ActionLog,
)
from app.services.identity.tax_id import (
    validate_tax_id_israel,
    normalize_tax_id,
    infer_legal_structure_from_tax_id,
)


# ─────────────────────────────────────────────────────────────
# create_organization
# ─────────────────────────────────────────────────────────────
def create_organization(
    *,
    display_name: str,
    legal_structure: str,
    tax_id: str,
    owner_user_id: int,
    db: Session,
    # Optional fields (may be filled later in onboarding)
    business_address: Optional[str] = None,
    city: Optional[str] = None,
    postal_code: Optional[str] = None,
    industry_code: Optional[str] = None,
    business_phone: Optional[str] = None,
    business_email: Optional[str] = None,
    website: Optional[str] = None,
    # Compatibility bridge: keep a paired Business row alive during expand/contract
    create_legacy_business: bool = True,
) -> Organization:
    """
    Create a new Organization and immediately attach the owner User as
    role='owner', is_primary=True (unless the user already has another
    primary org, in which case the new one is non-primary).

    VALIDATION:
      - display_name required, ≥3 chars after strip
      - legal_structure must be one of {osek_morshe, osek_patur, chevra_baam}
      - tax_id must pass the Israeli mod-11 checksum
      - owner_user_id must exist and be active

    EXPAND/CONTRACT BRIDGE:
      During the migration window we ALSO create a paired Business row
      (legacy schema) so that legacy code paths reading from `businesses`
      continue to work. Organization.legacy_business_id points to the
      Business row. This dual-write is removed in Sprint 5.

    Raises ValueError on invalid input.
    Returns the created Organization (committed).
    """
    # ── Input validation ──
    if not display_name or len(display_name.strip()) < 3:
        raise ValueError("display_name must be at least 3 characters")

    legal_structure = (legal_structure or "").strip()
    if legal_structure not in ("osek_morshe", "osek_patur", "chevra_baam"):
        raise ValueError(
            "legal_structure must be 'osek_morshe', 'osek_patur', or 'chevra_baam'"
        )

    normalized_tax_id = normalize_tax_id(tax_id)
    if not validate_tax_id_israel(normalized_tax_id):
        raise ValueError("tax_id failed Israeli mod-11 checksum")

    owner = db.query(User).filter(User.id == owner_user_id, User.is_active == True).first()  # noqa: E712
    if not owner:
        raise ValueError(f"owner_user_id={owner_user_id} not found or inactive")

    # ── Optional sanity check: legal_structure vs tax_id first-digit ──
    # If the inferred structure disagrees, log a warning but accept the
    # user's choice. (Edge cases exist; the user knows their own entity.)
    inferred = infer_legal_structure_from_tax_id(normalized_tax_id)
    if inferred and inferred != legal_structure:
        print(
            f"[ORG_SVC] ℹ️ tax_id {normalized_tax_id[:3]}***{normalized_tax_id[-2:]} "
            f"hints at '{inferred}' but user chose '{legal_structure}' — accepting"
        )

    # ── Dual-write: create paired Business (legacy) if requested ──
    legacy_business = None
    if create_legacy_business:
        legacy_business = Business(
            name=display_name.strip(),
            phone=business_phone,
            tax_id=normalized_tax_id,
            address=business_address,
            status="active",
        )
        db.add(legacy_business)
        db.flush()  # populate legacy_business.id without committing yet

    # ── Create the Organization ──
    org = Organization(
        display_name=display_name.strip(),
        legal_structure=legal_structure,
        tax_id=normalized_tax_id,
        tax_id_verified=False,
        business_address=business_address,
        city=city,
        postal_code=postal_code,
        industry_code=industry_code,
        business_phone=business_phone,
        business_email=business_email,
        website=website,
        kyc_status="pending",
        status="active",
        legacy_business_id=legacy_business.id if legacy_business else None,
    )
    db.add(org)
    db.flush()

    # ── Determine primary-status of this new membership ──
    has_existing_primary = (
        db.query(Membership)
        .filter(Membership.user_id == owner.id, Membership.is_primary == True)  # noqa: E712
        .first()
        is not None
    )

    membership = Membership(
        user_id=owner.id,
        organization_id=org.id,
        role="owner",
        is_primary=not has_existing_primary,
    )
    db.add(membership)

    # ── Update the legacy User.business_id pointer (dual-write) ──
    # Only set if the user has no existing business_id, OR the new org is
    # marked as primary. This preserves backward-compat for legacy queries.
    if legacy_business and (not owner.business_id or membership.is_primary):
        owner.business_id = legacy_business.id

    # ── Audit trail ──
    db.add(ActionLog(
        business_id=legacy_business.id if legacy_business else None,
        status="org.created",
        detail=(
            f"organization id={org.id} display_name={org.display_name!r} "
            f"legal_structure={legal_structure} owner_user_id={owner.id}"
        ),
    ))

    db.commit()
    db.refresh(org)

    print(
        f"[ORG_SVC] ✅ Created Organization id={org.id} "
        f"name={org.display_name!r} owner={owner.email}"
    )
    return org


# ─────────────────────────────────────────────────────────────
# add_membership
# ─────────────────────────────────────────────────────────────
def add_membership(
    *,
    user_id: int,
    organization_id: int,
    role: str = "employee",
    invited_by_user_id: Optional[int] = None,
    invitation_id: Optional[int] = None,
    db: Session,
) -> Membership:
    """
    Add a user to an organization with a given role.

    role must be 'owner' or 'employee'. (Accountants are NOT memberships;
    they go through AccountantEngagement.)

    Idempotent: if the membership already exists, returns it unchanged.

    Raises ValueError on invalid role / missing org / missing user.
    """
    role = (role or "").strip()
    if role not in ("owner", "employee"):
        raise ValueError("role must be 'owner' or 'employee'")

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()  # noqa: E712
    if not user:
        raise ValueError(f"user_id={user_id} not found or inactive")

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if not org:
        raise ValueError(f"organization_id={organization_id} not found")

    existing = (
        db.query(Membership)
        .filter(Membership.user_id == user_id, Membership.organization_id == organization_id)
        .first()
    )
    if existing:
        return existing

    has_primary = (
        db.query(Membership)
        .filter(Membership.user_id == user_id, Membership.is_primary == True)  # noqa: E712
        .first()
        is not None
    )

    membership = Membership(
        user_id=user_id,
        organization_id=organization_id,
        role=role,
        is_primary=not has_primary,
        invited_by_user_id=invited_by_user_id,
        invitation_id=invitation_id,
    )
    db.add(membership)

    db.add(ActionLog(
        business_id=org.legacy_business_id,
        status="membership.added",
        detail=(
            f"membership user_id={user_id} org_id={organization_id} role={role}"
        ),
    ))
    db.commit()
    db.refresh(membership)
    return membership


# ─────────────────────────────────────────────────────────────
# resolve_user_context
# ─────────────────────────────────────────────────────────────
def resolve_user_context(user: User, db: Session) -> dict:
    """
    Compute the full multi-tenant context for a user.

    Returns:
        {
          "user_id":          int,
          "role":             "admin" | "business_owner" | "accountant" | "employee",
          "active_org_ids":   [int, ...],     # orgs where the user has a membership
          "primary_org_id":   int | None,     # the user's "default" org
          "role_per_org":     {org_id: "owner"|"employee", ...},
          "accountant_of":    [int, ...],     # orgs where user is an active accountant
        }

    Used by:
      - JWT issuance (`active_org_ids` becomes a claim)
      - Web onboarding state lookup
      - require_org_access middleware (Sprint 1.4)
      - Accountant portal book listing (Sprint 4)
    """
    memberships = (
        db.query(Membership).filter(Membership.user_id == user.id).all()
    )
    role_per_org = {m.organization_id: m.role for m in memberships}
    active_org_ids = list(role_per_org.keys())

    primary = next((m for m in memberships if m.is_primary), None)
    primary_org_id = primary.organization_id if primary else (
        active_org_ids[0] if active_org_ids else None
    )

    accountant_engagements = (
        db.query(AccountantEngagement)
        .filter(
            AccountantEngagement.accountant_user_id == user.id,
            AccountantEngagement.status == "active",
        )
        .all()
    )
    accountant_of = [e.organization_id for e in accountant_engagements]

    return {
        "user_id": user.id,
        "role": user.role,
        "active_org_ids": active_org_ids,
        "primary_org_id": primary_org_id,
        "role_per_org": role_per_org,
        "accountant_of": accountant_of,
    }


# ─────────────────────────────────────────────────────────────
# user_can_access_org
# ─────────────────────────────────────────────────────────────
def user_can_access_org(
    user: User,
    organization_id: int,
    db: Session,
    *,
    min_role: str = "employee",
) -> bool:
    """
    True iff `user` has access to `organization_id` at >= min_role,
    or is an active accountant for that org, or is a global admin.

    Role rank: admin > owner > employee > accountant > none.

    Note: 'accountant' is a special path (via AccountantEngagement),
    not a Membership role. We handle it explicitly: an accountant
    has read-level access regardless of `min_role` setting.
    """
    if user.role == "admin":
        return True

    role_rank = {"employee": 1, "owner": 2, "admin": 3}
    required = role_rank.get(min_role, 0)

    membership = (
        db.query(Membership)
        .filter(
            Membership.user_id == user.id,
            Membership.organization_id == organization_id,
        )
        .first()
    )
    if membership:
        actual_rank = role_rank.get(membership.role, 0)
        if actual_rank >= required:
            return True

    # Accountant fallback path — read-only access to engaged orgs
    if min_role == "employee":
        engagement = (
            db.query(AccountantEngagement)
            .filter(
                AccountantEngagement.accountant_user_id == user.id,
                AccountantEngagement.organization_id == organization_id,
                AccountantEngagement.status == "active",
            )
            .first()
        )
        if engagement:
            return True

    return False


# ─────────────────────────────────────────────────────────────
# get_primary_org
# ─────────────────────────────────────────────────────────────
def get_primary_org(user: User, db: Session) -> Optional[Organization]:
    """
    Return the user's primary organization (their "default" tenant).
    None if they have no memberships yet.

    Used by the dashboard when no explicit org_id is in the URL.
    """
    primary = (
        db.query(Membership)
        .filter(Membership.user_id == user.id, Membership.is_primary == True)  # noqa: E712
        .first()
    )
    if not primary:
        # Fall back to first membership by created_at (deterministic)
        primary = (
            db.query(Membership)
            .filter(Membership.user_id == user.id)
            .order_by(Membership.created_at.asc())
            .first()
        )
    if not primary:
        return None
    return db.query(Organization).filter(Organization.id == primary.organization_id).first()


# ─────────────────────────────────────────────────────────────
# get_or_create_organization_for_business   (Sprint 1.8 — Dual-write Audit)
# ─────────────────────────────────────────────────────────────
def get_or_create_organization_for_business(
    business_id: int,
    db: Session,
) -> Organization:
    """
    EXPAND/CONTRACT MIGRATION HELPER.

    Given the id of a legacy `Business` row, return the paired
    `Organization`. If the pairing doesn't exist yet (because the
    Business was created by a legacy code path that didn't dual-write),
    create the Organization on the fly and return it.

    IDEMPOTENT — calling it twice on the same Business returns the
    same Organization. Safe to inject at every legacy mutation site.

    INFERENCE RULES (mirrors migrate_phase6._backfill_organizations):
      - legal_structure: 'chevra_baam' if tax_id is 9 digits starting
        with '5'; 'osek_morshe' otherwise (safe default)
      - tax_id: copied from Business.tax_id; if missing, sentinel
        "BACKFILL-{business_id}" (auditable absence, not silent)

    THIS IS A WRITE. The caller must own the transaction — we
    `db.flush()` so the new row is visible within the transaction
    but do NOT `db.commit()`.

    Raises ValueError if business_id is not found.
    """
    biz = db.query(Business).filter(Business.id == business_id).first()
    if not biz:
        raise ValueError(f"business_id={business_id} not found")

    existing = (
        db.query(Organization)
        .filter(Organization.legacy_business_id == biz.id)
        .first()
    )
    if existing:
        return existing

    # ── Infer legal_structure from tax_id ──
    inferred = "osek_morshe"
    tax_id_value = (biz.tax_id or "").strip()
    if tax_id_value and tax_id_value.startswith("5") and len(tax_id_value) == 9:
        inferred = "chevra_baam"
    org_tax_id = tax_id_value or f"BACKFILL-{biz.id}"

    org = Organization(
        display_name=biz.name,
        legal_structure=inferred,
        tax_id=org_tax_id,
        tax_id_verified=False,
        business_address=biz.address,
        business_phone=biz.phone,
        kyc_status="pending",
        status=biz.status or "active",
        portal_token=biz.portal_token,
        legacy_business_id=biz.id,
        created_at=biz.created_at or datetime.datetime.utcnow(),
    )
    db.add(org)
    db.flush()

    db.add(ActionLog(
        business_id=biz.id,
        status="org.backfilled_at_runtime",
        detail=(
            f"organization id={org.id} backfilled from existing "
            f"business_id={biz.id} (dual-write audit)"
        ),
    ))

    print(
        f"[ORG_SVC] 🔧 Runtime-backfilled Organization id={org.id} "
        f"for legacy Business id={biz.id} (name={biz.name!r})"
    )
    return org
