"""
Aurora LTS — Admin Support / Tickets router (CEO Dashboard v3.1)
================================================================
Lightweight helpdesk for the pilot.

  GET    /api/v1/admin/tickets               — list (filters/pagination)
  POST   /api/v1/admin/tickets               — create
  GET    /api/v1/admin/tickets/{id}          — detail + message thread
  PATCH  /api/v1/admin/tickets/{id}          — status/priority/assignee/category
  POST   /api/v1/admin/tickets/{id}/messages — add a message / internal note

All require_admin; every mutation writes an AdminAuditEvent.
"""
from __future__ import annotations

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from aurora_shared.database import get_db, User, Organization
from aurora_shared.database.models import Ticket, TicketMessage
from aurora_shared.services.permissions import require_permission
from aurora_shared.services.admin_audit_service import write_admin_audit_event

router = APIRouter(prefix="/api/v1/admin/tickets", tags=["admin-support"])

_STATUSES = {"open", "in_progress", "waiting", "resolved", "closed"}
_PRIORITIES = {"low", "normal", "high", "critical"}
_EDITABLE = {"status", "priority", "category", "assigned_to_user_id"}


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request and request.client else None


def _ticket_or_404(db: Session, tid: int) -> Ticket:
    t = db.query(Ticket).filter(Ticket.id == tid).first()
    if not t:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Ticket not found"})
    return t


@router.get("")
def list_tickets(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    organization_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_permission("support", "read")),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Ticket)
    if status:
        query = query.filter(Ticket.status == status)
    if priority:
        query = query.filter(Ticket.priority == priority)
    if organization_id:
        query = query.filter(Ticket.organization_id == organization_id)
    if q:
        query = query.filter(Ticket.subject.ilike(f"%{q}%"))

    total = query.with_entities(func.count(Ticket.id)).scalar() or 0
    rows = (query.order_by(Ticket.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    org_ids = [t.organization_id for t in rows if t.organization_id]
    org_names = dict(db.query(Organization.id, Organization.display_name)
                     .filter(Organization.id.in_(org_ids)).all()) if org_ids else {}

    tickets = [{
        "id": t.id, "subject": t.subject, "status": t.status, "priority": t.priority,
        "category": t.category, "source": t.source,
        "organization_id": t.organization_id,
        "organization_name": org_names.get(t.organization_id),
        "assigned_to_user_id": t.assigned_to_user_id,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in rows]
    return {"total": total, "page": page, "page_size": page_size, "tickets": tickets}


class CreateTicket(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    body: Optional[str] = Field(None, max_length=4000)
    organization_id: Optional[int] = None
    priority: str = "normal"
    category: Optional[str] = "other"
    source: Optional[str] = "manual"


@router.post("")
def create_ticket(
    body: CreateTicket,
    request: Request,
    current_user: User = Depends(require_permission("support", "create")),
    db: Session = Depends(get_db),
) -> dict:
    priority = body.priority if body.priority in _PRIORITIES else "normal"
    t = Ticket(subject=body.subject, body=body.body, organization_id=body.organization_id,
               priority=priority, category=body.category, source=body.source,
               status="open", created_by_user_id=current_user.id)
    db.add(t)
    db.flush()
    write_admin_audit_event(db, actor=current_user, action="ticket.create",
                            entity_type="ticket", entity_id=t.id,
                            after={"subject": t.subject, "priority": priority},
                            ip=_client_ip(request))
    db.commit()
    return {"id": t.id, "status": t.status}


@router.get("/{ticket_id}")
def get_ticket(
    ticket_id: int,
    current_user: User = Depends(require_permission("support", "read")),
    db: Session = Depends(get_db),
) -> dict:
    t = _ticket_or_404(db, ticket_id)
    org_name = None
    if t.organization_id:
        org_name = db.query(Organization.display_name).filter(Organization.id == t.organization_id).scalar()
    messages = [{"id": m.id, "author_user_id": m.author_user_id, "body": m.body,
                 "is_internal": bool(m.is_internal),
                 "created_at": m.created_at.isoformat() if m.created_at else None}
                for m in db.query(TicketMessage).filter(TicketMessage.ticket_id == ticket_id)
                .order_by(TicketMessage.created_at.asc()).all()]
    return {
        "id": t.id, "subject": t.subject, "body": t.body, "status": t.status,
        "priority": t.priority, "category": t.category, "source": t.source,
        "organization_id": t.organization_id, "organization_name": org_name,
        "assigned_to_user_id": t.assigned_to_user_id,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        "messages": messages,
    }


class EditTicket(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    category: Optional[str] = None
    assigned_to_user_id: Optional[int] = None


@router.patch("/{ticket_id}")
def edit_ticket(
    ticket_id: int,
    body: EditTicket,
    request: Request,
    current_user: User = Depends(require_permission("support", "update")),
    db: Session = Depends(get_db),
) -> dict:
    t = _ticket_or_404(db, ticket_id)
    before, after = {}, {}
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in _STATUSES:
        raise HTTPException(400, detail={"error": "bad_status", "message": "invalid status"})
    if "priority" in data and data["priority"] not in _PRIORITIES:
        raise HTTPException(400, detail={"error": "bad_priority", "message": "invalid priority"})
    for field, value in data.items():
        if field in _EDITABLE and getattr(t, field) != value:
            before[field] = getattr(t, field)
            setattr(t, field, value)
            after[field] = value
    if after.get("status") in ("resolved", "closed") and not t.resolved_at:
        t.resolved_at = datetime.datetime.utcnow()
    if after:
        write_admin_audit_event(db, actor=current_user, action="ticket.update",
                                entity_type="ticket", entity_id=ticket_id,
                                before=before, after=after, ip=_client_ip(request))
        db.commit()
    return {"id": t.id, "updated_fields": list(after.keys())}


class AddMessage(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    is_internal: bool = True


@router.post("/{ticket_id}/messages")
def add_message(
    ticket_id: int,
    body: AddMessage,
    request: Request,
    current_user: User = Depends(require_permission("support", "update")),
    db: Session = Depends(get_db),
) -> dict:
    _ticket_or_404(db, ticket_id)
    m = TicketMessage(ticket_id=ticket_id, author_user_id=current_user.id,
                      body=body.body, is_internal=body.is_internal)
    db.add(m)
    db.flush()
    write_admin_audit_event(db, actor=current_user, action="ticket.message_add",
                            entity_type="ticket", entity_id=ticket_id,
                            after={"message_id": m.id, "internal": body.is_internal},
                            ip=_client_ip(request))
    db.commit()
    return {"id": m.id}
