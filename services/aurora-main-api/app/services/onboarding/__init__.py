"""
ASG / Aurora Solutions — Onboarding Services Package
=======================================================
Aurora Onboarding Module / Phase 6b.

This package owns the multi-step web onboarding flow:
  - otp_service        : phone/email OTP issue + verify (bcrypt)
  - kyc_service        : pre-signed upload, sha256 dedup, manual-review queue
  - payplus_client     : PayPlus tokenization + charge stub
  - subscription_service: plan pricing, trial setup, scheduled charges
  - onboarding_service : the state-machine orchestrator (start, advance, activate)

Public re-exports for ergonomic imports:
    from app.services.onboarding import (
        issue_otp, verify_otp,
        init_document_upload, finalize_document_upload,
        compute_plan_amount, create_subscription, schedule_first_charge,
        start_onboarding, advance_onboarding, get_state, activate_onboarding,
        payplus_tokenize, payplus_charge,
    )
"""

from app.services.onboarding.otp_service import (
    issue_otp,
    verify_otp,
    OTP_TTL_SECONDS,
)
from app.services.onboarding.kyc_service import (
    init_document_upload,
    finalize_document_upload,
    REQUIRED_DOC_TYPES_BY_LEGAL_STRUCTURE,
)
from app.services.onboarding.payplus_client import (
    payplus_tokenize,
    payplus_charge,
    PAYPLUS_BACKEND,
)
from app.services.onboarding.subscription_service import (
    PLAN_AMOUNTS_MINOR_UNITS,
    CYCLE_DISCOUNT_PCT,
    TRIAL_DAYS,
    compute_plan_amount,
    create_subscription,
    schedule_first_charge,
)
from app.services.onboarding.onboarding_service import (
    start_onboarding,
    advance_onboarding,
    get_state,
    activate_onboarding,
    abandon_onboarding,
    OnboardingError,
)

__all__ = [
    # OTP
    "issue_otp", "verify_otp", "OTP_TTL_SECONDS",
    # KYC
    "init_document_upload", "finalize_document_upload",
    "REQUIRED_DOC_TYPES_BY_LEGAL_STRUCTURE",
    # PayPlus
    "payplus_tokenize", "payplus_charge", "PAYPLUS_BACKEND",
    # Subscription
    "PLAN_AMOUNTS_MINOR_UNITS", "CYCLE_DISCOUNT_PCT", "TRIAL_DAYS",
    "compute_plan_amount", "create_subscription", "schedule_first_charge",
    # Orchestrator
    "start_onboarding", "advance_onboarding", "get_state",
    "activate_onboarding", "abandon_onboarding", "OnboardingError",
]
