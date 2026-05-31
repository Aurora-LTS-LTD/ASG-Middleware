"""
Aurora LTS — Admin Executive Router (Appendix H — Tier 1 CEO Dashboard)
========================================================================

Read-mostly endpoints that feed the CEO Executive Dashboard at
admin.aurora-ltd.co.il/executive.

Endpoints:
  GET  /api/v1/admin/exec/dashboard-summary    Mission Control aggregate
  GET  /api/v1/admin/exec/finance-summary       Financial Command detail
  GET  /api/v1/admin/exec/whatsapp-analytics    WhatsApp Operations Hub
  GET  /api/v1/admin/exec/templates             Operational Templates list
  POST /api/v1/admin/exec/templates             Create template (Tier 1.5)
  PATCH /api/v1/admin/exec/templates/{id}       Update template (Tier 1.5)
  GET  /api/v1/admin/exec/events?since=<id>     Alert Stream poller
  POST /api/v1/admin/exec/events                Synthetic event (manual / testing)

All endpoints are IAP-gated via Depends(require_admin) and accept:
  • OIDC service-to-service tokens (from aurora-admin-ui via metadata-server)
  • Break-glass JWTs (Track 3)
  • IAP-authenticated admin JWTs (manual probes from the browser)
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional, List
import os as _os      # relocated from the extracted copilot block
import json as _json  # relocated from the extracted copilot block

from fastapi import APIRouter, Depends, Query, HTTPException, Body, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from aurora_shared.database import (
    get_db, User, VerticalTemplate, ExecEvent,
    BusinessCategory, Organization,
    CopilotConversation, CopilotMessage, CopilotProvisioningRun, ClaudeApiUsage,
    GeminiRun, DailyBriefCard, Receipt,
    # Sprint 5 / Appendix M — Pre-Armed Autonomous Architecture
    ProjectConstraint, HcarlPolicyState, CausalInsight,
    FederatedSyncLog, GrowthMilestone,
    # Sprint 8.2.5 — Accountant seed endpoint dependencies
    AccountantEngagement, ActionLog,
)
from aurora_shared.middleware.auth_middleware import require_admin
from app.services.exec_aggregator import build_dashboard_summary, build_finance_summary
from app.services.whatsapp_analytics import get_whatsapp_analytics
from aurora_shared.services.exec_events import publish_exec_event, recent_events_since

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/exec", tags=["admin-exec"])


# ─────────────────────────────────────────────────────────────
# Dashboard summary — Mission Control
# ─────────────────────────────────────────────────────────────
@router.get("/dashboard-summary")
def dashboard_summary(
    no_diff: bool = Query(False, description="Skip since-last-visit diff + snapshot persist"),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """
    Aggregated KPIs + recent events for the executive landing page.

    Appendix I Sprint 2 — also computes `diff_since_last_visit` against the
    most recent CeoSessionSnapshot for this user (and persists a fresh one)
    unless `no_diff=true` is passed (used by background pollers that don't
    want to bump the "last visited" cursor).
    """
    summary = build_dashboard_summary(db)
    if not no_diff:
        try:
            from app.services.exec_aggregator import compute_since_last_visit_diff
            summary["diff_since_last_visit"] = compute_since_last_visit_diff(
                user_id=current_user.id,
                current_summary=summary,
                db=db,
                persist=True,
            )
        except Exception as e:
            log.warning("[dashboard-summary] diff computation failed (non-fatal): %s", e)
            summary["diff_since_last_visit"] = {
                "has_previous_visit": False,
                "last_visited_at": None,
                "deltas": [],
            }
    return summary


# ─────────────────────────────────────────────────────────────
# Finance summary — Financial Command
# ─────────────────────────────────────────────────────────────
@router.get("/finance-summary")
def finance_summary(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return build_finance_summary(db)


# ─────────────────────────────────────────────────────────────
# WhatsApp analytics — Operations Hub
# ─────────────────────────────────────────────────────────────
_ALLOWED_RANGES = {"24h", "7d", "30d"}


@router.get("/whatsapp-analytics")
def whatsapp_analytics(
    range: str = Query("24h", description="24h | 7d | 30d"),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    if range not in _ALLOWED_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"range must be one of {sorted(_ALLOWED_RANGES)}",
        )
    return get_whatsapp_analytics(db, range)


# ─────────────────────────────────────────────────────────────
# Vertical templates — Operational Templates
# ─────────────────────────────────────────────────────────────
class VerticalTemplateOut(BaseModel):
    id: int
    name: str
    business_type: str
    locale: str
    whatsapp_opening_flow_json: str
    invoice_preset_json: str
    receipt_categorization_rules_json: str
    vat_advisory_text: Optional[str]
    is_active: bool
    created_at: Optional[str]
    updated_at: Optional[str]


class VerticalTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    business_type: str = Field(min_length=1, max_length=40)
    locale: str = Field(default="he", min_length=2, max_length=8)
    whatsapp_opening_flow_json: str = Field(default="{}")
    invoice_preset_json: str = Field(default="{}")
    receipt_categorization_rules_json: str = Field(default="{}")
    vat_advisory_text: Optional[str] = None
    is_active: bool = True


class VerticalTemplatePatch(BaseModel):
    name: Optional[str] = None
    business_type: Optional[str] = None
    locale: Optional[str] = None
    whatsapp_opening_flow_json: Optional[str] = None
    invoice_preset_json: Optional[str] = None
    receipt_categorization_rules_json: Optional[str] = None
    vat_advisory_text: Optional[str] = None
    is_active: Optional[bool] = None


def _serialize_template(t: VerticalTemplate) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "business_type": t.business_type,
        "locale": t.locale,
        "whatsapp_opening_flow_json": t.whatsapp_opening_flow_json,
        "invoice_preset_json": t.invoice_preset_json,
        "receipt_categorization_rules_json": t.receipt_categorization_rules_json,
        "vat_advisory_text": t.vat_advisory_text,
        "is_active": bool(t.is_active),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


@router.get("/templates")
def list_templates(
    business_type: Optional[str] = Query(None, max_length=40),
    locale: Optional[str] = Query(None, max_length=8),
    include_inactive: bool = Query(False),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    q = db.query(VerticalTemplate)
    if business_type:
        q = q.filter(VerticalTemplate.business_type == business_type)
    if locale:
        q = q.filter(VerticalTemplate.locale == locale)
    if not include_inactive:
        q = q.filter(VerticalTemplate.is_active == True)  # noqa: E712

    rows = q.order_by(VerticalTemplate.business_type, VerticalTemplate.name).all()
    return {
        "total": len(rows),
        "templates": [_serialize_template(t) for t in rows],
    }


@router.post("/templates", status_code=201)
def create_template(
    body: VerticalTemplateCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    t = VerticalTemplate(
        name=body.name,
        business_type=body.business_type,
        locale=body.locale,
        whatsapp_opening_flow_json=body.whatsapp_opening_flow_json,
        invoice_preset_json=body.invoice_preset_json,
        receipt_categorization_rules_json=body.receipt_categorization_rules_json,
        vat_advisory_text=body.vat_advisory_text,
        is_active=body.is_active,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    publish_exec_event(
        db,
        kind="vertical_template_created",
        severity="info",
        title=f"Vertical template created: {t.name}",
        detail=f"id={t.id} business_type={t.business_type} locale={t.locale}",
        related_entity_type="vertical_template",
        related_entity_id=t.id,
    )
    return _serialize_template(t)


@router.patch("/templates/{template_id}")
def update_template(
    template_id: int,
    body: VerticalTemplatePatch,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    t = db.query(VerticalTemplate).filter(VerticalTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    patch_data = body.model_dump(exclude_unset=True)
    for k, v in patch_data.items():
        setattr(t, k, v)
    t.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(t)
    publish_exec_event(
        db,
        kind="vertical_template_updated",
        severity="info",
        title=f"Vertical template updated: {t.name}",
        detail=f"id={t.id} fields={','.join(patch_data.keys())}",
        related_entity_type="vertical_template",
        related_entity_id=t.id,
    )
    return _serialize_template(t)


# ─────────────────────────────────────────────────────────────
# Events — Alert Stream
# ─────────────────────────────────────────────────────────────
class ExecEventCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=200)
    severity: str = Field(default="info")
    detail: Optional[str] = None
    related_entity_type: Optional[str] = Field(default=None, max_length=40)
    related_entity_id: Optional[int] = None


@router.get("/events")
def list_events(
    since: int = Query(0, ge=0, description="Return events with id > since"),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    events = recent_events_since(db, since_id=since, limit=limit)
    cursor = events[-1]["id"] if events else since
    return {
        "since": since,
        "cursor": cursor,
        "events": events,
    }


@router.post("/events", status_code=201)
def create_event(
    body: ExecEventCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """
    Synthetic publish path — for testing the SSE loop end-to-end.
    Real services should call `exec_events.publish_exec_event` directly
    from inside their flow rather than this HTTP indirection.
    """
    new_id = publish_exec_event(
        db,
        kind=body.kind,
        title=body.title,
        severity=body.severity,
        detail=body.detail,
        related_entity_type=body.related_entity_type,
        related_entity_id=body.related_entity_id,
    )
    if new_id is None:
        raise HTTPException(status_code=500, detail="Event publish failed")
    return {"id": new_id, "kind": body.kind, "title": body.title}


# ═════════════════════════════════════════════════════════════════════
# Appendix I Sprint 2 — Dynamic Category & Branch Explorer (B-custom)
# ═════════════════════════════════════════════════════════════════════
# Self-referencing two-level taxonomy:
#   • L1 (sector / branch)  — e.g., "Construction"
#   • L2 (profession)        — e.g., "Electricity" under "Construction"
#   • L3 (organizations)     — mapped via Organization.category_id
#
# 8 endpoints:
#   GET    /api/v1/admin/exec/categories?tree=true|flat=true
#   POST   /api/v1/admin/exec/categories
#   PATCH  /api/v1/admin/exec/categories/{id}
#   DELETE /api/v1/admin/exec/categories/{id}
#   GET    /api/v1/admin/exec/categories/{id}/orgs
#   POST   /api/v1/admin/exec/categories/{id}/assign-org
#   POST   /api/v1/admin/exec/orgs/{org_id}/unassign-category
#
# Replaces the static /templates surface in the UI nav.

import re as _re
from sqlalchemy import func as _func


def _slugify(s: str) -> str:
    """Lowercase, ASCII-fold (best effort), kebab-case."""
    s = s.strip().lower()
    s = _re.sub(r"[^\w\s-]", "", s, flags=_re.UNICODE)
    s = _re.sub(r"[\s_-]+", "-", s)
    return s.strip("-") or "cat"


def _serialize_category(c: BusinessCategory, org_count: int = 0) -> dict:
    return {
        "id": c.id,
        "parent_id": c.parent_id,
        "name": c.name,
        "name_he": c.name_he,
        "name_ar": c.name_ar,
        "slug": c.slug,
        "level": c.level,
        "description": c.description,
        "icon_emoji": c.icon_emoji,
        "sort_order": c.sort_order,
        "is_active": bool(c.is_active),
        "org_count": int(org_count),
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


# Sprint 4 — Appendix L §3 DTO refactor.
# Wire-format Pydantic models for the categories HTTP CRUD inherit
# from CategoryCore (canonical DTO) — single source of truth for the
# shared fields (name, name_he, name_ar, icon_emoji, description,
# sort_order, is_active). The HTTP wire shape preserves backward compat:
# POST /categories takes flat `{name, parent_id?}` (no `level`
# discriminator required); the handler infers level from parent_id.
# The Copilot tools (copilot/tools.py) use the discriminated-union
# variant directly so Claude's tool schema is strict.
from aurora_shared.schemas.category_dto import (
    CategoryCore,
    CategoryPatch,           # canonical PATCH shape, exported as-is
    CategoryAssignOrg,       # canonical assign-org shape
)


class CategoryCreate(CategoryCore):
    """HTTP wire-format for POST /categories.

    Backward-compatible flat shape:
      • parent_id=None  → creates an L1 sector
      • parent_id=<id>  → creates an L2 profession under that sector

    Internally the handler picks the appropriate canonical DTO from
    `aurora_shared.schemas.category_dto` based on `parent_id`.
    """
    parent_id: Optional[int] = None


# Backward-compat alias (legacy callsites referencing AssignOrgBody)
AssignOrgBody = CategoryAssignOrg


@router.get("/categories")
def list_categories(
    tree: bool = Query(False, description="Return as nested tree (L1 with children L2)"),
    flat: bool = Query(False, description="Return as flat list (default)"),
    include_inactive: bool = Query(False),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    q = db.query(BusinessCategory)
    if not include_inactive:
        q = q.filter(BusinessCategory.is_active == True)  # noqa: E712
    rows = q.order_by(
        BusinessCategory.level,
        BusinessCategory.sort_order,
        BusinessCategory.name,
    ).all()

    # Compute org counts per category in one query
    count_rows = (
        db.query(Organization.category_id, _func.count(Organization.id))
        .filter(Organization.category_id.isnot(None))
        .group_by(Organization.category_id)
        .all()
    )
    counts = {cid: int(c) for cid, c in count_rows}

    serialized = [_serialize_category(c, counts.get(c.id, 0)) for c in rows]

    if tree:
        l1 = [s for s in serialized if s["level"] == 1]
        l2_by_parent: dict[int, list[dict]] = {}
        for s in serialized:
            if s["level"] == 2 and s["parent_id"] is not None:
                l2_by_parent.setdefault(s["parent_id"], []).append(s)
        for node in l1:
            node["children"] = l2_by_parent.get(node["id"], [])
            # Roll up org count
            node["org_count_subtree"] = node["org_count"] + sum(
                ch["org_count"] for ch in node["children"]
            )
        return {"total": len(l1), "tree": l1}

    return {"total": len(serialized), "categories": serialized}


@router.post("/categories", status_code=201)
def create_category(
    body: CategoryCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    # Determine level + validate parent
    if body.parent_id is None:
        level = 1
        parent: Optional[BusinessCategory] = None
        slug_prefix = ""
    else:
        parent = db.query(BusinessCategory).filter(BusinessCategory.id == body.parent_id).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent category not found")
        if parent.level != 1:
            raise HTTPException(
                status_code=400,
                detail="Sub-categories can only be created under a level-1 sector",
            )
        level = 2
        slug_prefix = f"{parent.slug}/"

    name_slug = _slugify(body.name)
    candidate = f"{slug_prefix}{name_slug}"
    # Ensure slug uniqueness (append -2, -3, ... on collision)
    suffix = 1
    while db.query(BusinessCategory).filter(BusinessCategory.slug == candidate).first():
        suffix += 1
        candidate = f"{slug_prefix}{name_slug}-{suffix}"

    cat = BusinessCategory(
        parent_id=body.parent_id,
        name=body.name,
        name_he=body.name_he,
        name_ar=body.name_ar,
        slug=candidate,
        level=level,
        description=body.description,
        icon_emoji=body.icon_emoji,
        sort_order=body.sort_order,
        is_active=body.is_active,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)

    publish_exec_event(
        db,
        kind="category_created",
        severity="info",
        title=f"Category created: {cat.name} (L{cat.level})",
        detail=f"id={cat.id} slug={cat.slug} parent_id={cat.parent_id}",
        related_entity_type="business_category",
        related_entity_id=cat.id,
    )
    return _serialize_category(cat, 0)


@router.patch("/categories/{category_id}")
def update_category(
    category_id: int,
    body: CategoryPatch,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    cat = db.query(BusinessCategory).filter(BusinessCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    patch_data = body.model_dump(exclude_unset=True)
    changed_name = False
    for k, v in patch_data.items():
        setattr(cat, k, v)
        if k == "name":
            changed_name = True
    if changed_name:
        # Re-slugify under the existing parent's slug prefix
        prefix = ""
        if cat.parent_id:
            parent = db.query(BusinessCategory).filter(BusinessCategory.id == cat.parent_id).first()
            if parent:
                prefix = f"{parent.slug}/"
        name_slug = _slugify(cat.name)
        candidate = f"{prefix}{name_slug}"
        suffix = 1
        while (
            candidate != cat.slug
            and db.query(BusinessCategory).filter(BusinessCategory.slug == candidate).first()
        ):
            suffix += 1
            candidate = f"{prefix}{name_slug}-{suffix}"
        cat.slug = candidate

    cat.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(cat)

    org_count = (
        db.query(_func.count(Organization.id))
        .filter(Organization.category_id == cat.id)
        .scalar()
        or 0
    )
    publish_exec_event(
        db,
        kind="category_updated",
        severity="info",
        title=f"Category updated: {cat.name}",
        detail=f"id={cat.id} fields={','.join(patch_data.keys())}",
        related_entity_type="business_category",
        related_entity_id=cat.id,
    )
    return _serialize_category(cat, org_count)


@router.delete("/categories/{category_id}")
def delete_category(
    category_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    cat = db.query(BusinessCategory).filter(BusinessCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    # Block delete if children exist
    child_count = (
        db.query(_func.count(BusinessCategory.id))
        .filter(BusinessCategory.parent_id == category_id)
        .scalar()
        or 0
    )
    if child_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete: {child_count} sub-category(ies) exist. Delete or re-parent them first.",
        )

    # Block delete if orgs are mapped
    org_count = (
        db.query(_func.count(Organization.id))
        .filter(Organization.category_id == category_id)
        .scalar()
        or 0
    )
    if org_count > 0:
        sample = (
            db.query(Organization.id, Organization.display_name)
            .filter(Organization.category_id == category_id)
            .limit(5)
            .all()
        )
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Cannot delete: orgs are mapped to this category. Reassign them first.",
                "orgs_mapped_count": int(org_count),
                "sample": [{"id": s[0], "display_name": s[1]} for s in sample],
            },
        )

    cat_name = cat.name
    cat_slug = cat.slug
    db.delete(cat)
    db.commit()

    publish_exec_event(
        db,
        kind="category_deleted",
        severity="warning",
        title=f"Category deleted: {cat_name}",
        detail=f"id={category_id} slug={cat_slug}",
        related_entity_type="business_category",
        related_entity_id=category_id,
    )
    return {"ok": True, "deleted_id": category_id}


@router.get("/categories/{category_id}/orgs")
def list_category_orgs(
    category_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    if not db.query(BusinessCategory).filter(BusinessCategory.id == category_id).first():
        raise HTTPException(status_code=404, detail="Category not found")
    total = (
        db.query(_func.count(Organization.id))
        .filter(Organization.category_id == category_id)
        .scalar()
        or 0
    )
    rows = (
        db.query(Organization.id, Organization.display_name, Organization.status, Organization.kyc_status)
        .filter(Organization.category_id == category_id)
        .order_by(Organization.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "orgs": [
            {"id": r[0], "display_name": r[1], "status": r[2], "kyc_status": r[3]}
            for r in rows
        ],
    }


@router.post("/categories/{category_id}/assign-org")
def assign_org_to_category(
    category_id: int,
    body: AssignOrgBody,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    cat = db.query(BusinessCategory).filter(BusinessCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    if cat.level != 2:
        # Force orgs to map to a profession (L2), not a bare sector (L1)
        raise HTTPException(
            status_code=400,
            detail="Orgs can only be assigned to level-2 categories (professions)",
        )
    org = db.query(Organization).filter(Organization.id == body.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    prev_cat_id = org.category_id
    org.category_id = category_id
    db.commit()

    publish_exec_event(
        db,
        kind="org_category_assigned",
        severity="info",
        title=f"{org.display_name} → {cat.name}",
        detail=f"org_id={org.id} category_id={cat.id} prev_category_id={prev_cat_id}",
        related_entity_type="organization",
        related_entity_id=org.id,
    )
    return {
        "ok": True,
        "organization_id": org.id,
        "category_id": category_id,
        "category_slug": cat.slug,
        "previous_category_id": prev_cat_id,
    }


@router.post("/orgs/{org_id}/unassign-category")
def unassign_org_category(
    org_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    prev_cat_id = org.category_id
    if prev_cat_id is None:
        return {"ok": True, "organization_id": org.id, "category_id": None, "no_change": True}
    org.category_id = None
    db.commit()
    publish_exec_event(
        db,
        kind="org_category_unassigned",
        severity="info",
        title=f"{org.display_name} → uncategorized",
        detail=f"org_id={org.id} previous_category_id={prev_cat_id}",
        related_entity_type="organization",
        related_entity_id=org.id,
    )
    return {"ok": True, "organization_id": org.id, "category_id": None, "previous_category_id": prev_cat_id}


# ═════════════════════════════════════════════════════════════════════
# Appendix I Sprint 2 — Palette Index (F: ⌘K command palette)
# ═════════════════════════════════════════════════════════════════════
# Flat label+href pairs for fuzzy-search in the ⌘K command palette.
# Aggregates: static routes + recent invoices + recent orgs + categories +
# one-shot actions (audit export, prune events).
#
# Returns a single flat list per call — caller does client-side fuzzy
# scoring. Cached server-side TTL=30s to keep query cost negligible.

import time as _time

_PALETTE_CACHE: dict = {"ts": 0.0, "items": []}
_PALETTE_TTL_S = 30.0


@router.get("/palette-index")
def palette_index(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    now = _time.monotonic()
    if _PALETTE_CACHE["items"] and now - _PALETTE_CACHE["ts"] < _PALETTE_TTL_S:
        return {"items": _PALETTE_CACHE["items"], "cached": True}

    items: list[dict] = []

    # ── Static routes ──
    items.extend([
        {"kind": "route", "label": "Mission Control", "href": "/executive", "icon": "◐"},
        {"kind": "route", "label": "Financial Command", "href": "/executive/finance", "icon": "₪"},
        {"kind": "route", "label": "WhatsApp Operations", "href": "/executive/whatsapp", "icon": "✉"},
        {"kind": "route", "label": "Categories & Branches", "href": "/executive/categories", "icon": "◑"},
        {"kind": "route", "label": "Audit & Compliance", "href": "/executive/compliance", "icon": "◉"},
    ])

    # ── Recent invoices (last 30) ──
    try:
        inv_rows = (
            db.query(
                Invoice.id,
                Invoice.invoice_number,
                Invoice.amount_total,
                Invoice.status,
                Invoice.beneficiary_name,
            )
            .order_by(Invoice.id.desc())
            .limit(30)
            .all()
        )
        for r in inv_rows:
            label = f"Invoice {r.invoice_number or r.id}"
            if r.beneficiary_name:
                label += f" — {r.beneficiary_name}"
            sub = f"₪{(r.amount_total or 0):,.2f} ・ {r.status}"
            items.append({
                "kind": "invoice",
                "label": label,
                "sublabel": sub,
                "href": f"/executive/archive/{r.id}",
                "entity_id": r.id,
            })
    except Exception as e:
        log.warning("[palette] invoices query failed: %s", e)

    # ── Recent orgs (last 30) ──
    try:
        org_rows = (
            db.query(Organization.id, Organization.display_name, Organization.status, Organization.kyc_status)
            .order_by(Organization.id.desc())
            .limit(30)
            .all()
        )
        for r in org_rows:
            items.append({
                "kind": "org",
                "label": r[1] or f"Org #{r[0]}",
                "sublabel": f"{r[2] or 'active'} ・ KYC {r[3] or 'pending'}",
                "href": f"/executive/orgs/{r[0]}",
                "entity_id": r[0],
            })
    except Exception as e:
        log.warning("[palette] orgs query failed: %s", e)

    # ── Categories (all L1 + L2) ──
    try:
        cat_rows = (
            db.query(BusinessCategory.id, BusinessCategory.name, BusinessCategory.level, BusinessCategory.slug, BusinessCategory.icon_emoji)
            .filter(BusinessCategory.is_active == True)  # noqa
            .order_by(BusinessCategory.level, BusinessCategory.name)
            .all()
        )
        for r in cat_rows:
            items.append({
                "kind": "category",
                "label": f"{(r[4] or '')}{' ' if r[4] else ''}{r[1]}",
                "sublabel": f"L{r[2]} ・ {r[3]}",
                "href": f"/executive/categories#{r[3]}",
                "entity_id": r[0],
            })
    except Exception as e:
        log.warning("[palette] categories query failed: %s", e)

    # ── Actions ──
    items.extend([
        {"kind": "action", "label": "Run audit export now", "action": "audit_export", "icon": "↻"},
        {"kind": "action", "label": "Prune exec events", "action": "prune_exec_events", "icon": "✕"},
    ])

    _PALETTE_CACHE["ts"] = now
    _PALETTE_CACHE["items"] = items
    return {"items": items, "cached": False}


# ═════════════════════════════════════════════════════════════════════


# ═════════════════════════════════════════════════════════════════════
# Appendix J Sprint 3 — WebAuthn step-up (T3.3)
# ═════════════════════════════════════════════════════════════════════
# Endpoints:
#   POST /api/v1/admin/exec/webauthn/register/start
#   POST /api/v1/admin/exec/webauthn/register/finish
#   POST /api/v1/admin/exec/webauthn/assert/start?action=<verb>
#   POST /api/v1/admin/exec/webauthn/assert/finish
#   GET  /api/v1/admin/exec/webauthn/credentials  (list registered passkeys)
#
# All endpoints require_admin (the founder enrolling their own device).
# The assertion challenge is bound to an action verb (e.g.,
# "copilot_provision", "payout_approve") so step-up tokens cannot be
# reused across action types.


class WebauthnRegisterFinishBody(BaseModel):
    credential: dict
    device_label: Optional[str] = Field(default=None, max_length=120)


class WebauthnAssertFinishBody(BaseModel):
    action: str = Field(min_length=1, max_length=60)
    credential: dict


@router.post("/webauthn/register/start")
def webauthn_register_start(
    current_user: User = Depends(require_admin),
) -> dict:
    from aurora_shared.services.webauthn_service import begin_registration, WebauthnError
    try:
        options = begin_registration(current_user)
        return {"ok": True, "options": options}
    except WebauthnError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/webauthn/register/finish")
def webauthn_register_finish(
    body: WebauthnRegisterFinishBody,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    from aurora_shared.services.webauthn_service import finish_registration, WebauthnError
    try:
        cred = finish_registration(
            user=current_user,
            credential_dict=body.credential,
            device_label=body.device_label,
            db=db,
        )
    except WebauthnError as e:
        raise HTTPException(status_code=400, detail=str(e))

    publish_exec_event(
        db,
        kind="webauthn_credential_registered",
        severity="info",
        title=f"Passkey registered: {body.device_label or 'unnamed device'}",
        detail=f"credential_id={cred.id} user_id={current_user.id}",
        related_entity_type="webauthn_credential",
        related_entity_id=cred.id,
    )
    return {
        "ok": True,
        "credential_id": cred.id,
        "device_label": cred.device_label,
        "aaguid": cred.aaguid,
        "created_at": cred.created_at.isoformat() if cred.created_at else None,
    }


@router.post("/webauthn/assert/start")
def webauthn_assert_start(
    action: str = Query(..., min_length=1, max_length=60),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    from aurora_shared.services.webauthn_service import begin_assertion, WebauthnError
    try:
        options = begin_assertion(current_user, action, db)
        return {"ok": True, "action": action, "options": options}
    except WebauthnError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/webauthn/assert/finish")
def webauthn_assert_finish(
    body: WebauthnAssertFinishBody,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    from aurora_shared.services.webauthn_service import finish_assertion, WebauthnError
    try:
        token, cred_id = finish_assertion(
            user=current_user,
            action=body.action,
            credential_dict=body.credential,
            db=db,
        )
    except WebauthnError as e:
        raise HTTPException(status_code=400, detail=str(e))

    publish_exec_event(
        db,
        kind="webauthn_step_up_succeeded",
        severity="info",
        title=f"Step-up confirmed for action '{body.action}'",
        detail=f"credential_id={cred_id} user_id={current_user.id}",
        related_entity_type="webauthn_credential",
        related_entity_id=cred_id,
    )
    return {
        "ok": True,
        "action": body.action,
        "step_up_token": token,
        "credential_id": cred_id,
    }


@router.get("/webauthn/credentials")
def webauthn_list_credentials(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    from aurora_shared.database.models import WebauthnCredential
    rows = (
        db.query(WebauthnCredential)
        .filter(WebauthnCredential.user_id == current_user.id)
        .order_by(WebauthnCredential.id.desc())
        .all()
    )
    return {
        "total": len(rows),
        "credentials": [
            {
                "id": r.id,
                "device_label": r.device_label,
                "aaguid": r.aaguid,
                "transports": r.transports,
                "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
                "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
                "sign_count": r.sign_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


# ─────────────────────────────────────────────────────────────
# WebAuthn — Preflight diagnostic (Appendix K §1B)
# ─────────────────────────────────────────────────────────────
# Called by the frontend BEFORE attempting navigator.credentials.create
# so the UI can surface a clear diagnostic when the browser origin
# does NOT match the WebAuthn server's allowed origin list — instead
# of letting the ceremony fail silently with SecurityError.

@router.get("/webauthn/preflight")
def webauthn_preflight(
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    import os as _os
    from aurora_shared.database.models import WebauthnCredential

    rp_id = (_os.getenv("AURORA_WEBAUTHN_RP_ID") or "console.api-aurora-lts.com").strip()
    # Multi-origin support (Appendix K §1B Layer 2)
    raw = (
        _os.getenv("AURORA_WEBAUTHN_ALLOWED_ORIGINS")
        or _os.getenv("AURORA_WEBAUTHN_ORIGIN")
        or "https://console.api-aurora-lts.com"
    )
    allowed_origins = [o.strip() for o in raw.split(",") if o.strip()]

    browser_origin = (request.headers.get("origin") or "").strip()
    browser_origin_in_allowlist = browser_origin in allowed_origins

    cred_count = (
        db.query(WebauthnCredential)
        .filter(WebauthnCredential.user_id == current_user.id)
        .filter(WebauthnCredential.revoked_at.is_(None))
        .count()
    )

    return {
        "rp_id": rp_id,
        "rp_name": _os.getenv("AURORA_WEBAUTHN_RP_NAME", "Aurora LTS Executive"),
        "allowed_origins": allowed_origins,
        "browser_origin": browser_origin,
        "browser_origin_in_allowlist": browser_origin_in_allowlist,
        "registered_credentials": int(cred_count),
        "iap_email": current_user.email,
        "step_up_enforced": _os.getenv("AURORA_EXEC_REQUIRE_STEP_UP", "0") == "1",
    }




# ═════════════════════════════════════════════════════════════════════
# Sprint 4 — Vertex AI / Gemini multi-workload endpoints (Appendix L)
# ═════════════════════════════════════════════════════════════════════
#
# Sprint 4 introduces new Gemini-powered features ALONGSIDE the existing
# Claude-powered Copilot (which stays untouched). These endpoints use the
# Vertex AI provider via the LLM abstraction.
#
# Workloads:
#   • POST /api/v1/admin/exec/receipts/{id}/classify-with-gemini
#       Gemini Flash classifies an OCR'd receipt → expense category
#   • GET  /api/v1/admin/exec/llm/usage?range=24h|7d|30d
#       Cross-provider spend + token rollup (powers Mission Control tile)
#   • POST /api/v1/admin/exec/whatsapp/template-draft
#       Gemini Flash drafts a Meta-compliant WhatsApp template body
#   • POST /api/v1/internal/daily-insights-generate
#       (Lives in internal.py — Cloud Scheduler 07:00 IL cron)
#
# All endpoints require_admin. All persist a GeminiRun row for audit.

import time as _time_sprint4


class _ReceiptClassifyOut(BaseModel):
    receipt_id: int
    classification: dict
    gemini_run_id: int


@router.post("/receipts/{receipt_id}/classify-with-gemini")
async def receipts_classify_with_gemini(
    receipt_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Classify an OCR'd receipt with Gemini 1.5 Flash.

    Reads the receipt's OCR text + supplier + amount and asks Gemini for:
        {category, confidence, vat_eligible, rationale}

    Persists the result on Receipt.gemini_classification_json + writes a
    GeminiRun row. Cheap (~$0.00007 per call); meant to run on every new
    receipt automatically OR on-demand for re-classification.
    """
    receipt = db.query(Receipt).filter(Receipt.id == receipt_id).first()
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    # Build the input prompt from OCR-extracted fields
    ocr_text = getattr(receipt, "ocr_text_redacted", None) or ""
    supplier = getattr(receipt, "supplier_name", None) or "(unknown)"
    amount = getattr(receipt, "total_amount_minor_units", None) or 0
    amount_nis = amount / 100.0 if amount else 0.0

    prompt = (
        "You are a bookkeeping classification assistant for Israeli SMBs. "
        "Given a receipt's OCR text + supplier name + total amount, output "
        "a JSON object classifying the expense.\n\n"
        f"Supplier: {supplier}\n"
        f"Total (ILS): {amount_nis:,.2f}\n"
        f"OCR text (PII-redacted): {ocr_text[:2000]}\n\n"
        "Return JSON with these fields:\n"
        '  {\n'
        '    "category": "<one of: meals, fuel, supplies, utilities, '
        'rent, professional_services, equipment, marketing, other>",\n'
        '    "confidence": 0.0-1.0,\n'
        '    "vat_eligible": true/false,\n'
        '    "rationale": "<one sentence explaining the choice>"\n'
        '  }'
    )

    response_schema = {
        "type": "object",
        "properties": {
            "category": {"type": "string"},
            "confidence": {"type": "number"},
            "vat_eligible": {"type": "boolean"},
            "rationale": {"type": "string"},
        },
        "required": ["category", "confidence", "vat_eligible", "rationale"],
    }

    from app.services.llm import get_provider
    from app.services.llm.vertex_provider import (
        VertexConfigError,
        _get_default_fast_model,
    )

    t0 = _time_sprint4.monotonic()
    try:
        provider = get_provider("vertex_gemini")
        text, usage = await provider.one_shot(
            prompt,
            model=_get_default_fast_model(),
            max_tokens=512,
            response_json_schema=response_schema,
        )
    except VertexConfigError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Vertex AI not configured: {e}",
        )
    except Exception as e:
        log.warning("[receipts.classify] vertex call failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail=f"vertex call failed: {type(e).__name__}: {str(e)[:200]}",
        )

    duration_ms = int((_time_sprint4.monotonic() - t0) * 1000)

    # Parse the JSON output
    try:
        classification = _json.loads(text)
    except Exception as e:
        # Persist the failed run for audit, then 502
        run = GeminiRun(
            user_id=current_user.id,
            purpose="receipt_classify",
            related_entity_type="receipt",
            related_entity_id=receipt.id,
            model=usage.model,
            input_text=prompt[:4000],
            output_text=text[:4000],
            tokens_input=usage.tokens_input,
            tokens_output=usage.tokens_output,
            cost_usd=usage.cost_usd,
            status="failed",
            error=f"json_parse_failed: {e}",
            duration_ms=duration_ms,
        )
        db.add(run)
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=f"Gemini returned non-JSON; run {run.id} recorded for audit",
        )

    # Persist the GeminiRun + update Receipt
    run = GeminiRun(
        user_id=current_user.id,
        purpose="receipt_classify",
        related_entity_type="receipt",
        related_entity_id=receipt.id,
        model=usage.model,
        input_text=prompt[:4000],
        output_text=text[:4000],
        output_json=_json.dumps(classification, ensure_ascii=False),
        tokens_input=usage.tokens_input,
        tokens_output=usage.tokens_output,
        cost_usd=usage.cost_usd,
        status="success",
        duration_ms=duration_ms,
    )
    db.add(run)
    db.flush()  # populate run.id

    # Also write to llm_api_usage for the unified cost tile
    db.add(ClaudeApiUsage(
        user_id=current_user.id,
        conversation_id=None,
        model=usage.model,
        provider="vertex_gemini",
        tokens_input=usage.tokens_input,
        tokens_output=usage.tokens_output,
        tokens_cache_creation=0,
        tokens_cache_read=0,
    ))

    receipt.gemini_classification_json = _json.dumps(
        {**classification, "model": usage.model, "run_id": run.id},
        ensure_ascii=False,
    )
    receipt.gemini_classified_at = datetime.datetime.utcnow()

    db.commit()
    db.refresh(run)
    db.refresh(receipt)

    publish_exec_event(
        db,
        kind="receipt_classified",
        severity="info",
        title=f"Receipt {receipt.id}: {classification.get('category', '?')} ({classification.get('confidence', 0):.0%})",
        detail=(
            f"supplier={supplier} amount=₪{amount_nis:,.2f} "
            f"vat_eligible={classification.get('vat_eligible')} "
            f"model={usage.model}"
        ),
        related_entity_type="receipt",
        related_entity_id=receipt.id,
    )

    return {
        "receipt_id": receipt.id,
        "classification": classification,
        "gemini_run_id": run.id,
        "cost_usd": usage.cost_usd,
        "duration_ms": duration_ms,
    }


