"""
Aurora Copilot — tool executor (Sprint 3).

Two execution paths:
  • READ tools (search_existing_categories): run INLINE during chat
    streaming. No approval needed. Result becomes a `tool_result` block
    in the next Claude turn, so the model can continue reasoning.
  • WRITE tools (propose_provisioning_blueprint, update_category,
    delete_category, assign_org_to_category): NOT executed during chat.
    Instead, surfaced in the UI as a "Pending Approval" card. The CEO
    clicks Approve & Build → step-up → /copilot/approve calls
    `execute_write_tool()` here.

Every WRITE-tool execution records a CopilotProvisioningRun row with
outcome_json so partial-success cases are forensically reconstructable.

The executor talks DIRECTLY to the existing categories CRUD via
SQLAlchemy (it does NOT make HTTP loopback calls). This keeps the
transaction-per-execution guarantee — one tool_use commits or rolls
back atomically.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from aurora_shared.database.models import (
    BusinessCategory,
    Organization,
    User,
    CopilotProvisioningRun,
)
from app.services.copilot.tools import (
    AssignOrgInput,
    DeleteCategoryInput,
    NewProfessionInput,
    NewSectorInput,
    ProvisioningBlueprintInput,
    SearchCategoriesInput,
    UpdateCategoryInput,
    WRITE_TOOLS,
)
from aurora_shared.services.exec_events import publish_exec_event

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# READ tool — runs inline during chat (no approval)
# ─────────────────────────────────────────────────────────────

def execute_search_categories(args: Dict[str, Any], db: Session) -> Dict[str, Any]:
    """Inline executor for `search_existing_categories`.

    Returns a `tool_result`-shaped dict the router can drop straight into
    the next Anthropic messages payload.
    """
    try:
        parsed = SearchCategoriesInput(**args)
    except Exception as e:
        return {"error": f"args validation failed: {str(e)[:200]}"}

    q = db.query(
        BusinessCategory.id,
        BusinessCategory.name,
        BusinessCategory.name_he,
        BusinessCategory.name_ar,
        BusinessCategory.slug,
        BusinessCategory.level,
        BusinessCategory.parent_id,
        BusinessCategory.icon_emoji,
        BusinessCategory.is_active,
    )
    if parsed.level is not None:
        q = q.filter(BusinessCategory.level == parsed.level)

    needle = f"%{parsed.query}%"
    # ILIKE on name + name_he + name_ar + slug; the OR set covers most
    # founder vocabulary (he/ar/en).
    q = q.filter(
        (BusinessCategory.name.ilike(needle))
        | (BusinessCategory.name_he.ilike(needle))
        | (BusinessCategory.name_ar.ilike(needle))
        | (BusinessCategory.slug.ilike(needle))
    )

    rows = q.order_by(BusinessCategory.level, BusinessCategory.name).limit(30).all()
    return {
        "matched_count": len(rows),
        "categories": [
            {
                "id": r.id,
                "name": r.name,
                "name_he": r.name_he,
                "name_ar": r.name_ar,
                "slug": r.slug,
                "level": r.level,
                "parent_id": r.parent_id,
                "icon_emoji": r.icon_emoji,
                "is_active": bool(r.is_active),
            }
            for r in rows
        ],
    }


# ─────────────────────────────────────────────────────────────
# WRITE tools — run via /copilot/approve (post step-up)
# ─────────────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"[^\w\s-]", re.UNICODE)


def _slugify(s: str) -> str:
    s = s.strip().lower()
    s = _SLUG_RE.sub("", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-") or "cat"


def _ensure_unique_slug(db: Session, candidate: str) -> str:
    """Return `candidate` (or candidate-2, candidate-3, ...) if it's already taken."""
    base = candidate
    suffix = 1
    while db.query(BusinessCategory).filter(BusinessCategory.slug == candidate).first():
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def execute_propose_blueprint(
    args: Dict[str, Any],
    db: Session,
    actor_user_id: int,
) -> Dict[str, Any]:
    """Execute an approved provisioning blueprint atomically.

    Strategy:
      1. Validate args via Pydantic.
      2. Create all new L1 sectors first (so professions can reference
         them by name).
      3. Build a name → id map for L1 (existing + freshly-created).
      4. Create L2 professions, resolving `parent_sector_name` to id.
      5. Return per-item outcome.

    All operations are within a single SQLAlchemy transaction. Caller's
    db.commit() seals it; any exception triggers a rollback at the
    router level.
    """
    try:
        parsed = ProvisioningBlueprintInput(**args)
    except Exception as e:
        return {
            "status": "failed",
            "error": f"args validation failed: {str(e)[:240]}",
        }

    outcome: Dict[str, Any] = {
        "summary": parsed.summary,
        "sectors_created": [],
        "professions_created": [],
        "errors": [],
    }

    # Existing L1 lookup table — both name AND name_he (founder may
    # reference a sector by either).
    existing_l1 = {
        r.name.lower(): r
        for r in db.query(BusinessCategory).filter(BusinessCategory.level == 1).all()
    }
    for r in db.query(BusinessCategory).filter(
        BusinessCategory.level == 1, BusinessCategory.name_he.isnot(None)
    ).all():
        if r.name_he:
            existing_l1[r.name_he.lower()] = r

    # 1. Create L1 sectors
    created_l1_by_name: Dict[str, BusinessCategory] = {}
    for new in parsed.new_sectors:
        # Skip if an L1 with this name already exists
        key = new.name.lower()
        if key in existing_l1:
            outcome["errors"].append({
                "kind": "sector_exists",
                "name": new.name,
                "existing_id": existing_l1[key].id,
            })
            continue

        slug = _ensure_unique_slug(db, _slugify(new.name))
        row = BusinessCategory(
            parent_id=None,
            name=new.name,
            name_he=new.name_he,
            name_ar=new.name_ar,
            slug=slug,
            level=1,
            description=new.description,
            icon_emoji=new.icon_emoji,
            sort_order=0,
            is_active=True,
        )
        db.add(row)
        db.flush()  # populate row.id without commit
        created_l1_by_name[new.name.lower()] = row
        outcome["sectors_created"].append({
            "id": row.id,
            "name": row.name,
            "slug": row.slug,
        })

    # 2. Build name → L1 row resolver (existing + newly created)
    def _resolve_l1(parent_name: str) -> Optional[BusinessCategory]:
        if not parent_name:
            return None
        key = parent_name.lower()
        if key in created_l1_by_name:
            return created_l1_by_name[key]
        if key in existing_l1:
            return existing_l1[key]
        return None

    # 3. Create L2 professions
    for prof in parsed.new_professions:
        parent = _resolve_l1(prof.parent_sector_name)
        if not parent:
            outcome["errors"].append({
                "kind": "parent_sector_not_found",
                "name": prof.name,
                "parent_sector_name": prof.parent_sector_name,
            })
            continue

        slug = _ensure_unique_slug(
            db, f"{parent.slug}/{_slugify(prof.name)}"
        )
        row = BusinessCategory(
            parent_id=parent.id,
            name=prof.name,
            name_he=prof.name_he,
            name_ar=prof.name_ar,
            slug=slug,
            level=2,
            description=prof.description,
            icon_emoji=prof.icon_emoji,
            sort_order=0,
            is_active=True,
        )
        db.add(row)
        db.flush()
        outcome["professions_created"].append({
            "id": row.id,
            "name": row.name,
            "slug": row.slug,
            "parent_id": parent.id,
            "parent_name": parent.name,
        })

    # Caller commits.
    sector_count = len(outcome["sectors_created"])
    prof_count = len(outcome["professions_created"])
    err_count = len(outcome["errors"])

    # Decide status
    if err_count == 0:
        outcome["status"] = "success"
    elif sector_count + prof_count > 0:
        outcome["status"] = "partial"
    else:
        outcome["status"] = "failed"

    return outcome


