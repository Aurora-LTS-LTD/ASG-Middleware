"""
ASG / Aurora Solutions — Onboarding Orchestrator
====================================================
The state-machine that drives the multi-step onboarding wizard.
Owns the transitions between wizard steps and the final activate()
transaction that commits the Organization, Membership, Subscription,
and PaymentMethod rows atomically.

STATE MACHINE:

    +------------+     identity submitted
    | identity   | -----------------------------+
    +------------+                              v
                                          +-------------+
                                          | phone_otp   |
                                          +-------------+
                                                |  phone verified
                                                v
                                          +-------------+
                                          | email_otp   |
                                          +-------------+
                                                |  email verified
                                                v
                                          +-------------+
                                          | documents   |
                                          +-------------+
                                                |  all required docs uploaded
                                                v
                                          +-------------+
                                          |    plan     |
                                          +-------------+
                                                |  plan + cycle chosen
                                                v
                                          +-------------+
                                          | payment_    |
                                          | method      |
                                          +-------------+
                                                |  card / direct-debit tokenized
                                                v
                                          +-------------+
                                          | review      | (final summary + T&C)
                                          +-------------+
                                                |  user clicks Activate
                                                v
                                          +-------------+
                                          |   active    |
                                          +-------------+

  Any step can transition to 'abandoned' (timeout / explicit cancel).

ATOMICITY OF activate():
  All commits inside one transaction so a partial failure leaves the
  user able to retry from the review step. Specifically:
    - Organization created (with KYC docs back-linked to it)
    - Membership(role='owner', is_primary=True) created
    - Subscription(status='trialing') created
    - SubscriptionPayment(status='scheduled') created
    - User.onboarding_status='active'
    - OnboardingState.current_step='active'
    - ActionLog rows for every change (KYC dossier)

REUSE:
  - create_organization() from app.services.identity → Organization + Membership + dual-write to legacy Business
  - create_subscription() + schedule_first_charge() from this package
  - invoice_service is NOT called on activation (trial period); it's called later by the scheduled-charge worker on the first successful charge.
"""

import datetime
import json
import os
from typing import Optional

from sqlalchemy.orm import Session

from app.database import (
    OnboardingState,
    User,
    Organization,
    PaymentMethod,
    KycDocument,
    ActionLog,
)
from app.services.identity import (
    create_organization,
    validate_tax_id_israel,
    normalize_tax_id,
)
from app.services.onboarding.kyc_service import REQUIRED_DOC_TYPES_BY_LEGAL_STRUCTURE
from app.services.onboarding.subscription_service import (
    create_subscription,
    schedule_first_charge,
    compute_plan_amount,
)


# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────
ONBOARDING_TTL_DAYS = 30


class OnboardingError(Exception):
    """Raised when a state transition isn't allowed or input is invalid."""


# ─────────────────────────────────────────────────────────────
# State helpers
# ─────────────────────────────────────────────────────────────
STEP_ORDER = [
    "identity",
    "phone_otp",
    "email_otp",
    "documents",
    "plan",
    "payment_method",
    "review",
    "active",
]


def _load_payload(state: OnboardingState) -> dict:
    if not state.draft_payload:
        return {}
    try:
        return json.loads(state.draft_payload)
    except Exception:
        return {}


def _save_payload(state: OnboardingState, payload: dict) -> None:
    state.draft_payload = json.dumps(payload, ensure_ascii=False)


def _load_completed(state: OnboardingState) -> list[str]:
    if not state.completed_steps:
        return []
    try:
        return json.loads(state.completed_steps)
    except Exception:
        return []


def _save_completed(state: OnboardingState, completed: list[str]) -> None:
    state.completed_steps = json.dumps(completed, ensure_ascii=False)


def _advance_step(state: OnboardingState, just_finished: str) -> str:
    """Mark `just_finished` as completed and move current_step to the next."""
    completed = _load_completed(state)
    if just_finished not in completed:
        completed.append(just_finished)
    _save_completed(state, completed)

    try:
        idx = STEP_ORDER.index(state.current_step)
    except ValueError:
        idx = STEP_ORDER.index(just_finished) if just_finished in STEP_ORDER else 0

    next_idx = min(idx + 1, len(STEP_ORDER) - 1)
    state.current_step = STEP_ORDER[next_idx]
    return state.current_step


