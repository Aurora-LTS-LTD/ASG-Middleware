"""
Aurora LTS — Admin Customers router (CEO Dashboard v3.0)
========================================================
Customer/Business management + Customer-360 + Pilot Operations.

  GET    /api/v1/admin/customers              — list (filters/pagination)
  GET    /api/v1/admin/customers/{id}         — Customer-360 aggregate
  POST   /api/v1/admin/customers              — create
  PATCH  /api/v1/admin/customers/{id}         — edit
  POST   /api/v1/admin/customers/{id}/suspend — suspend (step-up if enabled)
  POST   /api/v1/admin/customers/{id}/archive — soft-delete (step-up if enabled)
  GET    /api/v1/admin/customers/{id}/notes   — pilot notes list
  POST   /api/v1/admin/customers/{id}/notes   — add pilot note + next action
  GET    /api/v1/admin/pilot                  — pilot cohort board

Every mutation writes an AdminAuditEvent and (where relevant) an AnalyticsEvent.
The Mac app holds NO logic — it only calls these.
"""
from __future__ import annotations

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from aurora_shared.database import (
    get_db, User, Organization, Membership, Subscription, Invoice,
)
from aurora_shared.database.models import (
    KycDocument, AccountantEngagement, CustomerNote, AdminAuditEvent, AnalyticsEvent,
)
from aurora_shared.services.permissions import require_permission
from aurora_shared.services.webauthn_service import require_step_up
from aurora_shared.services.admin_audit_service import write_admin_audit_event
from aurora_shared.services.analytics_service import emit_event

router = APIRouter(prefix="/api/v1/admin", tags=["admin-customers"])

_EDITABLE = {
    "display_name", "business_email", "business_phone", "website",
    "industry_code", "business_address", "city", "postal_code", "is_pilot",
}


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request and request.client else None


def _org_or_404(db: Session, org_id: int) -> Organization:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Customer not found"})
    return org


def _owner_map(db: Session, org_ids: list[int]) -> dict:
    if not org_ids:
        return {}
    rows = (db.query(Membership.organization_id, User.id, User.full_name, User.email,
                     User.whatsapp_phone_e164, User.telegram_user_id)
            .join(User, User.id == Membership.user_id)
            .filter(Membership.organization_id.in_(org_ids), Membership.role == "owner")
            .all())
    out = {}
    for org_id, uid, name, email, wa, tg in rows:
        out.setdefault(org_id, {"user_id": uid, "full_name": name, "email": email,
                                "whatsapp_linked": bool(wa), "telegram_linked": bool(tg)})
    return out


def _sub_map(db: Session, org_ids: list[int]) -> dict:
    if not org_ids:
        return {}
    rows = (db.query(Subscription).filter(Subscription.organization_id.in_(org_ids)).all())
    out = {}
    for s in rows:
        # prefer an active sub if multiple
        if s.organization_id not in out or s.status == "active":
            out[s.organization_id] = {"plan": s.plan, "status": s.status,
                                      "billing_cycle": s.billing_cycle,
                                      "trial_ends_at": s.trial_ends_at.isoformat() if s.trial_ends_at else None}
    return out


def _blocking_step(org: Organization, sub: Optional[dict]) -> str:
    if org.status == "suspended":
        return "Suspended"
    if (org.kyc_status or "pending") != "approved":
        return "KYC"
    if not sub or sub.get("status") not in ("active", "trialing"):
        return "Payment"
    return "Active"


