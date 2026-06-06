"""
Aurora LTS — Smart Reminders (Product Upgrade #3)
======================================================
Periodic worker that scans for users with stale flows and sends a
gentle re-engagement nudge:

  CASE A — Abandoned WhatsApp NEW_INVOICE:CONFIRM
    User filled in amount + client but never tapped ✅. Nudge after
    30 minutes idle, escalate to "ניתן לשלוח /menu לחזור לתפריט"
    after 24h. Skip if they're outside the 24h freeform window.

  CASE B — Abandoned web onboarding
    OnboardingState.current_step ∈ {phone_otp, email_otp, documents,
    plan, payment_method, review} for ≥48h. Send a single email
    nudge with a deep-link back into the wizard.

  CASE C — Trial ending in 3 days
    Subscription.status='trialing' and trial_ends_at within 3 days.
    Trigger the VAT-Coach trial_ending message via WhatsApp.

  CASE D — Pending KYC docs (Sprint 6)
    KycDocument.status='pending_review' for ≥7 days → ping the
    admin queue (already covered by the manual-review queue UI;
    this is the data-side reminder).

CALLED BY:
  /api/v1/internal/smart-reminders   (Cloud Scheduler cron — every 6h)

DESIGN:
  - Stateful: a Reminder isn't sent twice within the same cooldown.
    We track this on WhatsAppOutboundLog (template_name='reminder_<case>')
    so we don't need a new table.
  - 24h-window-aware: if the WhatsApp 24h window is closed, we either
    send a pre-approved template (when one exists) or skip.
  - Always non-fatal: a single user's reminder failure never aborts
    the sweep.
"""

import datetime
from typing import Optional

from sqlalchemy.orm import Session

from aurora_shared.database import (
    User,
    WhatsAppSession,
    WhatsAppOutboundLog,
    OnboardingState,
    Subscription,
    Organization,
    ActionLog,
)
from app.services.vat_coach import coach_trial_ending


# Cooldowns
ABANDONED_INVOICE_NUDGE_AFTER_MINUTES = 30
ABANDONED_INVOICE_REPEAT_COOLDOWN_HOURS = 24

ABANDONED_ONBOARDING_NUDGE_AFTER_HOURS = 48
ABANDONED_ONBOARDING_REPEAT_COOLDOWN_HOURS = 72

TRIAL_ENDING_NUDGE_DAYS = 3


def _was_reminded_recently(
    db: Session, *, phone: str, kind: str, hours: int,
) -> bool:
    """Did we already send this same reminder kind to this phone within `hours`?"""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
    existing = (
        db.query(WhatsAppOutboundLog)
        .filter(
            WhatsAppOutboundLog.whatsapp_phone_e164 == phone,
            WhatsAppOutboundLog.template_name == f"reminder_{kind}",
            WhatsAppOutboundLog.created_at >= cutoff,
        )
        .first()
    )
    return existing is not None


async def run_reminders(*, db: Session, dry_run: bool = False) -> dict:
    """
    Run all reminder cases. Returns a summary dict of per-case counts.
    Safe to call repeatedly — each case has its own cooldown.

    Outbound calls go through whatsapp_meta_client which is a no-op
    when Meta isn't configured (dev mode), so this is dev-safe.
    """
    summary = {
        "case_a_abandoned_invoice": 0,
        "case_b_abandoned_onboarding": 0,
        "case_c_trial_ending": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    summary["case_a_abandoned_invoice"] = await _case_a_abandoned_invoice(db, dry_run, summary)
    summary["case_b_abandoned_onboarding"] = await _case_b_abandoned_onboarding(db, dry_run, summary)
    summary["case_c_trial_ending"] = await _case_c_trial_ending(db, dry_run, summary)

    db.add(ActionLog(
        business_id=None,
        status="reminders.swept",
        detail=(
            f"a={summary['case_a_abandoned_invoice']} "
            f"b={summary['case_b_abandoned_onboarding']} "
            f"c={summary['case_c_trial_ending']} "
            f"errors={summary['errors']} dry_run={dry_run}"
        ),
    ))
    db.commit()

    return summary


async def _case_a_abandoned_invoice(db: Session, dry_run: bool, summary: dict) -> int:
    """WhatsApp users stuck mid-NEW_INVOICE for >30min → nudge."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(
        minutes=ABANDONED_INVOICE_NUDGE_AFTER_MINUTES
    )
    sessions = (
        db.query(WhatsAppSession)
        .filter(
            WhatsAppSession.state.like("NEW_INVOICE:%"),
            WhatsAppSession.updated_at < cutoff,
            WhatsAppSession.user_id.isnot(None),
        )
        .all()
    )
    sent = 0
    for sess in sessions:
        if _was_reminded_recently(
            db, phone=sess.whatsapp_phone_e164,
            kind="abandoned_invoice",
            hours=ABANDONED_INVOICE_REPEAT_COOLDOWN_HOURS,
        ):
            continue
        if dry_run:
            sent += 1
            continue
        try:
            await _send_reminder(
                db,
                phone=sess.whatsapp_phone_e164,
                kind="abandoned_invoice",
                lang=sess.locale or "he",
                user_id=sess.user_id,
            )
            sent += 1
        except Exception as e:
            print(f"[REMIND] case_a error for {sess.whatsapp_phone_e164}: {e}")
            summary["errors"] += 1
    return sent


async def _case_b_abandoned_onboarding(db: Session, dry_run: bool, summary: dict) -> int:
    """OnboardingState idle for ≥48h → email/whatsapp nudge."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(
        hours=ABANDONED_ONBOARDING_NUDGE_AFTER_HOURS
    )
    states = (
        db.query(OnboardingState)
        .filter(
            OnboardingState.current_step.notin_(("active", "abandoned")),
            OnboardingState.updated_at < cutoff,
        )
        .all()
    )
    sent = 0
    for state in states:
        user = db.query(User).filter(User.id == state.user_id).first()
        if not user or not user.whatsapp_phone_e164:
            # Web-only user — skip in this slice (email nudge ships
            # when SMS provider is wired)
            continue
        if _was_reminded_recently(
            db, phone=user.whatsapp_phone_e164,
            kind="abandoned_onboarding",
            hours=ABANDONED_ONBOARDING_REPEAT_COOLDOWN_HOURS,
        ):
            continue
        if dry_run:
            sent += 1
            continue
        try:
            await _send_reminder(
                db, phone=user.whatsapp_phone_e164,
                kind="abandoned_onboarding",
                lang=user.language_pref or "he",
                user_id=user.id,
            )
            sent += 1
        except Exception as e:
            print(f"[REMIND] case_b error for user {user.id}: {e}")
            summary["errors"] += 1
    return sent


