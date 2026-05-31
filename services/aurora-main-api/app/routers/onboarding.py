"""
ASG / Aurora Solutions — Onboarding Router
==============================================
Aurora Onboarding Module / Phase 6b.

ALL ENDPOINTS UNDER /api/v1/onboarding/* (except /onboarding/start which
also accepts unauthenticated requests).

ENDPOINTS:
  POST /api/v1/onboarding/start              — bootstrap user + state, issue JWT
  GET  /api/v1/onboarding/state              — read current state (resume support)
  POST /api/v1/onboarding/identity           — submit identity step
  POST /api/v1/onboarding/phone/send-otp     — issue phone OTP
  POST /api/v1/onboarding/phone/verify-otp   — verify phone OTP
  POST /api/v1/onboarding/email/send-otp     — issue email OTP
  POST /api/v1/onboarding/email/verify-otp   — verify email OTP
  POST /api/v1/onboarding/documents/init-upload     — request signed URL
  PUT  /api/v1/onboarding/documents/{doc_id}/upload-stub — local-PUT bytes (stub)
  POST /api/v1/onboarding/documents/finalize — confirm upload, hash, queue review
  POST /api/v1/onboarding/billing/plan       — record plan choice
  POST /api/v1/onboarding/billing/payment-method — tokenize via PayPlus, persist
  POST /api/v1/onboarding/review             — accept T&C / privacy
  POST /api/v1/onboarding/activate           — final commit
  POST /api/v1/onboarding/abandon            — explicit cancel

  GET  /api/v1/onboarding/plans              — public pricing + cycle table
  GET  /api/v1/onboarding/health             — module health check (public)

SECURITY:
  - /start is public (creates a User from email + password)
  - All other endpoints require JWT (issued by /start or by /auth/login)
  - State-changing endpoints write to ActionLog (KYC dossier evidence)
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import datetime
import json
import os
import pathlib
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from aurora_shared.database import (
    get_db,
    User,
    OnboardingState,
    OtpVerification,
    KycDocument,
    PaymentMethod,
    Subscription,
    ActionLog,
)
from aurora_shared.middleware.auth_middleware import get_current_user
from aurora_shared.services.auth_service import (
    hash_password,
    verify_password,
    create_access_token,
)
from aurora_shared.services.identity import resolve_user_context
from app.services.onboarding import (
    issue_otp,
    verify_otp,
    init_document_upload,
    finalize_document_upload,
    REQUIRED_DOC_TYPES_BY_LEGAL_STRUCTURE,
    payplus_tokenize,
    compute_plan_amount,
    PLAN_AMOUNTS_MINOR_UNITS,
    CYCLE_DISCOUNT_PCT,
    TRIAL_DAYS,
    start_onboarding,
    advance_onboarding,
    get_state,
    activate_onboarding,
    abandon_onboarding,
    OnboardingError,
)
from app.services.onboarding.otp_service import OtpDeliveryError
from app.middleware.rate_limit import limiter


# ─────────────────────────────────────────────────────────────
# Validation helpers
# ─────────────────────────────────────────────────────────────
_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

# Single source of truth for the local-stub upload directory lives in
# kyc_service. Importing it here means the router + service can never
# disagree about where bytes are written (matters for Cloud Run where
# the path is /tmp/aurora/kyc_uploads, not app/static/kyc_uploads).
from app.services.onboarding.kyc_service import _LOCAL_KYC_DIR  # noqa: E402


router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


# ═══════════════════════════════════════════════════════════════
# REQUEST / RESPONSE SCHEMAS
# ═══════════════════════════════════════════════════════════════
class StartRequest(BaseModel):
    """Bootstrap a new onboarding session — public endpoint."""
    email: str = Field(..., pattern=_EMAIL_RE)
    password: str = Field(..., min_length=8, max_length=200)
    language_pref: Optional[str] = Field(default="he", pattern=r"^(he|ar|en)$")
    surface: Optional[str] = Field(default="web", pattern=r"^(web|whatsapp)$")


class IdentityStep(BaseModel):
    first_name: str = Field(..., min_length=2, max_length=80)
    last_name: str = Field(..., min_length=2, max_length=80)
    legal_structure: str = Field(..., pattern=r"^(osek_morshe|osek_patur|chevra_baam)$")
    tax_id: str = Field(..., min_length=5, max_length=20)
    display_name: str = Field(..., min_length=3, max_length=120)
    business_address: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    industry_code: Optional[str] = None
    business_phone: Optional[str] = None
    business_email: Optional[str] = Field(default=None, pattern=_EMAIL_RE)
    website: Optional[str] = None
    fax: Optional[str] = None


class SendOtpRequest(BaseModel):
    target: str = Field(..., min_length=3, max_length=120)
    purpose: Optional[str] = Field(default="signup", pattern=r"^(signup|step_up)$")


class VerifyOtpRequest(BaseModel):
    target: str = Field(..., min_length=3, max_length=120)
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class InitUploadRequest(BaseModel):
    document_type: str
    mime_type: str
    bytes_size: int = Field(..., gt=0, le=10 * 1024 * 1024)


class FinalizeUploadRequest(BaseModel):
    doc_id: str


class PlanStep(BaseModel):
    plan: str = Field(..., pattern=r"^(starter|pro|enterprise)$")
    billing_cycle: str = Field(..., pattern=r"^(monthly|quarterly|annual)$")


class PaymentMethodStep(BaseModel):
    kind: str = Field(..., pattern=r"^(credit_card|direct_debit)$")
    # The browser-side iframe (PayPlus) returns a tokenization payload —
    # we accept it as a free-form dict and let payplus_tokenize() pull
    # the safe display fields.
    tokenization_payload: dict


class ReviewStep(BaseModel):
    terms_accepted: bool
    privacy_accepted: bool
    version: Optional[str] = None  # e.g. "2026-04"; defaults to current month


# ═══════════════════════════════════════════════════════════════
# PUBLIC: GET /onboarding/plans
# ═══════════════════════════════════════════════════════════════
@router.get("/plans")
def get_plans():
    """
    Public pricing table for the wizard's PlanPicker step.
    Returns all plan × cycle combinations with VAT shown separately.
    """
    plans = []
    for plan, base_minor in PLAN_AMOUNTS_MINOR_UNITS.items():
        for cycle in CYCLE_DISCOUNT_PCT.keys():
            pricing = compute_plan_amount(plan, cycle)
            # VAT: 18% (current Israeli rate as of 2026)
            vat_amount = int(round(pricing["cycle_amount"] * 0.18))
            plans.append({
                "plan": plan,
                "billing_cycle": cycle,
                "cycle_amount_minor_units": pricing["cycle_amount"],
                "vat_amount_minor_units": vat_amount,
                "total_with_vat_minor_units": pricing["cycle_amount"] + vat_amount,
                "discount_pct": pricing["discount_pct"],
                "currency": "ILS",
            })
    return {
        "plans": plans,
        "trial_days": TRIAL_DAYS,
        "vat_rate_pct": 18.0,
    }


# ═══════════════════════════════════════════════════════════════
# PUBLIC: GET /onboarding/health
# ═══════════════════════════════════════════════════════════════
@router.get("/health")
def health():
    """Module health endpoint (no auth)."""
    return {
        "ok": True,
        "module": "aurora-onboarding",
        "trial_days": TRIAL_DAYS,
        "kyc_backend": (os.getenv("KYC_BACKEND") or "stub"),
        "otp_backend": (os.getenv("OTP_BACKEND") or "stub"),
        "payplus_backend": (os.getenv("PAYPLUS_BACKEND") or "stub"),
    }


# ═══════════════════════════════════════════════════════════════
# POST /onboarding/start  — public (creates User)
# ═══════════════════════════════════════════════════════════════
@router.post("/start", status_code=201)
@limiter.limit("5/minute")
def start_endpoint(payload: StartRequest, request: Request, db: Session = Depends(get_db)):
    """
    Bootstrap a new tenant. Creates the User row, issues a JWT, and
    creates the OnboardingState in 'identity' step.

    No business / organization is created here — that happens at activate().
    """
    email = payload.email.strip().lower()

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        # Treat as login — verify password and reissue JWT only if the
        # user is still in onboarding. If activated, redirect them
        # to the dashboard via /auth/login.
        if not verify_password(payload.password, existing.password_hash):
            raise HTTPException(status_code=401, detail="Email exists; password mismatch")
        if existing.onboarding_status == "active":
            raise HTTPException(
                status_code=409,
                detail="Account is already activated — please log in via /auth/login",
            )
        # Resume the existing onboarding
        try:
            state = start_onboarding(user_id=existing.id, surface=payload.surface, db=db)
        except OnboardingError as e:
            raise HTTPException(status_code=400, detail=str(e))
        ctx = resolve_user_context(existing, db)
        token = create_access_token(
            existing.id, existing.role, existing.business_id,
            active_org_ids=ctx["active_org_ids"],
            primary_org_id=ctx["primary_org_id"],
            accountant_of=ctx["accountant_of"],
        )
        return _start_response(existing, state, token, resumed=True)

    # ── Fresh signup ──
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=email.split("@")[0],
        role="business_owner",
        is_active=True,
        language_pref=payload.language_pref or "he",
        onboarding_status="identity",
    )
    db.add(user)
    db.flush()
    db.commit()
    db.refresh(user)

    try:
        state = start_onboarding(user_id=user.id, surface=payload.surface, db=db)
    except OnboardingError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.add(ActionLog(
        business_id=None,
        status="user.signup",
        detail=f"user_id={user.id} email={email} surface={payload.surface}",
    ))
    db.commit()

    token = create_access_token(user.id, user.role, user.business_id)
    return _start_response(user, state, token, resumed=False)


def _start_response(user: User, state: OnboardingState, token: str, resumed: bool) -> dict:
    return {
        "resumed": resumed,
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "language_pref": user.language_pref,
            "onboarding_status": user.onboarding_status,
            "phone_verified_at": user.phone_verified_at.isoformat() if user.phone_verified_at else None,
            "email_verified_at": user.email_verified_at.isoformat() if user.email_verified_at else None,
        },
        "onboarding": {
            "state_id": state.id,
            "current_step": state.current_step,
            "completed_steps": json.loads(state.completed_steps or "[]"),
            "expires_at": state.expires_at.isoformat() if state.expires_at else None,
        },
    }


# ═══════════════════════════════════════════════════════════════
# GET /onboarding/state  — resume support
# ═══════════════════════════════════════════════════════════════
@router.get("/state")
def get_state_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Read the current onboarding state for the logged-in user."""
    state = get_state(user_id=current_user.id, db=db)
    if not state:
        raise HTTPException(status_code=404, detail="No onboarding session for this user")
    return {
        "state_id": state.id,
        "current_step": state.current_step,
        "completed_steps": json.loads(state.completed_steps or "[]"),
        "draft_payload": json.loads(state.draft_payload or "{}"),
        "expires_at": state.expires_at.isoformat() if state.expires_at else None,
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "phone_verified_at": current_user.phone_verified_at.isoformat()
                if current_user.phone_verified_at else None,
            "email_verified_at": current_user.email_verified_at.isoformat()
                if current_user.email_verified_at else None,
        },
    }