# ─────────────────────────────────────────────────────────────
# start_onboarding
# ─────────────────────────────────────────────────────────────
def start_onboarding(
    *,
    user_id: int,
    surface: str = "web",
    db: Session,
) -> OnboardingState:
    """
    Bootstrap an OnboardingState for the given user. Idempotent: if a
    state already exists (and isn't expired), returns it.
    """
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()  # noqa: E712
    if not user:
        raise OnboardingError(f"user_id={user_id} not found or inactive")

    state = (
        db.query(OnboardingState)
        .filter(OnboardingState.user_id == user_id)
        .first()
    )
    now = datetime.datetime.utcnow()
    if state and state.expires_at > now:
        return state

    if state and state.expires_at <= now:
        # Expired — clear and start fresh
        db.delete(state)
        db.flush()

    state = OnboardingState(
        user_id=user_id,
        current_step="identity",
        completed_steps="[]",
        draft_payload="{}",
        surface=surface,
        expires_at=now + datetime.timedelta(days=ONBOARDING_TTL_DAYS),
    )
    db.add(state)

    # Sync the user's status to the new flow state
    user.onboarding_status = "identity"

    db.add(ActionLog(
        business_id=None,
        status="onboarding.started",
        detail=f"user_id={user_id} surface={surface} state_id={state.id}",
    ))
    db.commit()
    db.refresh(state)
    return state