def execute_update_category(
    args: Dict[str, Any],
    db: Session,
    actor_user_id: int,
) -> Dict[str, Any]:
    try:
        parsed = UpdateCategoryInput(**args)
    except Exception as e:
        return {"status": "failed", "error": f"args validation failed: {str(e)[:200]}"}

    cat = db.query(BusinessCategory).filter(BusinessCategory.id == parsed.category_id).first()
    if not cat:
        return {"status": "failed", "error": "category_not_found"}

    patch = parsed.model_dump(exclude_unset=True, exclude={"category_id"})
    for k, v in patch.items():
        setattr(cat, k, v)
    cat.updated_at = datetime.datetime.utcnow()
    return {
        "status": "success",
        "category_id": cat.id,
        "updated_fields": list(patch.keys()),
        "category": {
            "id": cat.id,
            "name": cat.name,
            "name_he": cat.name_he,
            "slug": cat.slug,
            "level": cat.level,
            "icon_emoji": cat.icon_emoji,
            "is_active": bool(cat.is_active),
        },
    }


def execute_delete_category(
    args: Dict[str, Any],
    db: Session,
    actor_user_id: int,
) -> Dict[str, Any]:
    try:
        parsed = DeleteCategoryInput(**args)
    except Exception as e:
        return {"status": "failed", "error": f"args validation failed: {str(e)[:200]}"}

    cat = db.query(BusinessCategory).filter(BusinessCategory.id == parsed.category_id).first()
    if not cat:
        return {"status": "failed", "error": "category_not_found"}

    # Re-verify the founder echoed the exact name (anti fat-finger).
    expected_names = {cat.name.lower()}
    if cat.name_he:
        expected_names.add(cat.name_he.lower())
    if cat.name_ar:
        expected_names.add(cat.name_ar.lower())
    if parsed.confirm_understanding.strip().lower() not in expected_names:
        return {
            "status": "failed",
            "error": "confirmation_mismatch",
            "hint": f"Echo one of: {sorted(expected_names)}",
        }

    # Block delete if children or orgs exist (same gates as the
    # categories endpoint — keep behavior identical).
    child_count = (
        db.query(func.count(BusinessCategory.id))
        .filter(BusinessCategory.parent_id == cat.id)
        .scalar()
        or 0
    )
    if child_count > 0:
        return {
            "status": "failed",
            "error": "has_children",
            "child_count": int(child_count),
        }

    org_count = (
        db.query(func.count(Organization.id))
        .filter(Organization.category_id == cat.id)
        .scalar()
        or 0
    )
    if org_count > 0:
        return {
            "status": "failed",
            "error": "orgs_mapped",
            "org_count": int(org_count),
        }

    cat_summary = {
        "id": cat.id,
        "name": cat.name,
        "slug": cat.slug,
        "level": cat.level,
    }
    db.delete(cat)
    return {"status": "success", "deleted": cat_summary}


