"""
Aurora LTS — Executive Dashboard Aggregator (Appendix H Tier 1)
================================================================

Composes the single-screen "Mission Control" payload from many
independent DB queries. The CEO dashboard hits one endpoint
(GET /api/v1/admin/exec/dashboard-summary) and gets:

  • Today / MTD revenue (sum of finalized invoice amount_net)
  • Active org count
  • In-flight invoice counts by status (draft / finalized / sent)
  • Receipt review queue depth
  • Payouts pending approval
  • System health snapshot (delegates to compliance/health)
  • Last N exec events for the right-rail Alert Stream warm-start

Performance:
  • Each query is small (count + sum). Total wall-clock budget < 200ms.
  • No N+1 joins.
  • No caching at this layer — the SSR shell already throttles to once
    per page render, and clients are bounded to one CEO.

Returns a stable JSON shape so the frontend can hard-bind without
defensive `?.` everywhere.
"""

from __future__ import annotations

import datetime
import logging
from typing import Dict, Any, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from aurora_shared.database.models import (
    Invoice,
    Organization,
    Receipt,
    AccountantPayout,
    ExecEvent,
    User,
    CeoSessionSnapshot,
)

log = logging.getLogger(__name__)


def _today_start_utc() -> datetime.datetime:
    now = datetime.datetime.utcnow()
    return datetime.datetime(now.year, now.month, now.day)


def _month_start_utc() -> datetime.datetime:
    now = datetime.datetime.utcnow()
    return datetime.datetime(now.year, now.month, 1)


def _safe_count(db: Session, query) -> int:
    try:
        return int(query.scalar() or 0)
    except Exception as e:
        log.warning("[exec_aggregator] count query failed: %s", e)
        return 0


def _safe_sum(db: Session, query) -> float:
    try:
        return float(query.scalar() or 0.0)
    except Exception as e:
        log.warning("[exec_aggregator] sum query failed: %s", e)
        return 0.0


def build_dashboard_summary(db: Session) -> Dict[str, Any]:
    today = _today_start_utc()
    month = _month_start_utc()

    # ── Revenue ──
    revenue_today = _safe_sum(
        db,
        db.query(func.coalesce(func.sum(Invoice.amount_net), 0))
        .filter(Invoice.finalized_at.isnot(None))
        .filter(Invoice.finalized_at >= today),
    )
    revenue_mtd = _safe_sum(
        db,
        db.query(func.coalesce(func.sum(Invoice.amount_net), 0))
        .filter(Invoice.finalized_at.isnot(None))
        .filter(Invoice.finalized_at >= month),
    )

    # ── Org counts ──
    total_orgs = _safe_count(db, db.query(func.count(Organization.id)))
    active_orgs = _safe_count(
        db,
        db.query(func.count(Organization.id)).filter(Organization.status == "active"),
    )
    suspended_orgs = _safe_count(
        db,
        db.query(func.count(Organization.id)).filter(Organization.status == "suspended"),
    )

    # ── Invoice pipeline ──
    invoices_by_status: Dict[str, int] = {}
    try:
        rows = (
            db.query(Invoice.status, func.count(Invoice.id))
            .group_by(Invoice.status)
            .all()
        )
        for s, c in rows:
            invoices_by_status[s or "unknown"] = int(c or 0)
    except Exception as e:
        log.warning("[exec_aggregator] invoice status query failed: %s", e)

    # ── Receipts ──
    receipts_total = _safe_count(db, db.query(func.count(Receipt.id)))
    # "review queue depth" — heavy-review-routed receipts
    # We don't have a dedicated route column, so approximate via OCR confidence threshold.
    # The receipt service uses `confidence.py` for actual routing; we mirror its threshold.
    receipts_review_queue = 0
    try:
        # Best-effort: count receipts with low confidence + unconfirmed expense.
        receipts_review_queue = _safe_count(
            db,
            db.query(func.count(Receipt.id))
            .filter(Receipt.ocr_confidence.isnot(None))
            .filter(Receipt.ocr_confidence < 0.7),
        )
    except Exception:
        # Schema may differ — silently fall back.
        pass

    # ── Payouts pending approval ──
    payouts_pending = _safe_count(
        db,
        db.query(func.count(AccountantPayout.id)).filter(
            AccountantPayout.status == "pending"
        ),
    )

    # ── Users ──
    total_users = _safe_count(db, db.query(func.count(User.id)))

    # ── Recent exec events (warm-start the Alert Stream) ──
    recent_events: List[Dict[str, Any]] = []
    try:
        ev_rows = (
            db.query(ExecEvent)
            .order_by(ExecEvent.id.desc())
            .limit(10)
            .all()
        )
        for ev in ev_rows:
            recent_events.append({
                "id": ev.id,
                "kind": ev.kind,
                "severity": ev.severity,
                "title": ev.title,
                "detail": ev.detail,
                "related_entity_type": ev.related_entity_type,
                "related_entity_id": ev.related_entity_id,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
            })
    except Exception as e:
        log.warning("[exec_aggregator] exec_events query failed: %s", e)

    return {
        "as_of": datetime.datetime.utcnow().isoformat(),
        "revenue": {
            "today_net_nis": round(revenue_today, 2),
            "mtd_net_nis": round(revenue_mtd, 2),
        },
        "orgs": {
            "total": total_orgs,
            "active": active_orgs,
            "suspended": suspended_orgs,
        },
        "invoices": {
            "by_status": invoices_by_status,
            "in_flight": (
                invoices_by_status.get("draft", 0)
                + invoices_by_status.get("finalized", 0)
                + invoices_by_status.get("sent", 0)
            ),
        },
        "receipts": {
            "total": receipts_total,
            "review_queue_depth": receipts_review_queue,
        },
        "payouts": {
            "pending_approval": payouts_pending,
        },
        "users": {
            "total": total_users,
        },
        "recent_events": recent_events,
    }