# ─────────────────────────────────────────────────────────────
# advance_onboarding
# ─────────────────────────────────────────────────────────────
def advance_onboarding(
    *,
    user_id: int,
    step: str,
    payload: dict,
    db: Session,
) -> OnboardingState:
    """
    Persist a step's data into draft_payload and move the wizard forward.

    Validation per step:
      - 'identity'     : first/last name + legal_structure + tax_id (mod-11 valid)
      - 'phone_otp'    : User.phone_verified_at populated by verify_otp() before this
      - 'email_otp'    : User.email_verified_at populated by verify_otp() before this
      - 'documents'    : every required doc for the legal_structure exists with status != rejected
      - 'plan'         : plan + billing_cycle pass compute_plan_amount() validation
      - 'payment_method': payment_method_id exists and belongs to the future org
      - 'review'       : T&C + Privacy accepted

    Returns the updated OnboardingState.
    """
    state = (
        db.query(OnboardingState)
        .filter(OnboardingState.user_id == user_id)
        .first()
    )
    if not state:
        raise OnboardingError("Onboarding has not been started for this user")
    if state.expires_at < datetime.datetime.utcnow():
        raise OnboardingError("Onboarding session has expired — please start over")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise OnboardingError(f"user_id={user_id} not found")

    draft = _load_payload(state)

    # ── Step-specific validation ──
    if step == "identity":
        first = (payload.get("first_name") or "").strip()
        last = (payload.get("last_name") or "").strip()
        legal = (payload.get("legal_structure") or "").strip()
        tax_id_raw = (payload.get("tax_id") or "").strip()
        biz_name = (payload.get("display_name") or "").strip()

        if len(first) < 2:
            raise OnboardingError("first_name is required (≥2 chars)")
        if len(last) < 2:
            raise OnboardingError("last_name is required (≥2 chars)")
        if legal not in ("osek_morshe", "osek_patur", "chevra_baam"):
            raise OnboardingError(
                "legal_structure must be 'osek_morshe', 'osek_patur', or 'chevra_baam'"
            )
        normalized_tax = normalize_tax_id(tax_id_raw)
        if not validate_tax_id_israel(normalized_tax):
            raise OnboardingError("Tax ID failed Israeli mod-11 checksum")
        if len(biz_name) < 3:
            raise OnboardingError("display_name (business name) is required (≥3 chars)")

        # Persist to User immediately so other surfaces (WhatsApp deep link)
        # see consistent identity data.
        user.first_name = first
        user.last_name = last
        if not user.full_name or user.full_name == user.email:
            user.full_name = f"{first} {last}".strip()

        # Persist optional contact fields if present
        if payload.get("fax"):
            user.fax = payload.get("fax")[:40]

        # Stash everything in the draft for activate()
        draft["identity"] = {
            "first_name": first,
            "last_name": last,
            "legal_structure": legal,
            "tax_id": normalized_tax,
            "display_name": biz_name,
            "business_address": payload.get("business_address"),
            "city": payload.get("city"),
            "postal_code": payload.get("postal_code"),
            "industry_code": payload.get("industry_code"),
            "business_phone": payload.get("business_phone"),
            "business_email": payload.get("business_email"),
            "website": payload.get("website"),
        }

    elif step == "phone_otp":
        if not user.phone_verified_at:
            raise OnboardingError("Phone has not been verified yet")
        draft["phone_e164"] = payload.get("phone_e164") or draft.get("phone_e164")

    elif step == "email_otp":
        if not user.email_verified_at:
            raise OnboardingError("Email has not been verified yet")

    elif step == "documents":
        legal = (draft.get("identity", {}) or {}).get("legal_structure")
        if not legal:
            raise OnboardingError("Complete the identity step before uploading documents")
        required_types = REQUIRED_DOC_TYPES_BY_LEGAL_STRUCTURE.get(legal, [])
        present = (
            db.query(KycDocument)
            .filter(
                KycDocument.user_id == user_id,
                KycDocument.document_type.in_(required_types),
                KycDocument.status.in_(("pending_review", "approved")),
            )
            .all()
        )
        present_types = {d.document_type for d in present}
        missing = [t for t in required_types if t not in present_types]
        if missing:
            raise OnboardingError(
                f"Missing required documents for legal_structure={legal}: {missing}"
            )
        draft["documents_uploaded"] = sorted(present_types)

    elif step == "plan":
        plan = (payload.get("plan") or "").strip()
        cycle = (payload.get("billing_cycle") or "").strip()
        # compute_plan_amount raises on bad input — re-raise as OnboardingError
        try:
            pricing = compute_plan_amount(plan, cycle)
        except ValueError as e:
            raise OnboardingError(str(e))
        draft["plan"] = plan
        draft["billing_cycle"] = cycle
        draft["pricing"] = pricing

    elif step == "payment_method":
        pm_id = payload.get("payment_method_id")
        if not pm_id:
            raise OnboardingError("payment_method_id is required")
        pm = db.query(PaymentMethod).filter(PaymentMethod.id == pm_id).first()
        if not pm:
            raise OnboardingError(f"payment_method_id={pm_id} not found")
        draft["payment_method_id"] = pm.id

    elif step == "review":
        if not payload.get("terms_accepted"):
            raise OnboardingError("Terms of Service must be accepted")
        if not payload.get("privacy_accepted"):
            raise OnboardingError("Privacy Notice must be accepted")
        # Stamp the versioned consent on the User
        version = payload.get("version") or datetime.datetime.utcnow().strftime("%Y-%m")
        now = datetime.datetime.utcnow()
        user.terms_accepted_version = version
        user.terms_accepted_at = now
        user.privacy_accepted_version = version
        user.privacy_accepted_at = now
        draft["consent_version"] = version

    else:
        raise OnboardingError(f"Unknown step: {step}")

    _save_payload(state, draft)
    next_step = _advance_step(state, just_finished=step)
    user.onboarding_status = next_step

    db.add(ActionLog(
        business_id=None,
        status=f"onboarding.advanced.{step}",
        detail=f"user_id={user_id} next_step={next_step}",
    ))
    db.commit()
    db.refresh(state)
    return state


# ─────────────────────────────────────────────────────────────
# get_state
# ─────────────────────────────────────────────────────────────
def get_state(*, user_id: int, db: Session) -> Optional[OnboardingState]:
    """Read the current OnboardingState for a user (None if never started)."""
    return (
        db.query(OnboardingState)
        .filter(OnboardingState.user_id == user_id)
        .first()
    )


