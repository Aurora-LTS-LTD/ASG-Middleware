"""
Aurora LTS — Internal Router (Cloud Scheduler Hooks)
========================================================
Endpoints called by Cloud Scheduler / Cloud Tasks (not by humans).

AUTH MODEL:
  All endpoints under /api/v1/internal/* require an `X-Aurora-Internal`
  header whose value matches the AURORA_INTERNAL_TOKEN env var. This
  is set in Secret Manager and only known to:
    - Aurora's Cloud Run instance (env var)
    - Cloud Scheduler jobs (configured to send the header)

  No JWT required because Cloud Scheduler doesn't have one. The shared
  secret is sufficient for service-to-service auth inside Aurora's
  GCP project.

  In dev (no token set) the auth check is skipped — same pattern as
  the WhatsApp HMAC.

ENDPOINTS:
  POST /api/v1/internal/close-month
       Cloud Scheduler cron: 0 3 1 * *  (3 AM Asia/Jerusalem on the 1st)
       Triggers revenue_share.close_month() for last calendar month.

  POST /api/v1/internal/charge-trial-ends
       Cloud Scheduler cron: 0 4 * * *  (4 AM daily)
       Sweeps SubscriptionPayment(status='scheduled', attempted_at <= now)
       and runs the first-charge attempt via PayPlus. On success, calls
       revenue_share.accrue_on_charge_success().

  POST /api/v1/internal/expire-invitations
       Cloud Scheduler cron: 15 * * * *  (every hour)
       Bulk-flips Invitation rows past their TTL to status='expired'.

  POST /api/v1/internal/audit-export
       Cloud Scheduler cron: 0 2 * * *   (Sprint 6 — daily BigQuery push)

REAL-WORLD ANALOGY:
  This is the office back door. Cloud Scheduler shows its badge
  (X-Aurora-Internal token), the door opens, the closure / sweep /
  reminder job runs, the door closes. Customers never see this door.
"""

import datetime
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aurora_shared.database import (
    get_db,
    SubscriptionPayment,
    Subscription,
    Invitation,
    ActionLog,
)


router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