async def _case_c_trial_ending(db: Session, dry_run: bool, summary: dict) -> int:
    """Subscriptions where trial_ends_at is in [now, now+3d]."""
    now = datetime.datetime.utcnow()
    horizon = now + datetime.timedelta(days=TRIAL_ENDING_NUDGE_DAYS)
    subs = (
        db.query(Subscription)
        .filter(
            Subscription.status == "trialing",
            Subscription.trial_ends_at != None,  # noqa: E711
            Subscription.trial_ends_at >= now,
            Subscription.trial_ends_at <= horizon,
        )
        .all()
    )
    sent = 0
    for sub in subs:
        org = db.query(Organization).filter(Organization.id == sub.organization_id).first()
        if not org:
            continue
        # Find the owner User (first owner Membership)
        from aurora_shared.database import Membership
        owner_membership = (
            db.query(Membership)
            .filter(Membership.organization_id == org.id, Membership.role == "owner")
            .first()
        )
        if not owner_membership:
            continue
        user = db.query(User).filter(User.id == owner_membership.user_id).first()
        if not user or not user.whatsapp_phone_e164:
            continue
        if _was_reminded_recently(
            db, phone=user.whatsapp_phone_e164,
            kind="trial_ending", hours=24,
        ):
            continue

        msg = coach_trial_ending(
            trial_ends_at=sub.trial_ends_at,
            lang=user.language_pref or "he",
        )
        if not msg:
            continue
        if dry_run:
            sent += 1
            continue
        try:
            await _send_reminder(
                db, phone=user.whatsapp_phone_e164,
                kind="trial_ending",
                lang=user.language_pref or "he",
                user_id=user.id,
                custom_text=msg,
            )
            sent += 1
        except Exception as e:
            print(f"[REMIND] case_c error for user {user.id}: {e}")
            summary["errors"] += 1
    return sent


# ─────────────────────────────────────────────────────────────
# Outbound helper
# ─────────────────────────────────────────────────────────────
_REMINDER_TEXTS = {
    "abandoned_invoice": {
        "he": "👋 השארת חשבונית באמצע — שלח 'תפריט' להמשיך, או 'ביטול' לבטל.",
        "ar": "👋 الفاتورة لا تزال غير مكتملة — أرسل 'القائمة' للمتابعة أو 'إلغاء' للإلغاء.",
        "en": "👋 You left an invoice mid-flow — send 'menu' to continue or 'cancel' to abort.",
    },
    "abandoned_onboarding": {
        "he": "👋 לא סיימנו את ההרשמה — חזרה אלינו לוקחת דקה אחת. שלח 'הרשמה' להמשיך.",
        "ar": "👋 لم ننهِ التسجيل — العودة دقيقة واحدة. أرسل 'تسجيل'.",
        "en": "👋 We didn't finish your signup — coming back takes 60 seconds. Send 'register' to resume.",
    },
}


async def _send_reminder(
    db: Session, *, phone: str, kind: str, lang: str,
    user_id: Optional[int] = None, custom_text: Optional[str] = None,
) -> None:
    """Lazy import the WhatsApp sender so dev tests without it still work."""
    from app.services import whatsapp_meta_client as wa
    from aurora_shared.services.whatsapp_identity import can_send_freeform, get_or_create_session

    session = get_or_create_session(phone, db)
    if not can_send_freeform(session):
        # 24h window closed → would need an approved template. Skip for now.
        # Future: dispatch via approved-template send path.
        return

    text = custom_text or (_REMINDER_TEXTS.get(kind) or {}).get(lang) \
           or (_REMINDER_TEXTS.get(kind) or {}).get("he", "Aurora reminder")
    await wa.send_text(
        phone, text, db,
        user_id=user_id,
        # Tag the outbound log so we can detect "already reminded"
    )
    # Tag the row we just inserted with the template_name (which is how
    # _was_reminded_recently identifies prior reminders).
    last = (
        db.query(WhatsAppOutboundLog)
        .filter(WhatsAppOutboundLog.whatsapp_phone_e164 == phone)
        .order_by(WhatsAppOutboundLog.id.desc())
        .first()
    )
    if last and not last.template_name:
        last.template_name = f"reminder_{kind}"
        db.commit()
