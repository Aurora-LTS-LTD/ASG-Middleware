"""
ASG Solutions — Invitation Service
====================================
Sprint 1 of the Tax & Document Layer.

Owns the lifecycle of pending invitations:
  - create_invitation()    : owner invites someone (employee or accountant)
  - accept_invitation()    : invited person joins (creates Membership or AccountantEngagement)
  - expire_old_invitations(): scheduled cleanup (run periodically)
  - get_invitation_by_code(): lookup by URL code (public, used by accept-page)

INVITATION CODE:
  UUIDv4 (36 chars) — unguessable, opaque. Surfaces in:
  - Email links: https://app.asg.co.il/invitations/<code>/accept
  - WhatsApp template messages: "{accountant} invited you. Tap to confirm."
  - Accountant portal copy-paste flow

TTL:
  Default 72 hours. Configurable via INVITATION_TTL_HOURS env var.

REAL-WORLD ANALOGY:
  Like an invitation card with a unique RSVP code. The host (owner)
  writes one out and hands it to a courier (email or WhatsApp). The
  guest (invitee) brings the card to the door — the doorman scans
  the code, lets them in, and tears the card up so it can't be re-used.

SECURITY:
  - Single-use: status flips from 'pending' → 'accepted' atomically.
  - TTL-bound: expired codes are rejected.
  - Bound to organization at creation: an attacker who guesses a code
    can only join the specific org it was issued for.
  - No PII in the code itself (UUIDv4 has no embedded info).
"""

import datetime
import os
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.database import (
    Invitation,
    Organization,
    Membership,
    AccountantEngagement,
    User,
    ActionLog,
)
from app.services.identity.organization_service import add_membership


# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────
def _ttl_hours() -> int:
    """Read INVITATION_TTL_HOURS env var; default 72."""
    try:
        return int(os.getenv("INVITATION_TTL_HOURS", "72"))
    except ValueError:
        return 72


# ─────────────────────────────────────────────────────────────
# create_invitation
# ─────────────────────────────────────────────────────────────
def create_invitation(
    *,
    organization_id: int,
    invited_by_user_id: int,
    role: str,                                  # 'employee' | 'accountant'
    target_email: Optional[str] = None,
    target_phone_e164: Optional[str] = None,
    display_name_hint: Optional[str] = None,
    db: Session,
) -> Invitation:
    """
    Create a pending invitation. Either target_email or target_phone_e164
    MUST be provided (we need somewhere to send it).

    Raises ValueError on:
      - bad role
      - missing target
      - missing org / inviter
      - inviter not authorized to invite to this org

    Returns the created Invitation (committed).
    """
    role = (role or "").strip()
    if role not in ("employee", "accountant"):
        raise ValueError("role must be 'employee' or 'accountant'")

    if not target_email and not target_phone_e164:
        raise ValueError("must provide target_email or target_phone_e164")

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if not org:
        raise ValueError(f"organization_id={organization_id} not found")

    inviter = db.query(User).filter(User.id == invited_by_user_id, User.is_active == True).first()  # noqa: E712
    if not inviter:
        raise ValueError(f"invited_by_user_id={invited_by_user_id} not found or inactive")

    # Authorization: inviter must be admin OR an owner of the org.
    if inviter.role != "admin":
        membership = (
            db.query(Membership)
            .filter(
                Membership.user_id == inviter.id,
                Membership.organization_id == organization_id,
                Membership.role == "owner",
            )
            .first()
        )
        if not membership:
            raise ValueError(
                f"user {inviter.email} is not an owner of org {organization_id} "
                "and cannot invite"
            )

    code = str(uuid.uuid4())
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(hours=_ttl_hours())

    invitation = Invitation(
        code=code,
        organization_id=organization_id,
        invited_by_user_id=inviter.id,
        role=role,
        target_email=(target_email or "").strip().lower() or None,
        target_phone_e164=(target_phone_e164 or "").strip() or None,
        display_name_hint=display_name_hint,
        status="pending",
        expires_at=expires_at,
    )
    db.add(invitation)

    db.add(ActionLog(
        business_id=org.legacy_business_id,
        status="invitation.created",
        detail=(
            f"invitation code={code[:8]}... role={role} org_id={organization_id} "
            f"invited_by={inviter.email} target={(target_email or target_phone_e164)[:6]}..."
        ),
    ))
    db.commit()
    db.refresh(invitation)

    print(
        f"[INV_SVC] ✅ Invitation created: code={code[:8]}... "
        f"role={role} org_id={organization_id}"
    )
    return invitation


# ─────────────────────────────────────────────────────────────
# get_invitation_by_code
# ─────────────────────────────────────────────────────────────
def get_invitation_by_code(code: str, db: Session) -> Optional[Invitation]:
    """
    Lookup an invitation by its public code. Returns None if not found.

    NOTE: returns the row even if expired/accepted/revoked — caller is
    responsible for status checks. This lets the UI distinguish
    'never existed' from 'expired' (different error messages).
    """
    if not code:
        return None
    return db.query(Invitation).filter(Invitation.code == code).first()


