"""
Aurora LTS — Admin Users Router (Track 4 — aurora-admin-ui backend)
====================================================================
Two endpoints feeding the Glass Fintech admin dashboard:

  GET /api/v1/admin/users
       Paginated list of all users with their primary organization
       and KYC status. Used by the User Table on the dashboard.

  GET /api/v1/admin/organizations
       List of all organizations with member counts. Used by the
       Summary Cards (Active Organizations metric) and the org list
       page.

Both endpoints are IAP-gated via Depends(require_admin) — they accept:
  - IAP-authenticated admin JWTs (aurora-admin-ui proxy path)
  - Break-glass JWTs (Track 3 emergency access)

Response shapes match the contract documented in the aurora-admin-ui
plan; columns are picked to fit the User Table component directly.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import (
    get_db,
    User,
    Membership,
    Organization,
)
from app.middleware.auth_middleware import require_admin


router = APIRouter(prefix="/api/v1/admin", tags=["admin-users"])


# ─────────────────────────────────────────────────────────────
# GET /api/v1/admin/users
# ─────────────────────────────────────────────────────────────
@router.get("/users")
def list_users(
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(50, ge=1, le=200, description="rows per page"),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Paginated user list, joined to the user's primary org + KYC."""

    total = db.query(func.count(User.id)).scalar() or 0

    # LEFT JOIN to the user's primary Membership → Organization. A user
    # without an org (e.g., bootstrap admin) still shows up with null
    # org fields.
    primary_mem = (
        db.query(Membership)
        .filter(Membership.is_primary == True)  # noqa: E712 (SQLAlchemy idiom)
        .subquery()
    )

    rows = (
        db.query(
            User.id,
            User.email,
            User.full_name,
            User.role,
            User.is_active,
            User.whatsapp_phone_e164,
            User.created_at,
            primary_mem.c.organization_id.label("primary_org_id"),
            Organization.display_name.label("primary_org_name"),
            Organization.kyc_status.label("kyc_status"),
            Organization.legal_structure.label("legal_structure"),
        )
        .outerjoin(primary_mem, primary_mem.c.user_id == User.id)
        .outerjoin(Organization, Organization.id == primary_mem.c.organization_id)
        .order_by(User.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    users = []
    for r in rows:
        users.append({
            "id": r.id,
            "email": r.email,
            "full_name": r.full_name,
            "role": r.role,
            "is_active": bool(r.is_active),
            "whatsapp_phone_e164": r.whatsapp_phone_e164,
            "primary_org_id": r.primary_org_id,
            "primary_org_name": r.primary_org_name,
            "kyc_status": r.kyc_status or "n/a",
            "legal_structure": r.legal_structure,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "users": users,
    }


# ─────────────────────────────────────────────────────────────
# GET /api/v1/admin/organizations
# ─────────────────────────────────────────────────────────────
@router.get("/organizations")
def list_organizations(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """List of organizations with member counts."""

    total = db.query(func.count(Organization.id)).scalar() or 0

    # Aggregate member count per org in a subquery for efficiency.
    member_counts = (
        db.query(
            Membership.organization_id.label("org_id"),
            func.count(Membership.id).label("member_count"),
        )
        .group_by(Membership.organization_id)
        .subquery()
    )

    rows = (
        db.query(
            Organization.id,
            Organization.display_name,
            Organization.legal_structure,
            Organization.tax_id,
            Organization.kyc_status,
            Organization.status,
            Organization.created_at,
            func.coalesce(member_counts.c.member_count, 0).label("member_count"),
        )
        .outerjoin(member_counts, member_counts.c.org_id == Organization.id)
        .order_by(Organization.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    orgs = []
    for r in rows:
        orgs.append({
            "id": r.id,
            "display_name": r.display_name,
            "legal_structure": r.legal_structure,
            "tax_id": r.tax_id,
            "kyc_status": r.kyc_status or "pending",
            "status": r.status or "active",
            "member_count": int(r.member_count or 0),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "organizations": orgs,
    }