# ─────────────────────────────────────────────────────────────
# LIST
# ─────────────────────────────────────────────────────────────
@router.get("/customers")
def list_customers(
    q: Optional[str] = Query(None, description="search display_name / tax_id"),
    status: Optional[str] = Query(None),
    kyc_status: Optional[str] = Query(None),
    is_pilot: Optional[bool] = Query(None),
    include_archived: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_permission("customers", "read")),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Organization)
    if not include_archived:
        query = query.filter(Organization.archived_at.is_(None))
    if status:
        query = query.filter(Organization.status == status)
    if kyc_status:
        query = query.filter(Organization.kyc_status == kyc_status)
    if is_pilot is not None:
        query = query.filter(Organization.is_pilot.is_(is_pilot))
    if q:
        like = f"%{q}%"
        query = query.filter((Organization.display_name.ilike(like)) | (Organization.tax_id.ilike(like)))

    total = query.with_entities(func.count(Organization.id)).scalar() or 0
    orgs = (query.order_by(Organization.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    ids = [o.id for o in orgs]

    owners = _owner_map(db, ids)
    subs = _sub_map(db, ids)
    member_counts = dict(db.query(Membership.organization_id, func.count(Membership.id))
                         .filter(Membership.organization_id.in_(ids))
                         .group_by(Membership.organization_id).all()) if ids else {}
    accountant_orgs = {r[0] for r in db.query(AccountantEngagement.organization_id)
                       .filter(AccountantEngagement.organization_id.in_(ids),
                               AccountantEngagement.status == "active").all()} if ids else set()

    customers = []
    for o in orgs:
        owner = owners.get(o.id, {})
        sub = subs.get(o.id)
        customers.append({
            "id": o.id, "display_name": o.display_name, "tax_id": o.tax_id,
            "legal_structure": o.legal_structure, "status": o.status,
            "kyc_status": o.kyc_status or "pending", "is_pilot": bool(o.is_pilot),
            "archived": o.archived_at is not None,
            "owner_name": owner.get("full_name"), "owner_email": owner.get("email"),
            "whatsapp_linked": owner.get("whatsapp_linked", False),
            "accountant_linked": o.id in accountant_orgs,
            "subscription": sub,
            "member_count": int(member_counts.get(o.id, 0)),
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })
    return {"total": total, "page": page, "page_size": page_size, "customers": customers}


# ─────────────────────────────────────────────────────────────
# CUSTOMER-360
# ─────────────────────────────────────────────────────────────
@router.get("/customers/{org_id}")
def customer_360(
    org_id: int,
    current_user: User = Depends(require_permission("customers", "read")),
    db: Session = Depends(get_db),
) -> dict:
    org = _org_or_404(db, org_id)
    owner = _owner_map(db, [org_id]).get(org_id, {})
    sub = _sub_map(db, [org_id]).get(org_id)

    members = [{"user_id": uid, "full_name": name, "email": email, "role": role}
               for uid, name, email, role in
               db.query(User.id, User.full_name, User.email, Membership.role)
               .join(Membership, Membership.user_id == User.id)
               .filter(Membership.organization_id == org_id).all()]

    # Invoices link to the legacy business bridge.
    inv = {"count": 0, "total": 0.0, "paid": 0.0, "outstanding": 0.0}
    if org.legacy_business_id:
        rows = db.query(Invoice.amount_total, Invoice.amount_paid, Invoice.payment_status) \
            .filter(Invoice.business_id == org.legacy_business_id).all()
        inv["count"] = len(rows)
        inv["total"] = round(sum((r[0] or 0) for r in rows), 2)
        inv["paid"] = round(sum((r[1] or 0) for r in rows), 2)
        inv["outstanding"] = round(inv["total"] - inv["paid"], 2)

    kyc_docs = [{"id": str(d.id), "document_type": d.document_type, "status": d.status,
                 "created_at": d.created_at.isoformat() if d.created_at else None}
                for d in db.query(KycDocument).filter(KycDocument.organization_id == org_id).all()]

    recent_audit = [{"action": a.action, "actor_user_id": a.actor_user_id,
                     "severity": a.severity,
                     "created_at": a.created_at.isoformat() if a.created_at else None}
                    for a in db.query(AdminAuditEvent)
                    .filter(AdminAuditEvent.entity_type == "organization",
                            AdminAuditEvent.entity_id == str(org_id))
                    .order_by(AdminAuditEvent.created_at.desc()).limit(10).all()]

    notes = [{"id": n.id, "body": n.body, "next_action": n.next_action,
              "is_resolved": bool(n.is_resolved), "author_user_id": n.author_user_id,
              "created_at": n.created_at.isoformat() if n.created_at else None}
             for n in db.query(CustomerNote).filter(CustomerNote.organization_id == org_id)
             .order_by(CustomerNote.created_at.desc()).all()]

    return {
        "id": org.id, "display_name": org.display_name, "legal_structure": org.legal_structure,
        "tax_id": org.tax_id, "status": org.status, "kyc_status": org.kyc_status or "pending",
        "is_pilot": bool(org.is_pilot), "archived": org.archived_at is not None,
        "business_email": org.business_email, "business_phone": org.business_phone,
        "city": org.city, "created_at": org.created_at.isoformat() if org.created_at else None,
        "owner": owner, "members": members, "subscription": sub,
        "invoices": inv, "kyc_documents": kyc_docs,
        "integrations": {
            "whatsapp_linked": owner.get("whatsapp_linked", False),
            "telegram_linked": owner.get("telegram_linked", False),
            "accountant_linked": db.query(AccountantEngagement.id)
                .filter(AccountantEngagement.organization_id == org_id,
                        AccountantEngagement.status == "active").first() is not None,
        },
        "recent_audit": recent_audit, "notes": notes,
        "blocking_step": _blocking_step(org, sub),
    }


# ─────────────────────────────────────────────────────────────
# CREATE / EDIT
# ─────────────────────────────────────────────────────────────
class CreateCustomer(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=200)
    legal_structure: str = Field(..., description="osek_morshe | osek_patur | chevra_baam")
    tax_id: str = Field(..., min_length=8, max_length=15)
    business_email: Optional[str] = None
    business_phone: Optional[str] = None
    is_pilot: bool = False


@router.post("/customers")
def create_customer(
    body: CreateCustomer,
    request: Request,
    current_user: User = Depends(require_permission("customers", "create")),
    db: Session = Depends(get_db),
) -> dict:
    # Guard against silent duplicates — Israeli business/tax IDs are unique.
    if db.query(Organization.id).filter(Organization.tax_id == body.tax_id).first():
        raise HTTPException(
            status_code=409,
            detail={"error": "duplicate_tax_id",
                    "message": f"A customer with tax ID {body.tax_id} already exists."},
        )

    org = Organization(
        display_name=body.display_name, legal_structure=body.legal_structure,
        tax_id=body.tax_id, business_email=body.business_email,
        business_phone=body.business_phone, is_pilot=body.is_pilot,
        status="active", kyc_status="pending",
    )
    db.add(org)
    db.flush()
    write_admin_audit_event(db, actor=current_user, action="customer.create",
                            entity_type="organization", entity_id=org.id,
                            after={"display_name": org.display_name, "tax_id": org.tax_id,
                                   "is_pilot": org.is_pilot},
                            ip=_client_ip(request))
    # Capture values before commit (expire_on_commit would otherwise reload them).
    org_id, org_name = org.id, org.display_name
    db.commit()                       # org + audit durable FIRST

    # Analytics rides AFTER the commit so the organization row already exists for
    # the analytics_events FK. emit_event is best-effort and can never raise.
    emit_event(db, event_type="customer_created", organization_id=org_id,
               user_id=current_user.id, actor="admin")
    db.commit()
    return {"id": org_id, "display_name": org_name, "status": "active"}


class EditCustomer(BaseModel):
    display_name: Optional[str] = None
    business_email: Optional[str] = None
    business_phone: Optional[str] = None
    website: Optional[str] = None
    industry_code: Optional[str] = None
    business_address: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    is_pilot: Optional[bool] = None


@router.patch("/customers/{org_id}")
def edit_customer(
    org_id: int,
    body: EditCustomer,
    request: Request,
    current_user: User = Depends(require_permission("customers", "update")),
    db: Session = Depends(get_db),
) -> dict:
    org = _org_or_404(db, org_id)
    before, after = {}, {}
    for field, value in body.model_dump(exclude_unset=True).items():
        if field in _EDITABLE and getattr(org, field) != value:
            before[field] = getattr(org, field)
            setattr(org, field, value)
            after[field] = value
    if after:
        write_admin_audit_event(db, actor=current_user, action="customer.update",
                                entity_type="organization", entity_id=org_id,
                                before=before, after=after, ip=_client_ip(request))
        db.commit()
    return {"id": org.id, "updated_fields": list(after.keys())}


# ─────────────────────────────────────────────────────────────
# SUSPEND / ARCHIVE (destructive — step-up if enabled)
# ─────────────────────────────────────────────────────────────
@router.post("/customers/{org_id}/suspend")
def suspend_customer(
    org_id: int,
    request: Request,
    current_user: User = Depends(require_permission("customers", "suspend")),
    _step_up: int = Depends(require_step_up("customer_suspend")),
    db: Session = Depends(get_db),
) -> dict:
    org = _org_or_404(db, org_id)
    before = {"status": org.status}
    org.status = "suspended"
    write_admin_audit_event(db, actor=current_user, action="customer.suspend",
                            entity_type="organization", entity_id=org_id,
                            before=before, after={"status": "suspended"},
                            ip=_client_ip(request), severity="warning")
    emit_event(db, event_type="customer_suspended", organization_id=org_id,
               user_id=current_user.id, actor="admin")
    db.commit()
    return {"id": org_id, "status": "suspended"}


@router.post("/customers/{org_id}/archive")
def archive_customer(
    org_id: int,
    request: Request,
    current_user: User = Depends(require_permission("customers", "archive")),
    _step_up: int = Depends(require_step_up("customer_archive")),
    db: Session = Depends(get_db),
) -> dict:
    org = _org_or_404(db, org_id)
    before = {"status": org.status, "archived_at": None}
    org.archived_at = datetime.datetime.utcnow()
    org.status = "closed"
    write_admin_audit_event(db, actor=current_user, action="customer.archive",
                            entity_type="organization", entity_id=org_id,
                            before=before,
                            after={"status": "closed", "archived_at": org.archived_at.isoformat()},
                            ip=_client_ip(request), severity="warning")
    emit_event(db, event_type="customer_archived", organization_id=org_id,
               user_id=current_user.id, actor="admin")
    db.commit()
    return {"id": org_id, "archived": True}


# ─────────────────────────────────────────────────────────────
# NOTES (Pilot Operations)
# ─────────────────────────────────────────────────────────────
class CreateNote(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)
    next_action: Optional[str] = Field(None, max_length=500)


@router.get("/customers/{org_id}/notes")
def list_notes(
    org_id: int,
    current_user: User = Depends(require_permission("customers", "read")),
    db: Session = Depends(get_db),
) -> dict:
    _org_or_404(db, org_id)
    notes = [{"id": n.id, "body": n.body, "next_action": n.next_action,
              "is_resolved": bool(n.is_resolved), "author_user_id": n.author_user_id,
              "created_at": n.created_at.isoformat() if n.created_at else None}
             for n in db.query(CustomerNote).filter(CustomerNote.organization_id == org_id)
             .order_by(CustomerNote.created_at.desc()).all()]
    return {"notes": notes}


@router.post("/customers/{org_id}/notes")
def add_note(
    org_id: int,
    body: CreateNote,
    request: Request,
    current_user: User = Depends(require_permission("customers", "update")),
    db: Session = Depends(get_db),
) -> dict:
    _org_or_404(db, org_id)
    note = CustomerNote(organization_id=org_id, author_user_id=current_user.id,
                        body=body.body, next_action=body.next_action)
    db.add(note)
    db.flush()
    write_admin_audit_event(db, actor=current_user, action="customer.note_add",
                            entity_type="organization", entity_id=org_id,
                            after={"note_id": note.id}, ip=_client_ip(request))
    db.commit()
    return {"id": note.id}


# ─────────────────────────────────────────────────────────────
# KYC actions (v3.1 — Onboarding/KYC control; destructive → step-up)
# ─────────────────────────────────────────────────────────────
class KycReject(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class KycRequestDocs(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)


@router.post("/customers/{org_id}/kyc/approve")
def kyc_approve(
    org_id: int,
    request: Request,
    current_user: User = Depends(require_permission("kyc", "approve")),
    _step_up: int = Depends(require_step_up("kyc_approve")),
    db: Session = Depends(get_db),
) -> dict:
    org = _org_or_404(db, org_id)
    before = {"kyc_status": org.kyc_status}
    org.kyc_status = "approved"
    org.kyc_approved_at = datetime.datetime.utcnow()
    org.kyc_approved_by = current_user.id
    org.tax_id_verified = True
    # Approve any docs still pending review.
    db.query(KycDocument).filter(
        KycDocument.organization_id == org_id,
        KycDocument.status.in_(["pending_review", "pending_upload"]),
    ).update({"status": "approved", "reviewed_by_user_id": current_user.id,
              "reviewed_at": datetime.datetime.utcnow()}, synchronize_session=False)
    write_admin_audit_event(db, actor=current_user, action="kyc.approve",
                            entity_type="organization", entity_id=org_id,
                            before=before, after={"kyc_status": "approved"},
                            ip=_client_ip(request))
    emit_event(db, event_type="kyc_approved", organization_id=org_id,
               user_id=current_user.id, actor="admin")
    db.commit()
    return {"id": org_id, "kyc_status": "approved"}


@router.post("/customers/{org_id}/kyc/reject")
def kyc_reject(
    org_id: int,
    body: KycReject,
    request: Request,
    current_user: User = Depends(require_permission("kyc", "reject")),
    _step_up: int = Depends(require_step_up("kyc_reject")),
    db: Session = Depends(get_db),
) -> dict:
    org = _org_or_404(db, org_id)
    before = {"kyc_status": org.kyc_status}
    org.kyc_status = "rejected"
    org.kyc_rejection_reason = body.reason
    write_admin_audit_event(db, actor=current_user, action="kyc.reject",
                            entity_type="organization", entity_id=org_id,
                            before=before, after={"kyc_status": "rejected", "reason": body.reason},
                            ip=_client_ip(request), severity="warning")
    emit_event(db, event_type="kyc_rejected", organization_id=org_id,
               user_id=current_user.id, actor="admin", properties={"reason": body.reason})
    db.commit()
    return {"id": org_id, "kyc_status": "rejected"}


@router.post("/customers/{org_id}/kyc/request-docs")
def kyc_request_docs(
    org_id: int,
    body: KycRequestDocs,
    request: Request,
    current_user: User = Depends(require_permission("kyc", "update")),
    db: Session = Depends(get_db),
) -> dict:
    org = _org_or_404(db, org_id)
    # Re-open KYC for more documents + leave an internal note of the request.
    before = {"kyc_status": org.kyc_status}
    new_status = "pending"
    org.kyc_status = new_status
    db.add(CustomerNote(organization_id=org_id, author_user_id=current_user.id,
                        body=f"Requested more KYC docs: {body.message}",
                        next_action="Await documents"))
    write_admin_audit_event(db, actor=current_user, action="kyc.request_docs",
                            entity_type="organization", entity_id=org_id,
                            before=before, after={"requested": body.message},
                            ip=_client_ip(request))
    emit_event(db, event_type="kyc_docs_requested", organization_id=org_id,
               user_id=current_user.id, actor="admin")
    db.commit()
    return {"id": org_id, "kyc_status": new_status}


# ─────────────────────────────────────────────────────────────
# CUSTOMER TIMELINE (v3.1 — merged audit + analytics + notes)
# ─────────────────────────────────────────────────────────────
@router.get("/customers/{org_id}/timeline")
def customer_timeline(
    org_id: int,
    limit: int = Query(100, ge=1, le=300),
    current_user: User = Depends(require_permission("customers", "read")),
    db: Session = Depends(get_db),
) -> dict:
    _org_or_404(db, org_id)
    items = []
    for a in (db.query(AdminAuditEvent)
              .filter(AdminAuditEvent.entity_type == "organization",
                      AdminAuditEvent.entity_id == str(org_id))
              .order_by(AdminAuditEvent.created_at.desc()).limit(limit).all()):
        items.append({"kind": "audit", "at": a.created_at.isoformat() if a.created_at else None,
                      "summary": a.action, "severity": a.severity, "actor_user_id": a.actor_user_id})
    for ev in (db.query(AnalyticsEvent)
               .filter(AnalyticsEvent.organization_id == org_id)
               .order_by(AnalyticsEvent.created_at.desc()).limit(limit).all()):
        items.append({"kind": "event", "at": ev.created_at.isoformat() if ev.created_at else None,
                      "summary": ev.event_type, "actor": ev.actor})
    for n in (db.query(CustomerNote)
              .filter(CustomerNote.organization_id == org_id)
              .order_by(CustomerNote.created_at.desc()).limit(limit).all()):
        items.append({"kind": "note", "at": n.created_at.isoformat() if n.created_at else None,
                      "summary": n.body, "next_action": n.next_action})
    items.sort(key=lambda x: x["at"] or "", reverse=True)
    return {"timeline": items[:limit]}


# ─────────────────────────────────────────────────────────────
# PILOT BOARD
# ─────────────────────────────────────────────────────────────
@router.get("/pilot")
def pilot_board(
    current_user: User = Depends(require_permission("customers", "read")),
    db: Session = Depends(get_db),
) -> dict:
    orgs = (db.query(Organization)
            .filter(Organization.is_pilot.is_(True), Organization.archived_at.is_(None))
            .order_by(Organization.id.desc()).all())
    ids = [o.id for o in orgs]
    owners = _owner_map(db, ids)
    subs = _sub_map(db, ids)
    open_notes = dict(db.query(CustomerNote.organization_id, func.count(CustomerNote.id))
                      .filter(CustomerNote.organization_id.in_(ids),
                              CustomerNote.is_resolved.is_(False))
                      .group_by(CustomerNote.organization_id).all()) if ids else {}

    board = []
    for o in orgs:
        sub = subs.get(o.id)
        board.append({
            "id": o.id, "display_name": o.display_name,
            "owner_name": owners.get(o.id, {}).get("full_name"),
            "status": o.status, "kyc_status": o.kyc_status or "pending",
            "subscription_status": (sub or {}).get("status"),
            "blocking_step": _blocking_step(o, sub),
            "open_notes": int(open_notes.get(o.id, 0)),
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })
    return {"total": len(board), "pilot": board}