# ─────────────────────────────────────────────────────────────
# Cross-provider LLM usage aggregate (Sprint 4 — Mission Control tile)
# ─────────────────────────────────────────────────────────────

_LLM_USAGE_RANGES = {"24h": 1, "7d": 7, "30d": 30}


@router.get("/llm/usage")
def llm_usage_aggregate(
    range: str = Query("24h", description="24h | 7d | 30d"),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Cross-provider LLM spend rollup.

    Reads `llm_api_usage` (post-Phase-17 rename) and groups by provider.
    Used by the Mission Control "LLM cost today" tile.
    """
    if range not in _LLM_USAGE_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"range must be one of {sorted(_LLM_USAGE_RANGES)}",
        )

    from app.services.llm.pricing import cost_for_usage_usd
    days = _LLM_USAGE_RANGES[range]
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)

    # Group by provider — defensive fallback for legacy rows missing the column
    try:
        rows = (
            db.query(
                ClaudeApiUsage.provider,
                ClaudeApiUsage.model,
                ClaudeApiUsage.tokens_input,
                ClaudeApiUsage.tokens_output,
                ClaudeApiUsage.tokens_cache_creation,
                ClaudeApiUsage.tokens_cache_read,
                ClaudeApiUsage.created_at,
            )
            .filter(ClaudeApiUsage.created_at >= cutoff)
            .filter(ClaudeApiUsage.user_id == current_user.id)
            .all()
        )
    except Exception as e:
        log.warning("[llm/usage] DB query failed: %s", e)
        rows = []

    per_provider: dict[str, dict] = {}
    for r in rows:
        prov = r.provider or "anthropic"
        ent = per_provider.setdefault(prov, {
            "provider": prov,
            "calls": 0,
            "tokens_input": 0,
            "tokens_output": 0,
            "cost_usd": 0.0,
        })
        ent["calls"] += 1
        ent["tokens_input"] += int(r.tokens_input or 0)
        ent["tokens_output"] += int(r.tokens_output or 0)
        ent["cost_usd"] += cost_for_usage_usd(
            model=r.model or "claude-sonnet-4-5-20250929",
            tokens_input=int(r.tokens_input or 0),
            tokens_output=int(r.tokens_output or 0),
            tokens_cache_creation=int(r.tokens_cache_creation or 0),
            tokens_cache_read=int(r.tokens_cache_read or 0),
        )

    for ent in per_provider.values():
        ent["cost_usd"] = round(ent["cost_usd"], 4)

    total_cost = round(sum(p["cost_usd"] for p in per_provider.values()), 4)
    return {
        "range": range,
        "since": cutoff.isoformat(),
        "as_of": datetime.datetime.utcnow().isoformat(),
        "by_provider": list(per_provider.values()),
        "total_cost_usd": total_cost,
        "user_id": current_user.id,
    }


# ─────────────────────────────────────────────────────────────
# Recent GeminiRun feed (for the WhatsApp Hub + Receipt detail view)
# ─────────────────────────────────────────────────────────────

@router.get("/gemini/runs")
def list_gemini_runs(
    purpose: Optional[str] = Query(None, max_length=50),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    q = db.query(GeminiRun)
    if purpose:
        q = q.filter(GeminiRun.purpose == purpose)
    total = q.count()
    rows = (
        q.order_by(GeminiRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "runs": [
            {
                "id": r.id,
                "purpose": r.purpose,
                "model": r.model,
                "related_entity_type": r.related_entity_type,
                "related_entity_id": r.related_entity_id,
                "tokens_input": r.tokens_input,
                "tokens_output": r.tokens_output,
                "cost_usd": r.cost_usd,
                "status": r.status,
                "duration_ms": r.duration_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


# ═════════════════════════════════════════════════════════════════════
# Sprint 5 / Appendix M — Growth & Milestone Activation Engine
# ═════════════════════════════════════════════════════════════════════
#
# CEO-facing endpoints that surface autonomous feature unlock progress.
# Three endpoints:
#
#   GET  /api/v1/admin/exec/growth/summary
#       Live system-scale metrics that gate each feature.
#       Cheap computed view; called on every Mission Control load.
#
#   GET  /api/v1/admin/exec/growth/milestones
#       Joined view: each milestone config + current value + unlock state.
#       Drives the GrowthMilestonesCard UI.
#
#   POST /api/v1/admin/exec/growth/activate/{feature_name}
#       Formal CEO activation. Requires:
#         (a) Milestone threshold met (current ≥ target)
#         (b) WebAuthn step-up via require_step_up("copilot_provision")
#       Flips growth_milestones.is_unlocked=true + stamps unlocked_at.
#       Idempotent: re-activating an already-unlocked feature is a no-op.
#
# All three endpoints use require_admin (and the activate endpoint adds
# the step-up dep when AURORA_EXEC_REQUIRE_STEP_UP=1).

from app.config.feature_flags import (
    ALL_FEATURES,
    AutonomousFeature,
    MILESTONE_THRESHOLDS,
    feature_display_meta,
    get_threshold,
)
from aurora_shared.services.webauthn_service import require_step_up


def _compute_growth_summary(db: Session) -> dict:
    """Compute live system-scale metrics.

    Used by both /growth/summary and /growth/milestones (the latter
    refreshes GrowthMilestone.current_value from this dict).

    DEFENSIVE: any individual count failure → 0 for that metric.
    """
    from sqlalchemy import func as _f
    from aurora_shared.database.models import WhatsAppSession, Invoice

    def _safe_count(q) -> int:
        try:
            return int(q.scalar() or 0)
        except Exception:
            return 0

    active_orgs = _safe_count(
        db.query(_f.count(Organization.id)).filter(
            Organization.status == "active"
        )
    )
    total_orgs = _safe_count(db.query(_f.count(Organization.id)))
    total_invoices = _safe_count(db.query(_f.count(Invoice.id)))

    classified_receipts = _safe_count(
        db.query(_f.count(Receipt.id)).filter(
            Receipt.gemini_classified_at.isnot(None)
        )
    )

    try:
        cutoff_30d = datetime.datetime.utcnow() - datetime.timedelta(days=30)
        active_sessions_30d = _safe_count(
            db.query(_f.count(WhatsAppSession.id))
            .filter(WhatsAppSession.last_client_message_at.isnot(None))
            .filter(WhatsAppSession.last_client_message_at >= cutoff_30d)
        )
    except Exception:
        active_sessions_30d = 0

    copilot_runs = _safe_count(
        db.query(_f.count(CopilotProvisioningRun.id))
        .filter(CopilotProvisioningRun.status == "success")
    )

    return {
        "active_orgs": active_orgs,
        "total_orgs": total_orgs,
        "total_invoices": total_invoices,
        "classified_receipts": classified_receipts,
        "active_sessions_30d": active_sessions_30d,
        "copilot_runs": copilot_runs,
        "as_of": datetime.datetime.utcnow().isoformat(),
    }


@router.get("/growth/summary")
def growth_summary_endpoint(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Live system-scale metrics powering the Growth Engine."""
    return _compute_growth_summary(db)


@router.get("/growth/milestones")
def growth_milestones_endpoint(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Joined milestone state for the GrowthMilestonesCard UI.

    Behavior:
      1. Compute live metrics via _compute_growth_summary().
      2. Refresh each GrowthMilestone.current_value from those metrics.
      3. Auto-seed any missing rows (defensive — migrate_phase18 already
         seeds, but this handles fresh-from-blank-DB cases).
      4. Return list with: feature_name, display_label, description,
         threshold, current_value, percent_complete, is_unlocked,
         unlocked_at, is_threshold_met (true iff current ≥ threshold).
    """
    metrics = _compute_growth_summary(db)

    # Build existing rows lookup
    existing_rows = {
        r.feature_name: r
        for r in db.query(GrowthMilestone).all()
    }

    out: list[dict] = []
    for feature in ALL_FEATURES:
        meta = feature_display_meta(feature)
        current_value = int(metrics.get(meta["metric"], 0) or 0)
        threshold = int(meta["threshold"])

        row = existing_rows.get(feature.value)
        if row is None:
            # Defensive seed (Phase 18 should have done this; idempotent here)
            row = GrowthMilestone(
                feature_name=feature.value,
                threshold_metric=meta["metric"],
                threshold_value=threshold,
                current_value=current_value,
                is_unlocked=False,
            )
            db.add(row)
            try:
                db.commit()
                db.refresh(row)
            except Exception as e:
                db.rollback()
                log.warning("[growth/milestones] seed failed: %s", e)
                continue
        else:
            # Refresh current_value + threshold (in case env was bumped)
            if (
                row.current_value != current_value
                or row.threshold_value != threshold
            ):
                row.current_value = current_value
                row.threshold_value = threshold
                row.last_updated_at = datetime.datetime.utcnow()
                try:
                    db.commit()
                except Exception:
                    db.rollback()

        percent = (
            min(100.0, round(100.0 * current_value / threshold, 1))
            if threshold > 0
            else 0.0
        )

        out.append({
            "feature_name": feature.value,
            "display_label": meta["display_label"],
            "display_description": meta["display_description"],
            "metric": meta["metric"],
            "display_unit": meta["display_unit"],
            "threshold_value": threshold,
            "current_value": current_value,
            "percent_complete": percent,
            "is_threshold_met": current_value >= threshold,
            "is_unlocked": bool(row.is_unlocked),
            "unlocked_at": row.unlocked_at.isoformat() if row.unlocked_at else None,
            "unlocked_by_user_id": row.unlocked_by_user_id,
            "last_updated_at": (
                row.last_updated_at.isoformat() if row.last_updated_at else None
            ),
        })

    return {
        "as_of": metrics["as_of"],
        "metrics_snapshot": metrics,
        "milestones": out,
    }


@router.post("/growth/activate/{feature_name}")
def growth_activate_endpoint(
    feature_name: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    step_up_credential_id: int = Depends(require_step_up("copilot_provision")),
) -> dict:
    """Formally activate an autonomous feature.

    Two gates enforced HERE (not in the service):
      (a) Threshold met — current_value ≥ threshold_value
      (b) WebAuthn step-up — via the require_step_up dependency
          (action='copilot_provision' since this is a provisioning-class
          action; reused to avoid proliferating step-up actions for now)

    Idempotent: calling activate on an already-unlocked feature returns
    the existing record without churn.

    The `is_unlocked=true` flip is what makes `is_feature_active()` start
    returning True system-wide. After activation, the autonomous service
    is "live" — but the SKELETON implementations still return placeholder
    payloads until the underlying ML pipeline ships.
    """
    # Validate feature
    try:
        feature = AutonomousFeature(feature_name)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown feature: {feature_name!r}. "
                f"Known: {[f.value for f in AutonomousFeature]}"
            ),
        )

    # Look up the milestone row (auto-seed if missing — defensive)
    row = (
        db.query(GrowthMilestone)
        .filter(GrowthMilestone.feature_name == feature.value)
        .first()
    )
    if row is None:
        meta = feature_display_meta(feature)
        row = GrowthMilestone(
            feature_name=feature.value,
            threshold_metric=meta["metric"],
            threshold_value=int(meta["threshold"]),
            current_value=0,
            is_unlocked=False,
        )
        db.add(row)
        db.commit()
        db.refresh(row)

    # Idempotent: already unlocked → return current state
    if row.is_unlocked:
        return {
            "ok": True,
            "feature": feature.value,
            "already_unlocked": True,
            "unlocked_at": row.unlocked_at.isoformat() if row.unlocked_at else None,
            "unlocked_by_user_id": row.unlocked_by_user_id,
        }

    # Threshold check — refresh current_value first
    metrics = _compute_growth_summary(db)
    current_value = int(metrics.get(row.threshold_metric, 0) or 0)
    row.current_value = current_value
    if current_value < row.threshold_value:
        try:
            db.commit()  # persist the refreshed current_value anyway
        except Exception:
            db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "error": "threshold_not_met",
                "feature": feature.value,
                "metric": row.threshold_metric,
                "current_value": current_value,
                "threshold_value": row.threshold_value,
                "shortfall": row.threshold_value - current_value,
            },
        )

    # Flip the unlock
    now = datetime.datetime.utcnow()
    row.is_unlocked = True
    row.unlocked_at = now
    row.unlocked_by_user_id = current_user.id
    row.last_updated_at = now

    try:
        db.commit()
        db.refresh(row)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"activate commit failed: {type(e).__name__}: {str(e)[:160]}",
        )

    # Audit event (CRITICAL — every activation is a permanent posture change)
    try:
        publish_exec_event(
            db,
            kind="autonomous_feature_activated",
            severity="warning",
            title=f"Autonomous feature activated: {feature.value}",
            detail=(
                f"by user_id={current_user.id} "
                f"step_up_credential_id={step_up_credential_id} "
                f"metric={row.threshold_metric} "
                f"current_value={current_value} threshold={row.threshold_value}"
            ),
            related_entity_type="growth_milestone",
            related_entity_id=row.id,
        )
    except Exception:
        pass

    return {
        "ok": True,
        "feature": feature.value,
        "already_unlocked": False,
        "unlocked_at": row.unlocked_at.isoformat(),
        "unlocked_by_user_id": current_user.id,
        "step_up_credential_id": step_up_credential_id,
        "metric": row.threshold_metric,
        "current_value": current_value,
        "threshold_value": row.threshold_value,
    }


# ═════════════════════════════════════════════════════════════════════
# Sprint 8.2.5 — Accountant Seed Endpoint
# ═════════════════════════════════════════════════════════════════════
#
# POST /api/v1/admin/exec/seed/accountant
#   Provisions a brand-new external accountant by creating:
#     1. User row     (role="accountant", is_active=True)
#     2. AccountantEngagement row mapping the new user to an
#        organization with status="active"
#     3. ActionLog audit entry with full actor + target detail
#
# SECURITY POSTURE:
#   • require_admin                — IAP-gated CEO/admin only
#   • require_step_up("seed_accountant") — WebAuthn (Touch ID / passkey)
#     just-in-time approval per call, since this provisions a fresh
#     user with system-level access to a tenant's books.
#
# PASSWORD HANDLING:
#   The accountant logs in via OTP-only (see accountant_auth.py).
#   We MUST still populate `password_hash` (NOT NULL on `users`), so
#   we bcrypt-hash a thrown-away cryptographic token. Result: no
#   plaintext can ever match this hash. The accountant gets in via
#   email OTP exclusively.
# ═════════════════════════════════════════════════════════════════════

import secrets as _secrets_acct
from app.services.auth_service import hash_password as _hash_password_acct


class AccountantSeedRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254,
                       description="Accountant's professional email — used as the OTP recipient.")
    name: str = Field(..., min_length=1, max_length=120,
                      description="Display name (firma legal name or CPA full name).")
    organization_id: int = Field(..., gt=0,
                                 description="Organization the accountant is engaged to advise.")


class AccountantSeedResponse(BaseModel):
    ok: bool
    user_id: int
    engagement_id: int
    email: str
    organization_id: int
    organization_display_name: str
    seeded_by_user_id: int
    step_up_credential_id: int
    seeded_at: str


@router.post(
    "/seed/accountant",
    response_model=AccountantSeedResponse,
    summary="Seed a new external accountant + engagement (CEO step-up gated)",
)
def seed_accountant_endpoint(
    payload: AccountantSeedRequest,
    request: Request,
    current_user: User = Depends(require_admin),
    step_up_credential_id: int = Depends(require_step_up("seed_accountant")),
    db: Session = Depends(get_db),
) -> AccountantSeedResponse:
    """
    Provision a new accountant user + engagement in a single atomic
    transaction. Idempotent only against unique-email duplication —
    raises HTTP 400 `user_exists` if the email is already in use.

    All three writes (User, AccountantEngagement, ActionLog) commit
    together so we never end up with a half-seeded accountant.
    """
    email_norm = payload.email.strip().lower()
    name_norm = payload.name.strip()

    # 1. Pre-check: email must not already exist (anti-clobber).
    existing = db.query(User).filter(User.email == email_norm).first()
    if existing is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "user_exists",
                "message": f"A user with email {email_norm} already exists "
                           f"(id={existing.id}, role={existing.role}).",
            },
        )

    # 2. Verify the target organization exists.
    org = (
        db.query(Organization)
        .filter(Organization.id == payload.organization_id)
        .first()
    )
    if org is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "organization_not_found",
                "message": f"Organization {payload.organization_id} does not exist.",
            },
        )

    # 3. Create the accountant User. Password is a thrown-away bcrypt
    #    hash — accountants authenticate via email OTP only.
    placeholder_secret = _secrets_acct.token_urlsafe(48)
    unguessable_hash = _hash_password_acct(placeholder_secret)
    del placeholder_secret  # explicit drop; nothing logs it

    new_user = User(
        email=email_norm,
        password_hash=unguessable_hash,
        full_name=name_norm,
        role="accountant",
        is_active=True,
        language_pref="he",   # default Hebrew for IL CPAs; user can change later
        onboarding_status="active",  # admin-seeded users skip onboarding
    )
    db.add(new_user)
    db.flush()  # populate new_user.id without committing

    # 4. Create the active AccountantEngagement.
    now_utc = datetime.datetime.utcnow()
    engagement = AccountantEngagement(
        accountant_user_id=new_user.id,
        organization_id=payload.organization_id,
        status="active",
        invited_at=now_utc,
        activated_at=now_utc,
        revenue_share_pct=20.0,  # platform default; overridable later
    )
    db.add(engagement)
    db.flush()

    # 5. Audit trail — capture actor, action, target metadata.
    audit_payload = {
        "action": "seed_accountant",
        "actor_user_id": current_user.id,
        "actor_email": current_user.email,
        "step_up_credential_id": step_up_credential_id,
        "target_user_id": new_user.id,
        "target_email": email_norm,
        "target_display_name": name_norm,
        "target_organization_id": payload.organization_id,
        "target_organization_display_name": org.display_name,
        "engagement_id": engagement.id,
        "client_ip_hint": (request.client.host if request.client else None),
        "timestamp_utc": now_utc.isoformat(),
    }
    action_log = ActionLog(
        business_id=None,  # operates at organization-level, not legacy business
        status="seed_accountant",
        detail=_json.dumps(audit_payload, ensure_ascii=False, separators=(",", ":")),
        triggered_at=now_utc,
    )
    db.add(action_log)

    # 6. Atomic commit. If anything fails, rollback everything — we
    #    never want an orphan User without its engagement.
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        log.exception("[SEED_ACCOUNTANT] commit failed for email=%s", email_norm)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "seed_commit_failed",
                "message": f"{type(e).__name__}: {str(e)[:160]}",
            },
        ) from e

    db.refresh(new_user)
    db.refresh(engagement)

    # 7. Operational visibility — surface in ExecEvent feed (best-effort).
    try:
        publish_exec_event(
            db,
            kind="accountant_seeded",
            severity="info",
            title=f"Accountant provisioned: {name_norm} ({email_norm})",
            detail=(
                f"engagement_id={engagement.id} "
                f"organization_id={payload.organization_id} "
                f"actor_user_id={current_user.id} "
                f"step_up_credential_id={step_up_credential_id}"
            ),
            related_entity_type="accountant_engagement",
            related_entity_id=engagement.id,
        )
    except Exception:
        # Audit log already persisted — ExecEvent is best-effort observability.
        log.warning(
            "[SEED_ACCOUNTANT] ExecEvent publish failed (non-fatal) for user_id=%d",
            new_user.id,
        )

    return AccountantSeedResponse(
        ok=True,
        user_id=new_user.id,
        engagement_id=engagement.id,
        email=email_norm,
        organization_id=payload.organization_id,
        organization_display_name=org.display_name,
        seeded_by_user_id=current_user.id,
        step_up_credential_id=step_up_credential_id,
        seeded_at=now_utc.isoformat(),
    )
