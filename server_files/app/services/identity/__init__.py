"""
ASG Solutions — Identity Services Package
============================================
Sprint 1 of the 12-week Tax & Document Layer roadmap.

This package owns everything related to:
  - Organizations (the legal entity that owns invoices, expenses, subscriptions)
  - Memberships (User <-> Organization with a role)
  - Accountant Engagements (external CPAs advising organizations)
  - Invitations (pending invites for employees/accountants)
  - Pairing codes (generalized: WhatsApp, Telegram, future SMS)
  - Israeli tax-ID validation (the 9-digit mod-11 checksum)

Public re-exports for ergonomic imports elsewhere:
    from app.services.identity import (
        validate_tax_id_israel,
        create_organization,
        add_membership,
        resolve_user_context,
        create_invitation,
        accept_invitation,
        generate_pairing_code,
        verify_pairing_code,
    )
"""

from app.services.identity.tax_id import (
    validate_tax_id_israel,
    infer_legal_structure_from_tax_id,
    normalize_tax_id,
)
from app.services.identity.organization_service import (
    create_organization,
    add_membership,
    resolve_user_context,
    user_can_access_org,
    get_primary_org,
    get_or_create_organization_for_business,
)
from app.services.identity.invitation_service import (
    create_invitation,
    accept_invitation,
    expire_old_invitations,
    get_invitation_by_code,
)
from app.services.identity.pairing import (
    generate_pairing_code,
    verify_pairing_code,
    PAIRING_CODE_TTL_MINUTES,
)

__all__ = [
    # Tax ID
    "validate_tax_id_israel",
    "infer_legal_structure_from_tax_id",
    "normalize_tax_id",
    # Organization
    "create_organization",
    "add_membership",
    "resolve_user_context",
    "user_can_access_org",
    "get_primary_org",
    "get_or_create_organization_for_business",  # Sprint 1.8 dual-write helper
    # Invitations
    "create_invitation",
    "accept_invitation",
    "expire_old_invitations",
    "get_invitation_by_code",
    # Pairing
    "generate_pairing_code",
    "verify_pairing_code",
    "PAIRING_CODE_TTL_MINUTES",
]