# ─────────────────────────────────────────────────────────────
# activate_onboarding
# ─────────────────────────────────────────────────────────────
def activate_onboarding(*, user_id: int, db: Session) -> dict:
    """
    Final commit. Wraps these mutations in one DB transaction:

      1. Create the Organization (uses identity service — also creates
         the legacy Business row for expand/contract dual-write).
      2. KycDocument rows are back-linked to the new Organization.
      3. Create the Subscription (status='trialing', trial_ends_at=now+14d).
      4. Create a SubscriptionPayment(status='scheduled') for trial end.
      5. Mark User.onboarding_status='active'.
      6. Mark OnboardingState.current_step='active'.
      7. ActionLog rows for every step (KYC dossier evidence).

    Returns:
        {
          "organization_id":    int,
          "subscription_id":    int,
          "first_payment_id":   int,
          "trial_ends_at":      str (ISO),
        }
    """
    state = get_state(user_id=user_id, db=db)
    if not state:
        raise OnboardingError("No onboarding state found for this user")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise OnboardingError(f"user_id={user_id} not found")

    draft = _load_payload(state)

    # ── Pre-flight gates ──
    if not user.phone_verified_at:
        raise OnboardingError("Phone is not verified")
    if not user.email_verified_at:
        raise OnboardingError("Email is not verified")
    if "identity" not in draft:
        raise OnboardingError("Identity step has not been completed")
    if "plan" not in draft or "billing_cycle" not in draft:
        raise OnboardingError("Plan + billing cycle have not been selected")
    if "payment_method_id" not in draft:
        raise OnboardingError("Payment method has not been added")
    if not user.terms_accepted_at or not user.privacy_accepted_at:
        raise OnboardingError("Terms / Privacy must be accepted")

    identity = draft["identity"]

    # ── Step 1: Create the Organization (also creates legacy Business) ──
    org = create_organization(
        display_name=identity["display_name"],
        legal_structure=identity["legal_structure"],
        tax_id=identity["tax_id"],
        owner_user_id=user.id,
        db=db,
        business_address=identity.get("business_address"),
        city=identity.get("city"),
        postal_code=identity.get("postal_code"),
        industry_code=identity.get("industry_code"),
        business_phone=identity.get("business_phone"),
        business_email=identity.get("business_email"),
        website=identity.get("website"),
    )

    # ── Step 2: Back-link any KYC docs uploaded against this user to the org ──
    pending_docs = (
        db.query(KycDocument)
        .filter(
            KycDocument.user_id == user.id,
            KycDocument.organization_id.is_(None),
        )
        .all()
    )
    for doc in pending_docs:
        doc.organization_id = org.id

    # ── Step 3: Re-link the payment_method to the new org ──
    pm_id = draft["payment_method_id"]
    pm = db.query(PaymentMethod).filter(PaymentMethod.id == pm_id).first()
    if pm and not pm.organization_id:
        pm.organization_id = org.id
    if pm and pm.organization_id != org.id:
        raise OnboardingError(
            f"Payment method {pm.id} is bound to a different organization"
        )

    # ── Step 4: Create the Subscription (trialing) ──
    sub = create_subscription(
        organization_id=org.id,
        plan=draft["plan"],
        billing_cycle=draft["billing_cycle"],
        payment_method_id=pm_id,
        db=db,
        with_trial=True,
    )

    # ── Step 5: Schedule the first charge for trial end ──
    first_payment = schedule_first_charge(subscription_id=sub.id, db=db)

    # ── Step 6: Update user + state ──
    user.onboarding_status = "active"
    user.business_id = org.legacy_business_id  # legacy compat (expand/contract)
    state.current_step = "active"

    completed = _load_completed(state)
    if "review" not in completed:
        completed.append("review")
    if "active" not in completed:
        completed.append("active")
    _save_completed(state, completed)

    db.add(ActionLog(
        business_id=org.legacy_business_id,
        status="onboarding.activated",
        detail=(
            f"user_id={user.id} organization_id={org.id} subscription_id={sub.id} "
            f"trial_ends_at={sub.trial_ends_at} kyc_docs={len(pending_docs)}"
        ),
    ))
    db.commit()

    return {
        "organization_id":  org.id,
        "subscription_id":  sub.id,
        "first_payment_id": first_payment.id,
        "trial_ends_at":    sub.trial_ends_at.isoformat() if sub.trial_ends_at else None,
    }


# ─────────────────────────────────────────────────────────────
# abandon_onboarding
# ─────────────────────────────────────────────────────────────
def abandon_onboarding(*, user_id: int, db: Session) -> None:
    """User cancels the flow. Marks state as abandoned but keeps the
    row for evidence (so we can analyze drop-off later)."""
    state = get_state(user_id=user_id, db=db)
    if not state:
        return
    state.current_step = "abandoned"

    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.onboarding_status = "suspended"

    db.add(ActionLog(
        business_id=None,
        status="onboarding.abandoned",
        detail=f"user_id={user_id} state_id={state.id}",
    ))
    db.commit()
