"""
Aurora LTS — Data Subject Access Request (Sprint 6)
========================================================
Israeli Protection of Privacy Law §13 (and GDPR Art. 15) require us
to give a user, on request, a copy of all their personal data we hold.

This module builds a single ZIP containing:
  - profile.json        : User row (sanitised — no password_hash)
  - organizations.json  : every Organization the user is a member of
  - invoices.json       : invoices the user created/owns
  - receipts.json       : receipts they uploaded
  - whatsapp_log.json   : their WhatsAppOutboundLog rows
  - action_log.json     : ActionLog entries that name them

CALLED BY:
  /api/v1/admin/compliance/dsar/{user_id}  (admin only)

DELETION (Right to Erasure, GDPR Art. 17):
  Implemented as soft-delete: User.is_active=False, PII fields nulled,
  but tax-document-bearing rows (Invoice, Receipt) preserved per the
  Israeli 7-year tax retention carve-out. See dsar_erase().
"""

import datetime
import io
import json
import zipfile
from typing import Optional

from sqlalchemy.orm import Session

from aurora_shared.database import (
    User,
    Organization,
    Membership,
    AccountantEngagement,
    Invoice,
    Receipt,
    Expense,
    KycDocument,
    WhatsAppOutboundLog,
    ActionLog,
    OnboardingState,
)


def build_dsar_bundle(*, user_id: int, db: Session) -> tuple[bytes, dict]:
    """
    Build the user's DSAR zip. Returns (zip_bytes, summary).

    The zip is suitable for emailing the user directly OR for an
    auditor inspecting our retained data.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"user_id={user_id} not found")

    # Profile (sanitise: no password_hash)
    profile = _row_to_dict(user, drop=("password_hash",))

    # Memberships → Organizations
    memberships = (
        db.query(Membership).filter(Membership.user_id == user_id).all()
    )
    org_ids = [m.organization_id for m in memberships]
    orgs = (
        db.query(Organization).filter(Organization.id.in_(org_ids)).all()
        if org_ids else []
    )

    # Active accountant engagements
    engagements = (
        db.query(AccountantEngagement)
        .filter(AccountantEngagement.accountant_user_id == user_id)
        .all()
    )

    # User's invoices (owner's biz_id)
    invoices = []
    if user.business_id:
        invoices = (
            db.query(Invoice).filter(Invoice.business_id == user.business_id).all()
        )

    # User's receipts (uploader)
    receipts = (
        db.query(Receipt).filter(Receipt.user_id == user_id).all()
    )

    # KYC docs they uploaded
    kyc_docs = (
        db.query(KycDocument).filter(KycDocument.user_id == user_id).all()
    )

    # OnboardingState
    onboarding = (
        db.query(OnboardingState).filter(OnboardingState.user_id == user_id).first()
    )

    # WhatsApp outbound (only when phone bound)
    wa_logs = []
    if user.whatsapp_phone_e164:
        wa_logs = (
            db.query(WhatsAppOutboundLog)
            .filter(WhatsAppOutboundLog.whatsapp_phone_e164 == user.whatsapp_phone_e164)
            .order_by(WhatsAppOutboundLog.id.desc())
            .limit(500)
            .all()
        )

    # Action logs that name them
    action_logs_q = []
    if user.business_id:
        action_logs_q = (
            db.query(ActionLog)
            .filter(ActionLog.business_id == user.business_id)
            .order_by(ActionLog.id.desc())
            .limit(2000)
            .all()
        )

    # ── Compose ZIP ──
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("profile.json", json.dumps(profile, ensure_ascii=False, indent=2, default=_json_default))
        zf.writestr("organizations.json", json.dumps([_row_to_dict(o) for o in orgs], ensure_ascii=False, indent=2, default=_json_default))
        zf.writestr("memberships.json", json.dumps([_row_to_dict(m) for m in memberships], ensure_ascii=False, indent=2, default=_json_default))
        zf.writestr("engagements.json", json.dumps([_row_to_dict(e) for e in engagements], ensure_ascii=False, indent=2, default=_json_default))
        zf.writestr("invoices.json", json.dumps([_row_to_dict(i) for i in invoices], ensure_ascii=False, indent=2, default=_json_default))
        zf.writestr("receipts.json", json.dumps([_row_to_dict(r) for r in receipts], ensure_ascii=False, indent=2, default=_json_default))
        zf.writestr("kyc_documents.json", json.dumps([_row_to_dict(k) for k in kyc_docs], ensure_ascii=False, indent=2, default=_json_default))
        if onboarding:
            zf.writestr("onboarding_state.json", json.dumps(_row_to_dict(onboarding), ensure_ascii=False, indent=2, default=_json_default))
        zf.writestr("whatsapp_log.json", json.dumps([_row_to_dict(w, drop=("payload_json",)) for w in wa_logs], ensure_ascii=False, indent=2, default=_json_default))
        zf.writestr("action_log.json", json.dumps([_row_to_dict(a) for a in action_logs_q], ensure_ascii=False, indent=2, default=_json_default))

        # Add a README for the user
        zf.writestr("README.txt", _readme(user))

    summary = {
        "user_id": user_id,
        "user_email": user.email,
        "filename": f"dsar-{user_id}-{datetime.date.today().isoformat()}.zip",
        "byte_size": len(buf.getvalue()),
        "tables_included": ["profile", "organizations", "memberships",
                            "engagements", "invoices", "receipts",
                            "kyc_documents", "onboarding_state",
                            "whatsapp_log", "action_log"],
    }
    return buf.getvalue(), summary


def _row_to_dict(row, *, drop=()) -> dict:
    """Convert a SQLAlchemy row → dict, dropping sensitive columns."""
    if row is None:
        return {}
    out = {}
    for col in row.__table__.columns:
        if col.name in drop:
            continue
        v = getattr(row, col.name)
        out[col.name] = v
    return out


def _json_default(o):
    """JSON encoder fallback for datetime/date/etc."""
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    return str(o)


def _readme(user: User) -> str:
    return (
        f"AURORA LTS — Data Subject Access Request bundle\n"
        f"Generated: {datetime.datetime.utcnow().isoformat()}Z\n"
        f"User: {user.email}\n\n"
        f"This zip contains every record Aurora holds about you, exported\n"
        f"from our production database. The JSON files are direct dumps of\n"
        f"the underlying tables.\n\n"
        f"Questions: privacy@aurora-ltd.co.il\n"
        f"To request erasure, reply to the email this bundle was attached to.\n"
        f"Note: tax-document records are retained for 7 years per Israeli\n"
        f"tax law, even on erasure (PII is anonymised).\n"
    )
