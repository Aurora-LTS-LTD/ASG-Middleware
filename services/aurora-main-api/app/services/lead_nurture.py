"""
Aurora LTS — Email Lead Nurture Service  (P2-24)
==================================================

Sends a multi-step email nurture sequence to new marketing leads
captured via POST /api/v1/marketing/lead.

SEQUENCE (configurable delays)
────────────────────────────────
  Day 0 → Email 1: Welcome + quick start guide (sent immediately on signup)
  Day 1 → Email 2: "How Aurora saved X hours/week" — social proof
  Day 3 → Email 3: Feature spotlight — WhatsApp invoicing in 60 seconds
  Day 7 → Email 4: Soft CTA — "14 days are almost up" + upgrade offer
  Day 14→ Email 5: Final CTA — "Your free trial ends tomorrow"

BACKEND
───────
  NURTURE_BACKEND=stub      Logs emails to ActionLog; no real sends
  NURTURE_BACKEND=brevo     Brevo (formerly Sendinblue) transactional API
  NURTURE_BACKEND=sendgrid  SendGrid transactional API

BREVO INTEGRATION
──────────────────
  Uses Brevo's contacts + transactional email API.
  Template IDs are configured via env vars:
    BREVO_TEMPLATE_WELCOME         (default: 1)
    BREVO_TEMPLATE_SOCIAL_PROOF    (default: 2)
    BREVO_TEMPLATE_FEATURE_SPOT    (default: 3)
    BREVO_TEMPLATE_SOFT_CTA        (default: 4)
    BREVO_TEMPLATE_FINAL_CTA       (default: 5)

  On signup: POST /contacts to add to list, POST /smtp/email to send email 1.
  Emails 2-5 are sent by a Cloud Scheduler job hitting
  POST /api/v1/internal/nurture-tick daily.

UNSUBSCRIBE
───────────
  Brevo manages unsubscribes at the list level. Aurora also stores
  MarketingLead.nurture_unsubscribed_at and skips subsequent emails.
"""

from __future__ import annotations

import datetime
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from app.database import ActionLog
from app.database.models import MarketingLead

log = logging.getLogger(__name__)

# Sequence definition: (step_number, delay_days, template_env_var, subject)
NURTURE_SEQUENCE = [
    (1,  0,  "BREVO_TEMPLATE_WELCOME",      "ברוכים הבאים ל-Aurora — כל מה שצריך לדעת"),
    (2,  1,  "BREVO_TEMPLATE_SOCIAL_PROOF", "איך קוסמטיקאית חסכה 6 שעות בחודש"),
    (3,  3,  "BREVO_TEMPLATE_FEATURE_SPOT", "חשבונית ב-WhatsApp — 60 שניות. ממש"),
    (4,  7,  "BREVO_TEMPLATE_SOFT_CTA",     "14 הימים שלך כמעט הסתיימו — מה הלאה?"),
    (5,  14, "BREVO_TEMPLATE_FINAL_CTA",    "יום אחרון לניסיון החינם — האם תמשיכו?"),
]


def _backend() -> str:
    return (os.getenv("NURTURE_BACKEND") or "stub").strip().lower()


def _brevo_api_key() -> str:
    return os.getenv("BREVO_API_KEY", "")


def _template_id(env_var: str, default: int) -> int:
    try:
        return int(os.getenv(env_var, str(default)))
    except ValueError:
        return default


# ─────────────────────────────────────────────────────────────
# Enrol a new lead into the sequence
# ─────────────────────────────────────────────────────────────

def enrol_lead(lead: MarketingLead, db: Session) -> None:
    """
    Called by the marketing router immediately after a new lead is captured.
    Sends email 1 and records the enrolment timestamp.
    """
    if not lead.email:
        return
    if lead.nurture_enrolled_at:
        log.debug("Lead %d already enrolled in nurture — skipping", lead.id)
        return

    backend = _backend()
    if backend == "brevo":
        _brevo_add_contact(lead)

    _send_sequence_email(lead, step=1, db=db)

    lead.nurture_enrolled_at = datetime.datetime.utcnow()
    db.add(ActionLog(
        business_id=None,
        status="nurture.enrolment",
        detail=f"lead_id={lead.id} email={lead.email} backend={backend}",
    ))
    db.commit()


# ─────────────────────────────────────────────────────────────
# Daily tick — called by Cloud Scheduler
# ─────────────────────────────────────────────────────────────