# ─────────────────────────────────────────────────────────────
# accept_invitation
# ─────────────────────────────────────────────────────────────
def accept_invitation(
    *,
    code: str,
    accepting_user_id: int,
    db: Session,
) -> dict:
    """
    Accept an invitation as `accepting_user_id`.

    BEHAVIOR:
      - role='employee'   → creates a Membership(role='employee')
      - role='accountant' → creates an AccountantEngagement(status='active')
      - sets invitation.status='accepted', accepted_at, accepted_by_user_id

    Returns:
        {
          "invitation_id": int,
          "organization_id": int,
          "role": str,
          "membership_id": int | None,
          "engagement_id": int | None,
        }

    Raises ValueError if:
      - code is unknown
      - invitation already accepted/expired/revoked
      - now > expires_at
      - accepting user is the same as the inviter (anti-self-invite)
    """
    invitation = get_invitation_by_code(code, db)
    if not invitation:
        raise ValueError("Invitation code not found")

    now = datetime.datetime.utcnow()

    if invitation.status != "pending":
        raise ValueError(f"Invitation is already {invitation.status}")

    if invitation.expires_at < now:
        invitation.status = "expired"
        db.commit()
        raise ValueError("Invitation has expired")

    accepting_user = db.query(User).filter(
        User.id == accepting_user_id, User.is_active == True  # noqa: E712
    ).first()
    if not accepting_user:
        raise ValueError(f"accepting_user_id={accepting_user_id} not found or inactive")

    if invitation.invited_by_user_id == accepting_user.id:
        raise ValueError("Cannot accept your own invitation")

    org = db.query(Organization).filter(Organization.id == invitation.organization_id).first()
    if not org:
        raise ValueError(f"organization_id={invitation.organization_id} not found")

    result = {
        "invitation_id": invitation.id,
        "organization_id": org.id,
        "role": invitation.role,
        "membership_id": None,
        "engagement_id": None,
    }

    # ── Branch by role ──
    if invitation.role == "employee":
        membership = add_membership(
            user_id=accepting_user.id,
            organization_id=org.id,
            role="employee",
            invited_by_user_id=invitation.invited_by_user_id,
            invitation_id=invitation.id,
            db=db,
        )
        result["membership_id"] = membership.id

    elif invitation.role == "accountant":
        # Promote user.role to 'accountant' if not already
        if accepting_user.role not in ("accountant", "admin"):
            accepting_user.role = "accountant"
            db.flush()

        # Idempotency check — re-accepting an existing engagement no-ops
        existing = (
            db.query(AccountantEngagement)
            .filter(
                AccountantEngagement.accountant_user_id == accepting_user.id,
                AccountantEngagement.organization_id == org.id,
                AccountantEngagement.status == "active",
            )
            .first()
        )
        if existing:
            engagement = existing
        else:
            engagement = AccountantEngagement(
                accountant_user_id=accepting_user.id,
                organization_id=org.id,
                status="active",
                revenue_share_pct=20.0,   # default; configurable per-deal later
                activated_at=now,
            )
            db.add(engagement)
            db.flush()
        result["engagement_id"] = engagement.id

    else:
        raise ValueError(f"Unknown invitation role: {invitation.role}")

    # ── Mark invitation accepted ──
    invitation.status = "accepted"
    invitation.accepted_at = now
    invitation.accepted_by_user_id = accepting_user.id

    db.add(ActionLog(
        business_id=org.legacy_business_id,
        status="invitation.accepted",
        detail=(
            f"invitation_id={invitation.id} role={invitation.role} "
            f"accepted_by={accepting_user.email} org_id={org.id}"
        ),
    ))

    db.commit()
    print(
        f"[INV_SVC] ✅ Invitation accepted: id={invitation.id} role={invitation.role} "
        f"by={accepting_user.email}"
    )
    return result


# ─────────────────────────────────────────────────────────────
# expire_old_invitations  (scheduled cleanup)
# ─────────────────────────────────────────────────────────────
def expire_old_invitations(db: Session) -> int:
    """
    Scan for invitations past their expires_at and flip them to
    status='expired'. Returns count of rows updated.

    Should be run periodically (e.g. once per hour from a Cloud Scheduler
    cron in production). Currently called manually or by tests.
    """
    now = datetime.datetime.utcnow()
    rows = (
        db.query(Invitation)
        .filter(
            Invitation.status == "pending",
            Invitation.expires_at < now,
        )
        .all()
    )
    for inv in rows:
        inv.status = "expired"
    if rows:
        db.commit()
        print(f"[INV_SVC] Expired {len(rows)} stale invitations")
    return len(rows)