def execute_assign_org(
    args: Dict[str, Any],
    db: Session,
    actor_user_id: int,
) -> Dict[str, Any]:
    try:
        parsed = AssignOrgInput(**args)
    except Exception as e:
        return {"status": "failed", "error": f"args validation failed: {str(e)[:200]}"}

    cat = db.query(BusinessCategory).filter(BusinessCategory.id == parsed.category_id).first()
    if not cat:
        return {"status": "failed", "error": "category_not_found"}
    if cat.level != 2:
        return {"status": "failed", "error": "category_not_level_2"}

    org = db.query(Organization).filter(Organization.id == parsed.organization_id).first()
    if not org:
        return {"status": "failed", "error": "organization_not_found"}

    prev = org.category_id
    org.category_id = cat.id
    return {
        "status": "success",
        "organization_id": org.id,
        "category_id": cat.id,
        "category_slug": cat.slug,
        "previous_category_id": prev,
    }


# ─────────────────────────────────────────────────────────────
# Dispatcher — called from /copilot/approve
# ─────────────────────────────────────────────────────────────

_WRITE_DISPATCH = {
    "propose_provisioning_blueprint": execute_propose_blueprint,
    "update_category": execute_update_category,
    "delete_category": execute_delete_category,
    "assign_org_to_category": execute_assign_org,
}


def execute_approved_tool(
    *,
    db: Session,
    conversation_id: int,
    tool_use_id: str,
    tool_name: str,
    tool_input: Dict[str, Any],
    actor_user_id: int,
    step_up_credential_id: Optional[int],
) -> Dict[str, Any]:
    """
    Run an approved WRITE tool inside a SQLAlchemy transaction.
    Records a CopilotProvisioningRun row in BOTH success and failure
    paths. Caller commits; on uncaught exception, caller rolls back.
    """
    if tool_name not in WRITE_TOOLS:
        return {"status": "failed", "error": f"tool_not_executable: {tool_name}"}

    handler = _WRITE_DISPATCH[tool_name]
    outcome = handler(tool_input, db, actor_user_id)

    # Record the run regardless of outcome
    run = CopilotProvisioningRun(
        conversation_id=conversation_id,
        tool_use_id=tool_use_id,
        tool_name=tool_name,
        input_json=json.dumps(tool_input, default=str, ensure_ascii=False),
        outcome_json=json.dumps(outcome, default=str, ensure_ascii=False),
        step_up_credential_id=step_up_credential_id,
        executed_by_user_id=actor_user_id,
        executed_at=datetime.datetime.utcnow(),
        status=outcome.get("status", "failed"),
    )
    db.add(run)

    # Publish ExecEvent for the Alert Stream
    try:
        severity = "info"
        if outcome.get("status") == "failed":
            severity = "warning"
        elif outcome.get("status") == "partial":
            severity = "warning"
        publish_exec_event(
            db,
            kind="copilot_provisioning_executed",
            severity=severity,
            title=f"Copilot: {tool_name} ({outcome.get('status', 'unknown')})",
            detail=(outcome.get("summary") or outcome.get("error") or "")[:240],
            related_entity_type="copilot_provisioning_run",
            related_entity_id=run.id if run.id else None,
        )
    except Exception:
        # ExecEvent failure must never break the provisioning path.
        pass

    return outcome