def run_nurture_tick(db: Session) -> dict:
    """
    Checks all enrolled leads and sends the next email in their sequence
    if the configured delay has elapsed.

    Called daily by POST /api/v1/internal/nurture-tick.
    """
    sent = 0
    skipped = 0
    now = datetime.datetime.utcnow()

    leads = (
        db.query(MarketingLead)
        .filter(
            MarketingLead.nurture_enrolled_at.isnot(None),
            MarketingLead.nurture_unsubscribed_at.is_(None),
        )
        .all()
    )

    for lead in leads:
        days_enrolled = (now - lead.nurture_enrolled_at).days

        # Find the next step to send
        next_step = None
        for step_num, delay_days, template_var, subject in NURTURE_SEQUENCE:
            if step_num == 1:
                continue  # step 1 sent at enrolment
            if days_enrolled >= delay_days:
                last_sent = lead.nurture_last_step or 0
                if step_num > last_sent:
                    next_step = (step_num, delay_days, template_var, subject)
                    # Don't break — take the highest eligible step

        if next_step:
            step_num, _, _, _ = next_step
            _send_sequence_email(lead, step=step_num, db=db)
            lead.nurture_last_step = step_num
            db.add(lead)
            sent += 1
        else:
            skipped += 1

    db.commit()
    log.info("nurture_tick: sent=%d skipped=%d", sent, skipped)
    return {"sent": sent, "skipped": skipped, "total_leads": len(leads)}


# ─────────────────────────────────────────────────────────────
# Send a single nurture email
# ─────────────────────────────────────────────────────────────

def _send_sequence_email(lead: MarketingLead, step: int, db: Session) -> None:
    step_entry = next((s for s in NURTURE_SEQUENCE if s[0] == step), None)
    if not step_entry:
        log.warning("Unknown nurture step %d", step)
        return

    step_num, delay_days, template_var, subject = step_entry
    template_id = _template_id(template_var, step_num)
    backend = _backend()

    if backend == "stub":
        log.info(
            "[nurture stub] lead_id=%d step=%d subject=%r template=%d",
            lead.id, step_num, subject, template_id,
        )
        db.add(ActionLog(
            business_id=None,
            status=f"nurture.step{step_num}.stub",
            detail=f"lead_id={lead.id} email={lead.email} subject={subject!r}",
        ))
        return

    if backend == "brevo":
        _brevo_send_template(
            to_email=lead.email,
            to_name=lead.full_name or "",
            template_id=template_id,
            params={
                "FIRST_NAME": (lead.full_name or "").split()[0] if lead.full_name else "שלום",
                "LEAD_ID": str(lead.id),
            },
        )
        db.add(ActionLog(
            business_id=None,
            status=f"nurture.step{step_num}.sent",
            detail=f"lead_id={lead.id} email={lead.email} template_id={template_id}",
        ))
        return

    if backend == "sendgrid":
        _sendgrid_send(
            to_email=lead.email,
            to_name=lead.full_name or "",
            subject=subject,
            template_id=str(template_id),
            dynamic_data={
                "first_name": (lead.full_name or "").split()[0] if lead.full_name else "שלום",
            },
        )
        db.add(ActionLog(
            business_id=None,
            status=f"nurture.step{step_num}.sent",
            detail=f"lead_id={lead.id} via=sendgrid template={template_id}",
        ))


# ─────────────────────────────────────────────────────────────
# Brevo API helpers
# ─────────────────────────────────────────────────────────────

def _brevo_add_contact(lead: MarketingLead) -> None:
    api_key = _brevo_api_key()
    if not api_key:
        log.warning("BREVO_API_KEY not set — skipping contact add")
        return
    import httpx
    headers = {"api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "email": lead.email,
        "attributes": {
            "FIRSTNAME": (lead.full_name or "").split()[0] if lead.full_name else "",
            "LASTNAME": " ".join((lead.full_name or "").split()[1:]),
            "AURORA_LEAD_ID": str(lead.id),
            "AURORA_SOURCE": lead.source or "",
            "AURORA_TIER": lead.tier_interest or "",
        },
        "listIds": [int(os.getenv("BREVO_LIST_ID", "1"))],
        "updateEnabled": True,
    }
    try:
        r = httpx.post("https://api.brevo.com/v3/contacts", json=payload, headers=headers, timeout=10)
        r.raise_for_status()
    except Exception as e:
        log.warning("Brevo add contact failed: %s", e)


def _brevo_send_template(
    to_email: str,
    to_name: str,
    template_id: int,
    params: dict,
) -> None:
    api_key = _brevo_api_key()
    if not api_key:
        log.warning("BREVO_API_KEY not set")
        return
    import httpx
    headers = {"api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "templateId": template_id,
        "to": [{"email": to_email, "name": to_name}],
        "params": params,
        "replyTo": {"email": os.getenv("BREVO_REPLY_TO", "hello@aurora-ltd.co.il")},
    }
    try:
        r = httpx.post(
            "https://api.brevo.com/v3/smtp/email", json=payload, headers=headers, timeout=10
        )
        r.raise_for_status()
        log.info("Brevo template %d sent to %s", template_id, to_email)
    except Exception as e:
        log.warning("Brevo send template failed: %s", e)


# ─────────────────────────────────────────────────────────────
# SendGrid fallback
# ─────────────────────────────────────────────────────────────

def _sendgrid_send(
    to_email: str,
    to_name: str,
    subject: str,
    template_id: str,
    dynamic_data: dict,
) -> None:
    from app.services.sendgrid_client import send_template_email
    send_template_email(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        template_id=template_id,
        dynamic_data=dynamic_data,
    )
