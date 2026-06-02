"""
Aurora LTS — Marketing Router
==============================
Sprint 7 — Marketing capture from the public site (aurora-ltd.co.il).

ENDPOINTS:
  POST /api/v1/marketing/lead
       Public endpoint. Captures a waitlist / founding-member signup
       from the marketing site. No JWT required, no cookies.
       Rate-limited per-IP via slowapi (10/10min). Redis backend when
       RATE_LIMIT_BACKEND=redis, in-memory for dev.

  GET  /api/v1/marketing/health
       Public health probe. Returns module status + lead count visibility.

SECURITY POSTURE:
  - Anonymous POST (no auth, no cookies, no JWT).
  - CORS: relies on app-wide allow_origins setting. Marketing site at
    https://aurora-ltd.co.il is the intended caller.
  - Rate limit: 10 requests / 10 minutes / IP via slowapi (P0-09).
    Redis backend when RATE_LIMIT_BACKEND=redis; memory for dev.
  - IP hash: SHA-256(salt + raw IP) — we never store the raw IP.
  - ActionLog write on every successful capture for audit trail.
  - Email re-submission within 24h returns 200 (idempotent UX) but
    updates the existing row's metadata; does NOT create a duplicate.

REAL-WORLD ANALOGY:
  This is the clipboard at the door of a coffee shop saying
  "Sign up for early access." Anyone can scribble; we collect names
  and write a one-line note in the daybook. We do not validate the
  email exists — that happens later when we WhatsApp them.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from aurora_shared.database import (
    SessionLocal,
    get_db,
    ActionLog,
    MarketingLead,
)
from aurora_shared.middleware.rate_limit import limiter


# ─────────────────────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────────────────────
router = APIRouter(prefix="/api/v1/marketing", tags=["marketing"])


# ─────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_PATTERN = re.compile(r"^\+?\d[\d\s\-]{6,20}$")

_VALID_TIERS = {"courier", "digital", "premium", "unsure"}
_VALID_LOCALES = {"he", "en", "ar"}


class LeadIn(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    full_name: Optional[str] = Field(None, max_length=200)
    phone_e164: Optional[str] = Field(None, max_length=32)
    tier_interest: Optional[str] = Field(None, max_length=16)
    source: Optional[str] = Field(None, max_length=64)
    locale: Optional[str] = Field("he", max_length=8)
    note: Optional[str] = Field(None, max_length=2000)
    consent_terms: bool = Field(False)
    consent_privacy: bool = Field(False)


class LeadOut(BaseModel):
    ok: bool
    id: int
    status_: str = Field(..., alias="status")
    message: str

    class Config:
        populate_by_name = True


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _hash_ip(raw_ip: str) -> str:
    """SHA-256(IP_HASH_SALT + raw_ip). We never store the raw IP."""
    from app.config.secrets import require_secret
    salt = require_secret("AURORA_IP_HASH_SALT", min_length=16)
    return hashlib.sha256(f"{salt}:{raw_ip}".encode("utf-8")).hexdigest()


def _client_ip(request: Request) -> str:
    # Behind Cloud Run + Global LB the real client IP is in
    # X-Forwarded-For (first hop). Fallback to request.client.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _normalise_email(raw: str) -> str:
    return raw.strip().lower()


def _validate_payload(payload: LeadIn) -> None:
    email = _normalise_email(payload.email)
    if not _EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=400, detail="invalid email")

    if payload.phone_e164:
        if not _PHONE_PATTERN.match(payload.phone_e164):
            raise HTTPException(status_code=400, detail="invalid phone")

    if payload.tier_interest and payload.tier_interest not in _VALID_TIERS:
        raise HTTPException(status_code=400, detail="invalid tier_interest")

    if payload.locale and payload.locale not in _VALID_LOCALES:
        raise HTTPException(status_code=400, detail="invalid locale")


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────
@router.get("/health")
def marketing_health(db: Session = Depends(get_db)) -> dict:
    """Public health probe for the marketing module."""
    try:
        lead_count = db.query(MarketingLead).count()
    except Exception:
        lead_count = -1
    return {
        "ok": True,
        "module": "aurora-marketing",
        "leads_captured_total": lead_count,
    }


@router.post(
    "/lead",
    response_model=LeadOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("10/10minutes")
def submit_lead(
    payload: LeadIn,
    request: Request,
    db: Session = Depends(get_db),
) -> LeadOut:
    """
    Public waitlist capture. Idempotent on email — re-submitting the
    same email within the recent past updates the existing row's
    metadata instead of creating duplicates.
    """
    _validate_payload(payload)

    raw_ip = _client_ip(request)
    ip_hash = _hash_ip(raw_ip)

    email = _normalise_email(payload.email)
    user_agent = (request.headers.get("user-agent") or "")[:500] or None
    referer = (request.headers.get("referer") or "")[:500] or None

    # Idempotent: look up existing row by email (case-insensitive already
    # via _normalise_email).
    existing = (
        db.query(MarketingLead)
        .filter(MarketingLead.email == email)
        .order_by(MarketingLead.id.desc())
        .first()
    )

    now = datetime.datetime.utcnow()

    if existing and existing.status in ("new", "contacted"):
        # Refresh existing row — accept the latest signal.
        if payload.full_name:
            existing.full_name = payload.full_name.strip()[:200]
        if payload.phone_e164:
            existing.phone_e164 = payload.phone_e164.strip()
        if payload.tier_interest:
            existing.tier_interest = payload.tier_interest
        if payload.source:
            existing.source = payload.source[:64]
        if payload.locale:
            existing.locale = payload.locale
        if payload.note:
            existing.note = payload.note[:2000]
        if payload.consent_terms:
            existing.consent_terms = True
        if payload.consent_privacy:
            existing.consent_privacy = True
        existing.ip_hash = ip_hash
        existing.user_agent = user_agent
        existing.referer = referer
        existing.updated_at = now
        try:
            db.commit()
            db.refresh(existing)
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="lead update failed")

        # Audit log — silent if ActionLog write fails
        try:
            db.add(ActionLog(
                business_id=None,
                status="marketing_lead_refreshed",
                detail=f"id={existing.id} tier={existing.tier_interest} source={existing.source}",
                triggered_at=now,
            ))
            db.commit()
        except Exception:
            db.rollback()

        return LeadOut(
            ok=True,
            id=existing.id,
            status=existing.status,
            message="Thanks — we'll be in touch.",
        )

    # New row
    lead = MarketingLead(
        email=email,
        full_name=(payload.full_name or "").strip()[:200] or None,
        phone_e164=(payload.phone_e164 or "").strip() or None,
        tier_interest=payload.tier_interest,
        source=(payload.source or "marketing-home")[:64],
        locale=payload.locale or "he",
        note=(payload.note or "")[:2000] or None,
        consent_terms=bool(payload.consent_terms),
        consent_privacy=bool(payload.consent_privacy),
        ip_hash=ip_hash,
        user_agent=user_agent,
        referer=referer,
        status="new",
        created_at=now,
        updated_at=now,
    )
    db.add(lead)
    try:
        db.commit()
        db.refresh(lead)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="lead capture failed")

    try:
        db.add(ActionLog(
            business_id=None,
            status="marketing_lead_captured",
            detail=f"id={lead.id} tier={lead.tier_interest} source={lead.source} locale={lead.locale}",
            triggered_at=now,
        ))
        db.commit()
    except Exception:
        db.rollback()

    # P2-24: enrol in email nurture sequence (non-blocking; fails silently)
    try:
        from app.services.lead_nurture import enrol_lead
        enrol_lead(lead, db)
    except Exception as _nurture_err:
        import logging as _log
        _log.getLogger(__name__).warning("nurture enrolment failed: %s", _nurture_err)

    return LeadOut(
        ok=True,
        id=lead.id,
        status=lead.status,
        message="You're on the list — we'll reach out by WhatsApp.",
    )