# ═══════════════════════════════════════════════════════════════
# POST /onboarding/identity
# ═══════════════════════════════════════════════════════════════
@router.post("/identity")
def submit_identity(
    payload: IdentityStep,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        state = advance_onboarding(
            user_id=current_user.id,
            step="identity",
            payload=payload.dict(),
            db=db,
        )
    except OnboardingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"current_step": state.current_step}


# ═══════════════════════════════════════════════════════════════
# OTP endpoints (phone + email)
# ═══════════════════════════════════════════════════════════════
@router.post("/phone/send-otp")
@limiter.limit("3/minute")
def send_phone_otp(
    payload: SendOtpRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = issue_otp(
            user_id=current_user.id,
            channel="phone",
            target=payload.target.strip(),
            purpose=payload.purpose or "signup",
            lang=current_user.language_pref or "he",
            db=db,
            request_ip=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OtpDeliveryError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return result


@router.post("/phone/verify-otp")
@limiter.limit("10/minute")
def verify_phone_otp(
    payload: VerifyOtpRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ok = verify_otp(
        user_id=current_user.id,
        channel="phone",
        target=payload.target.strip(),
        code=payload.code,
        db=db,
        request_ip=request.client.host if request.client else None,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    # Advance the wizard
    try:
        state = advance_onboarding(
            user_id=current_user.id,
            step="phone_otp",
            payload={"phone_e164": payload.target.strip()},
            db=db,
        )
    except OnboardingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"verified": True, "current_step": state.current_step}


@router.post("/email/send-otp")
@limiter.limit("5/minute")
def send_email_otp(
    payload: SendOtpRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = issue_otp(
            user_id=current_user.id,
            channel="email",
            target=payload.target.strip().lower(),
            purpose=payload.purpose or "signup",
            lang=current_user.language_pref or "he",
            db=db,
            request_ip=request.client.host if request.client else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OtpDeliveryError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return result


@router.post("/email/verify-otp")
@limiter.limit("10/minute")
def verify_email_otp(
    payload: VerifyOtpRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ok = verify_otp(
        user_id=current_user.id,
        channel="email",
        target=payload.target.strip().lower(),
        code=payload.code,
        db=db,
        request_ip=request.client.host if request.client else None,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    try:
        state = advance_onboarding(
            user_id=current_user.id,
            step="email_otp",
            payload={},
            db=db,
        )
    except OnboardingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"verified": True, "current_step": state.current_step}


# ═══════════════════════════════════════════════════════════════
# Document upload endpoints
# ═══════════════════════════════════════════════════════════════
@router.post("/documents/init-upload")
def init_upload_endpoint(
    payload: InitUploadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return init_document_upload(
            user_id=current_user.id,
            document_type=payload.document_type,
            mime_type=payload.mime_type,
            bytes_size=payload.bytes_size,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/documents/{doc_id}/upload-stub")
async def upload_stub_endpoint(
    doc_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    LOCAL-DEV stub for the pre-signed upload URL. The browser PUTs the
    raw bytes here. In production, the signed URL points to GCS and
    Aurora never sees the bytes — Sprint 2 wires that up.

    NO JWT required: the doc_id is a UUIDv4 (unguessable) and the row
    is single-use (status flips to pending_review on finalize).
    """
    doc = db.query(KycDocument).filter(KycDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Doc not found")
    if doc.status != "pending_upload":
        raise HTTPException(status_code=409, detail=f"Doc is in status {doc.status}")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")
    if len(body) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")

    _LOCAL_KYC_DIR.mkdir(parents=True, exist_ok=True)
    target = _LOCAL_KYC_DIR / doc_id
    with open(target, "wb") as fp:
        fp.write(body)

    return Response(status_code=204)


@router.post("/documents/finalize")
def finalize_upload_endpoint(
    payload: FinalizeUploadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        doc = finalize_document_upload(
            doc_id=payload.doc_id,
            user_id=current_user.id,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Check whether the documents step is now complete (all required types
    # for the chosen legal_structure are uploaded). If so, advance.
    state = get_state(user_id=current_user.id, db=db)
    advanced = False
    if state:
        draft = json.loads(state.draft_payload or "{}")
        legal = (draft.get("identity", {}) or {}).get("legal_structure")
        if legal:
            required = REQUIRED_DOC_TYPES_BY_LEGAL_STRUCTURE.get(legal, [])
            present = (
                db.query(KycDocument)
                .filter(
                    KycDocument.user_id == current_user.id,
                    KycDocument.document_type.in_(required),
                    KycDocument.status.in_(("pending_review", "approved")),
                )
                .all()
            )
            present_types = {d.document_type for d in present}
            if all(t in present_types for t in required):
                try:
                    advance_onboarding(
                        user_id=current_user.id,
                        step="documents",
                        payload={},
                        db=db,
                    )
                    advanced = True
                except OnboardingError:
                    pass

    return {
        "doc_id": doc.id,
        "status": doc.status,
        "sha256": doc.sha256,
        "bytes_size": doc.bytes_size,
        "advanced_to_next_step": advanced,
    }


# ═══════════════════════════════════════════════════════════════
# Billing endpoints
# ═══════════════════════════════════════════════════════════════
@router.post("/billing/plan")
def submit_plan(
    payload: PlanStep,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        state = advance_onboarding(
            user_id=current_user.id,
            step="plan",
            payload=payload.dict(),
            db=db,
        )
    except OnboardingError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Echo the computed pricing back so the frontend confirmation card
    # has the canonical numbers — never trust client-side math for money.
    pricing = compute_plan_amount(payload.plan, payload.billing_cycle)
    vat_amount = int(round(pricing["cycle_amount"] * 0.18))
    return {
        "current_step": state.current_step,
        "pricing": {
            **pricing,
            "vat_amount_minor_units": vat_amount,
            "total_with_vat_minor_units": pricing["cycle_amount"] + vat_amount,
            "trial_days": TRIAL_DAYS,
        },
    }


@router.post("/billing/payment-method")
def submit_payment_method(
    payload: PaymentMethodStep,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Receive the tokenization payload from the PayPlus iframe (or its
    direct-debit form), persist a PaymentMethod row, advance the wizard.
    """
    try:
        token_data = payplus_tokenize(
            kind=payload.kind,
            raw_payload=payload.tokenization_payload or {},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    pm = PaymentMethod(
        organization_id=None,    # back-linked at activate()
        kind=payload.kind,
        provider="payplus",
        provider_token=token_data["provider_token"],
        card_last4=token_data.get("card_last4"),
        card_brand=token_data.get("card_brand"),
        card_exp_month=token_data.get("card_exp_month"),
        card_exp_year=token_data.get("card_exp_year"),
        bank_code=token_data.get("bank_code"),
        branch_code=token_data.get("branch_code"),
        account_last4=token_data.get("account_last4"),
        holder_name=payload.tokenization_payload.get("holder_name") if isinstance(
            payload.tokenization_payload, dict
        ) else None,
        status="active",
        is_default=True,
    )
    db.add(pm)

    db.add(ActionLog(
        business_id=None,
        status="payment_method.added",
        detail=(
            f"user_id={current_user.id} kind={payload.kind} provider=payplus "
            f"last4={token_data.get('card_last4') or token_data.get('account_last4') or 'n/a'}"
        ),
    ))
    db.commit()
    db.refresh(pm)

    try:
        state = advance_onboarding(
            user_id=current_user.id,
            step="payment_method",
            payload={"payment_method_id": pm.id},
            db=db,
        )
    except OnboardingError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "current_step": state.current_step,
        "payment_method": {
            "id": pm.id,
            "kind": pm.kind,
            "provider": pm.provider,
            "card_last4": pm.card_last4,
            "card_brand": pm.card_brand,
            "card_exp_month": pm.card_exp_month,
            "card_exp_year": pm.card_exp_year,
            "bank_code": pm.bank_code,
            "branch_code": pm.branch_code,
            "account_last4": pm.account_last4,
        },
    }


# ═══════════════════════════════════════════════════════════════
# POST /onboarding/review  — accept T&C / Privacy
# ═══════════════════════════════════════════════════════════════
@router.post("/review")
def submit_review(
    payload: ReviewStep,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        state = advance_onboarding(
            user_id=current_user.id,
            step="review",
            payload=payload.dict(),
            db=db,
        )
    except OnboardingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"current_step": state.current_step}


# ═══════════════════════════════════════════════════════════════
# POST /onboarding/activate  — final commit
# ═══════════════════════════════════════════════════════════════
@router.post("/activate")
def activate_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = activate_onboarding(user_id=current_user.id, db=db)
    except OnboardingError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Reissue a JWT now that org context exists (so the dashboard load
    # has the correct active_org_ids without a re-login round-trip).
    db.refresh(current_user)
    ctx = resolve_user_context(current_user, db)
    token = create_access_token(
        current_user.id, current_user.role, current_user.business_id,
        active_org_ids=ctx["active_org_ids"],
        primary_org_id=ctx["primary_org_id"],
        accountant_of=ctx["accountant_of"],
    )
    return {
        **result,
        "access_token": token,
        "token_type": "bearer",
        "redirect_to": "/dashboard",
    }


# ═══════════════════════════════════════════════════════════════
# POST /onboarding/abandon  — explicit cancel
# ═══════════════════════════════════════════════════════════════
@router.post("/abandon")
def abandon_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    abandon_onboarding(user_id=current_user.id, db=db)
    return {"status": "abandoned"}