def build_finance_summary(db: Session) -> Dict[str, Any]:
    """
    Detailed financial breakdown for the Financial Command module.
    Cheap to compute — used at module entry.
    """
    today = _today_start_utc()
    month = _month_start_utc()

    invoices_by_status: Dict[str, int] = {}
    try:
        rows = (
            db.query(Invoice.status, func.count(Invoice.id))
            .group_by(Invoice.status)
            .all()
        )
        invoices_by_status = {s or "unknown": int(c or 0) for s, c in rows}
    except Exception as e:
        log.warning("[exec_aggregator.finance] invoice status query failed: %s", e)

    revenue_today = _safe_sum(
        db,
        db.query(func.coalesce(func.sum(Invoice.amount_net), 0))
        .filter(Invoice.finalized_at.isnot(None))
        .filter(Invoice.finalized_at >= today),
    )
    revenue_mtd = _safe_sum(
        db,
        db.query(func.coalesce(func.sum(Invoice.amount_net), 0))
        .filter(Invoice.finalized_at.isnot(None))
        .filter(Invoice.finalized_at >= month),
    )

    # Allocation queue depth (Sprint 5 / allocation_queue.py drives retries)
    allocation_pending = 0
    try:
        allocation_pending = _safe_count(
            db,
            db.query(func.count(Invoice.id)).filter(
                Invoice.allocation_status == "retry_pending"
            ),
        )
    except Exception:
        pass

    # Receipts breakdown
    receipts_total = _safe_count(db, db.query(func.count(Receipt.id)))
    receipts_dlp_flagged = 0
    try:
        receipts_dlp_flagged = _safe_count(
            db,
            db.query(func.count(Receipt.id)).filter(Receipt.dlp_flagged == True),  # noqa
        )
    except Exception:
        pass

    payouts_pending = _safe_count(
        db,
        db.query(func.count(AccountantPayout.id)).filter(
            AccountantPayout.status == "pending"
        ),
    )

    return {
        "as_of": datetime.datetime.utcnow().isoformat(),
        "revenue": {
            "today_net_nis": round(revenue_today, 2),
            "mtd_net_nis": round(revenue_mtd, 2),
        },
        "invoices": {
            "by_status": invoices_by_status,
            "allocation_queue_pending": allocation_pending,
        },
        "receipts": {
            "total": receipts_total,
            "dlp_flagged": receipts_dlp_flagged,
        },
        "payouts": {
            "pending_approval": payouts_pending,
        },
    }


# ─────────────────────────────────────────────────────────────
# Appendix I Sprint 2 — "What changed since you last looked"
# ─────────────────────────────────────────────────────────────

# Subset of dashboard-summary fields we diff (anything numeric)
_DIFFABLE_KPIS = [
    ("revenue.today_net_nis",          "Revenue today (₪)"),
    ("revenue.mtd_net_nis",            "Revenue MTD (₪)"),
    ("orgs.active",                    "Active orgs"),
    ("orgs.suspended",                 "Suspended orgs"),
    ("invoices.in_flight",             "Invoices in flight"),
    ("receipts.review_queue_depth",    "Receipts review queue"),
    ("payouts.pending_approval",       "Payouts pending"),
]


def _get_nested(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def compute_since_last_visit_diff(
    user_id: int,
    current_summary: Dict[str, Any],
    db: Session,
    persist: bool = True,
) -> Dict[str, Any]:
    """
    Compute the diff vs the most recent CeoSessionSnapshot for this user,
    then persist a fresh snapshot (if `persist=True`).

    Returns a dict shaped:
        {
            "has_previous_visit": bool,
            "last_visited_at":    "2026-05-19T18:00:00Z" | null,
            "deltas": [
                {"key": "revenue.today_net_nis", "label": "...",
                 "previous": 0, "current": 12345, "delta": 12345}
            ]
        }

    Always returns a non-None dict — never raises (defensive).
    """
    import json as _json

    out: Dict[str, Any] = {
        "has_previous_visit": False,
        "last_visited_at": None,
        "deltas": [],
    }

    # Look up the most recent snapshot for this user.
    try:
        prev = (
            db.query(CeoSessionSnapshot)
            .filter(CeoSessionSnapshot.user_id == user_id)
            .order_by(CeoSessionSnapshot.id.desc())
            .first()
        )
    except Exception as e:
        log.warning("[exec_aggregator.diff] snapshot lookup failed: %s", e)
        prev = None

    if prev is not None:
        try:
            prev_snapshot = _json.loads(prev.snapshot_json or "{}")
        except Exception:
            prev_snapshot = {}
        out["has_previous_visit"] = True
        out["last_visited_at"] = (
            prev.created_at.isoformat() if prev.created_at else None
        )

        for key, label in _DIFFABLE_KPIS:
            curr_val = _get_nested(current_summary, key)
            prev_val = _get_nested(prev_snapshot, key)
            if isinstance(curr_val, (int, float)) and isinstance(prev_val, (int, float)):
                delta = curr_val - prev_val
                if abs(delta) > 1e-9:
                    out["deltas"].append({
                        "key": key,
                        "label": label,
                        "previous": prev_val,
                        "current": curr_val,
                        "delta": delta,
                    })

    # Persist a fresh snapshot so the NEXT visit diffs against THIS visit.
    if persist:
        try:
            snap = CeoSessionSnapshot(
                user_id=user_id,
                snapshot_json=_json.dumps(current_summary, default=str),
            )
            db.add(snap)
            db.commit()
        except Exception as e:
            log.warning("[exec_aggregator.diff] snapshot persist failed: %s", e)
            try:
                db.rollback()
            except Exception:
                pass

    return out