# ─────────────────────────────────────────────────────────────
# Auth gate (Appendix K §1D — OIDC cron auth migration)
# ─────────────────────────────────────────────────────────────
# Accepts TWO authentication modes:
#   1. Legacy: `X-Aurora-Internal: <shared-secret>` header
#   2. NEW: `Authorization: Bearer <google-oidc-token>` where the OIDC
#      token was minted by Cloud Scheduler via
#      `--oidc-service-account-email=aurora-run@aurora-lts-prod.iam.gserviceaccount.com`
#      and `--oidc-token-audience=https://api-aurora-lts.com`.
#
# Either path is sufficient. The OIDC path is preferred (no shared
# secret); the legacy path stays during the migration window. Once
# all Cloud Scheduler jobs are migrated to OIDC, remove the legacy
# branch in a follow-up.
#
# OIDC verification reuses the proven `verify_google_oidc_token` from
# Track 4 Phase A (audience match + email allowlist + Google JWKS).
def _verify_internal_token(
    x_aurora_internal: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> None:
    # Try OIDC path first if Authorization: Bearer header is present
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token:
            try:
                from aurora_shared.services.auth_oidc import (
                    verify_google_oidc_token,
                    OidcVerificationError,
                )
            except Exception as e:
                # OIDC verification module not importable — fall through
                # to the legacy token path instead of failing closed
                print(f"[INTERNAL] OIDC verification module unavailable: {e}")
            else:
                audience = (os.getenv("AURORA_OIDC_AUDIENCE") or "https://api-aurora-lts.com").strip()
                try:
                    claims = verify_google_oidc_token(token, audience)
                except OidcVerificationError as e:
                    # Don't immediately reject — the caller may also have a
                    # legacy header. We'll fall through and try legacy.
                    print(f"[INTERNAL] OIDC verification failed: {e}")
                else:
                    # OIDC token is valid. Now check the email is in the
                    # cron allowlist (defaults to the OIDC SA allowlist
                    # for backward-compat with existing config).
                    email = (claims.get("email") or "").strip().lower()
                    allowlist_raw = (
                        os.getenv("AURORA_OIDC_CRON_ALLOWLIST")
                        or os.getenv("AURORA_OIDC_SA_ALLOWLIST")
                        or ""
                    )
                    allowlist = [e.strip().lower() for e in allowlist_raw.split(",") if e.strip()]
                    if not allowlist:
                        print("[INTERNAL] OIDC ok but no allowlist configured — rejecting")
                    elif email in allowlist:
                        print(f"[INTERNAL] OIDC-authenticated cron call: sa={email}")
                        return  # auth ok — let the handler run
                    else:
                        print(f"[INTERNAL] OIDC email {email} not in cron allowlist")

    # Legacy X-Aurora-Internal path
    expected = os.getenv("AURORA_INTERNAL_TOKEN", "")
    if not expected:
        # Dev mode: skip — same pattern as WhatsApp HMAC.
        # Production must set AURORA_INTERNAL_TOKEN in Secret Manager.
        if os.getenv("AURORA_RUNTIME", "").lower() == "cloud_run":
            # On Cloud Run a missing token is a misconfiguration.
            raise HTTPException(
                status_code=503,
                detail="AURORA_INTERNAL_TOKEN not configured",
            )
        return
    if x_aurora_internal != expected:
        raise HTTPException(
            status_code=403,
            detail="Bad internal token (need X-Aurora-Internal header OR a Google OIDC bearer token from an allowlisted SA)",
        )


# ═══════════════════════════════════════════════════════════════
# POST /api/v1/internal/close-month
# ═══════════════════════════════════════════════════════════════
class CloseMonthBody(BaseModel):
    period: Optional[str] = None  # "YYYY-MM"; default last calendar month
    dry_run: bool = False


@router.post("/close-month")
def close_month_endpoint(
    payload: CloseMonthBody,
    db: Session = Depends(get_db),
    _: None = Depends(_verify_internal_token),
):
    """
    Cloud Scheduler hits this once a month.
    Runs revenue_share.close_month() for the requested period (default
    = last calendar month).

    Returns the closure summary so Cloud Logging captures the run details.
    """
    from app.services.billing import close_month

    period = payload.period
    if not period:
        # Default: last calendar month
        today = datetime.date.today()
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - datetime.timedelta(days=1)
        period = last_month_end.strftime("%Y-%m")

    try:
        summary = close_month(period=period, db=db, dry_run=payload.dry_run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.add(ActionLog(
        business_id=None,
        status="rev_share.close_month",
        detail=(
            f"period={period} examined={summary['rows_examined']} "
            f"payable={summary['rows_payable']} held={summary['rows_held']} "
            f"payouts={len(summary['payouts_created'])} dry_run={payload.dry_run}"
        ),
    ))
    db.commit()

    return {"ok": True, **summary}


# ═══════════════════════════════════════════════════════════════
# POST /api/v1/internal/expire-invitations
# ═══════════════════════════════════════════════════════════════
@router.post("/expire-invitations")
def expire_invitations_endpoint(
    db: Session = Depends(get_db),
    _: None = Depends(_verify_internal_token),
):
    """Bulk-expire invitations past their expires_at."""
    from aurora_shared.services.identity import expire_old_invitations
    count = expire_old_invitations(db=db)
    return {"ok": True, "expired_count": count}


# ═══════════════════════════════════════════════════════════════
# POST /api/v1/internal/charge-trial-ends
# ═══════════════════════════════════════════════════════════════
class ChargeTrialEndsBody(BaseModel):
    dry_run: bool = False
    limit: int = 100


@router.post("/charge-trial-ends")
def charge_trial_ends_endpoint(
    payload: ChargeTrialEndsBody,
    db: Session = Depends(get_db),
    _: None = Depends(_verify_internal_token),
):
    """
    Sweep SubscriptionPayment rows whose attempted_at is in the past
    and status='scheduled'. Run them through PayPlus. On success call
    revenue_share.accrue_on_charge_success() so the engaged accountant's
    rev-share is credited automatically.

    NOTE: this is a stub-mode-friendly sweep — the actual PayPlus
    integration ships the moment provider creds are populated.
    """
    from app.services.billing import accrue_on_charge_success
    from app.services.onboarding.payplus_client import payplus_charge

    now = datetime.datetime.utcnow()
    pending = (
        db.query(SubscriptionPayment)
        .filter(
            SubscriptionPayment.status == "scheduled",
            SubscriptionPayment.attempted_at <= now,
        )
        .order_by(SubscriptionPayment.attempted_at.asc())
        .limit(min(max(payload.limit, 1), 1000))
        .all()
    )

    summary = {
        "examined": len(pending),
        "succeeded": 0,
        "failed": 0,
        "rev_share_accrued": 0,
        "dry_run": payload.dry_run,
    }
    if payload.dry_run:
        return {"ok": True, **summary}

    for payment in pending:
        # Look up the payment method bound to the subscription
        sub = (
            db.query(Subscription)
            .filter(Subscription.id == payment.subscription_id)
            .first()
        )
        if not sub or not sub.payment_method_id:
            payment.status = "failed"
            payment.failed_at = now
            payment.failure_code = "no_payment_method"
            payment.failure_message = "Subscription has no payment_method_id"
            summary["failed"] += 1
            continue

        from aurora_shared.database import PaymentMethod
        pm = db.query(PaymentMethod).filter(PaymentMethod.id == sub.payment_method_id).first()
        if not pm:
            payment.status = "failed"
            payment.failed_at = now
            payment.failure_code = "no_payment_method"
            summary["failed"] += 1
            continue

        try:
            charge = payplus_charge(
                provider_token=pm.provider_token,
                amount_minor_units=int(payment.amount_minor_units),
                currency=payment.currency or "ILS",
                idempotency_key=payment.idempotency_key,
                description=f"Aurora subscription {payment.period_start.date()}",
            )
        except NotImplementedError as e:
            payment.status = "failed"
            payment.failed_at = now
            payment.failure_code = "backend_unavailable"
            payment.failure_message = str(e)[:200]
            summary["failed"] += 1
            continue
        except Exception as e:
            payment.status = "failed"
            payment.failed_at = now
            payment.failure_code = "transport_error"
            payment.failure_message = str(e)[:200]
            summary["failed"] += 1
            continue

        if charge["status"] == "succeeded":
            payment.status = "succeeded"
            payment.succeeded_at = now
            payment.provider_charge_id = charge["provider_charge_id"]
            summary["succeeded"] += 1
            db.commit()

            # Accrue revenue share
            try:
                ledger_row = accrue_on_charge_success(
                    subscription_payment_id=payment.id, db=db,
                )
                if ledger_row:
                    summary["rev_share_accrued"] += 1
            except Exception as e:
                print(f"[INTERNAL] rev-share accrual failed for payment {payment.id}: {e}")

            # Subscription transitions: trialing → active
            if sub.status == "trialing":
                sub.status = "active"
                sub.started_at = now
                db.commit()
        else:
            payment.status = "failed"
            payment.failed_at = now
            payment.failure_code = charge.get("failure_code")
            payment.failure_message = charge.get("failure_message")
            summary["failed"] += 1

        db.commit()

    db.add(ActionLog(
        business_id=None,
        status="billing.charge_trial_ends",
        detail=(
            f"examined={summary['examined']} "
            f"succeeded={summary['succeeded']} "
            f"failed={summary['failed']} "
            f"accrued={summary['rev_share_accrued']}"
        ),
    ))
    db.commit()

    return {"ok": True, **summary}


# ═══════════════════════════════════════════════════════════════
# POST /api/v1/internal/smart-reminders
# ═══════════════════════════════════════════════════════════════
class SmartRemindersBody(BaseModel):
    dry_run: bool = False


@router.post("/smart-reminders")
async def smart_reminders_endpoint(
    payload: SmartRemindersBody,
    db: Session = Depends(get_db),
    _: None = Depends(_verify_internal_token),
):
    """
    Cloud Scheduler hits this every ~6 hours. Sweeps stale flows and
    sends gentle re-engagement messages.
    """
    from app.services.smart_reminders import run_reminders
    summary = await run_reminders(db=db, dry_run=payload.dry_run)
    return {"ok": True, **summary}


# ═══════════════════════════════════════════════════════════════
# POST /api/v1/internal/audit-export
# ═══════════════════════════════════════════════════════════════
class AuditExportBody(BaseModel):
    batch_size: int = 1000


@router.post("/audit-export")
def audit_export_endpoint_internal(
    payload: AuditExportBody,
    db: Session = Depends(get_db),
    _: None = Depends(_verify_internal_token),
):
    """
    Cloud Scheduler hits this once a day to push ActionLog + ItaAuditLog
    rows into BigQuery (when AUDIT_BIGQUERY_BACKEND='gcp'; stub mode
    writes to /tmp).
    """
    from app.services.compliance import export_audit_to_bigquery
    summary = export_audit_to_bigquery(db=db, batch_size=payload.batch_size)
    return {"ok": True, **summary}


# ─────────────────────────────────────────────────────────────
# Appendix I Sprint 2 — Internal jobs
# ─────────────────────────────────────────────────────────────

@router.post("/prune-exec-events")
def prune_exec_events_endpoint(
    db: Session = Depends(get_db),
    _: None = Depends(_verify_internal_token),
):
    """
    Cloud Scheduler (cron `30 2 * * *`) — daily hygiene job.

    Deletes ExecEvent rows older than 30 days. The durable audit trail
    (ActionLog, ItaAuditLog) is hash-chain-bound and never pruned;
    only the operator-UX feed table is.

    Idempotent. Returns `{deleted: N}` on success.
    """
    import datetime
    from sqlalchemy import text as _sql_text

    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    try:
        result = db.execute(
            _sql_text("DELETE FROM exec_events WHERE created_at < :cutoff"),
            {"cutoff": cutoff},
        )
        db.commit()
        deleted = int(result.rowcount or 0)
        print(f"[PRUNE_EXEC_EVENTS] Deleted {deleted} rows older than {cutoff.isoformat()}")
        return {"ok": True, "deleted": deleted, "cutoff": cutoff.isoformat()}
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[PRUNE_EXEC_EVENTS] failed: {e}")
        return {"ok": False, "error": str(e)[:200]}


@router.post("/eod-brief")
async def eod_brief_endpoint(
    db: Session = Depends(get_db),
    _: None = Depends(_verify_internal_token),
):
    """
    Cloud Scheduler (cron `0 15 * * 0-4`, i.e., 18:00 IL Sun–Thu) — daily
    operator brief.

    Builds a one-line summary from `build_dashboard_summary()` and sends
    it to the CEO's WhatsApp via the approved `aurora_eod_brief` template.

    Required env:
      • AURORA_CEO_WHATSAPP_E164  — destination phone in E.164 (e.g., +972508994296)

    Defensive: a missing env or send failure logs but never raises 5xx
    (Cloud Scheduler retries kick in on 5xx; we want clean exits).
    """
    import os

    ceo_phone = (os.getenv("AURORA_CEO_WHATSAPP_E164") or "").strip()
    if not ceo_phone:
        print("[EOD_BRIEF] skipped — AURORA_CEO_WHATSAPP_E164 not set")
        return {"ok": True, "skipped": "no_ceo_phone"}

    # Compose the brief
    try:
        from app.services.exec_aggregator import build_dashboard_summary
        summary = build_dashboard_summary(db)
    except Exception as e:
        print(f"[EOD_BRIEF] failed building summary: {e}")
        return {"ok": False, "error": "summary_failed"}

    rev_today = summary["revenue"]["today_net_nis"]
    rev_mtd = summary["revenue"]["mtd_net_nis"]
    active_orgs = summary["orgs"]["active"]
    in_flight = summary["invoices"]["in_flight"]
    payouts_pending = summary["payouts"]["pending_approval"]
    receipts_q = summary["receipts"]["review_queue_depth"]

    body_text = (
        f"📊 Aurora — סיכום יומי\n"
        f"הכנסות היום: ₪{rev_today:,.2f}\n"
        f"הכנסות חודש שוטף: ₪{rev_mtd:,.2f}\n"
        f"ארגונים פעילים: {active_orgs}\n"
        f"חשבוניות פתוחות: {in_flight}\n"
        f"תשלומים ממתינים: {payouts_pending}\n"
        f"קבלות לבדיקה: {receipts_q}"
    )

    # Send. Use plain text (24-hour window from any prior inbound from
    # the CEO's own phone — the founder messaged the bot at least once).
    # If outside the 24h window, the send fails and we log; founder can
    # message the bot once to refresh the window.
    try:
        from app.services.whatsapp_meta_client import send_text
        result = await send_text(
            to_phone=ceo_phone,
            text=body_text,
            db=db,
            user_id=None,
        )
    except Exception as e:
        print(f"[EOD_BRIEF] send failure: {e}")
        return {"ok": False, "error": "send_failed", "detail": str(e)[:200]}

    # Audit
    try:
        from aurora_shared.services.exec_events import publish_exec_event
        publish_exec_event(
            db,
            kind="eod_brief_sent",
            severity="info",
            title=f"EOD brief sent to {ceo_phone}",
            detail=f"rev_today=₪{rev_today:,.2f} rev_mtd=₪{rev_mtd:,.2f} active_orgs={active_orgs}",
        )
    except Exception:
        pass

    print(f"[EOD_BRIEF] sent → {ceo_phone} ok={result.get('ok')} wamid={result.get('wamid')}")
    return {"ok": True, "to": ceo_phone, "wamid": result.get("wamid"), "send_ok": result.get("ok")}
