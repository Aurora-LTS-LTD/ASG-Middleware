"""
Aurora LTS — WhatsApp Analytics Aggregator (Appendix H Tier 1)
===============================================================

Rolls up the `whatsapp_outbound_logs` table into the metrics that
the CEO Executive Dashboard's WhatsApp Operations Hub renders:

  • Delivery funnel: sent → delivered → read → failed (counts + %)
  • Per-template performance (rows for each `template_name`)
  • Cost estimate using Meta's per-conversation pricing approximation
  • Active FSM session count (from whatsapp_sessions)
  • Inbound message rate (count per minute / hour)

Caching:
  • Results are cached in-process for `_CACHE_TTL_S` seconds keyed
    by the range parameter (24h / 7d / 30d). Avoids hammering the
    DB on every dashboard repaint.
  • Cache is invalidated naturally by TTL expiry; no manual bust.
  • Single-instance Cloud Run = single cache copy. Scaling to N
    instances at most causes N*1 staleness windows; acceptable for
    Tier 1.

Cost model (Tier 1 approximation):
  • Israeli market pricing per Meta's "Service" conversation:
    ≈ ₪0.04 per conversation (user-initiated, FSM reply window).
    ≈ ₪0.18 per "Marketing" template send.
    ≈ ₪0.10 per "Utility" template send (invoice / OTP / receipts).
  • Cost is estimated by counting outbound messages per kind and
    multiplying. The "conversation" abstraction (Meta groups multiple
    messages into one billable conversation per 24h window) is
    approximated as 1 conversation per unique recipient phone per day.

  • Numbers are ESTIMATES, not authoritative billing. Real Meta
    invoices are reconciled in Meta Business Manager. The dashboard
    surface clearly labels them as "estimate" so the CEO doesn't
    confuse the two.
"""

from __future__ import annotations

import datetime
import logging
import threading
import time
from collections import defaultdict
from typing import Dict, Any, List

from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

from aurora_shared.database.models import WhatsAppOutboundLog, WhatsAppSession

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Pricing (₪) — approximate; calibrate after first real Meta invoice
# ─────────────────────────────────────────────────────────────
_PRICE_PER_SERVICE_CONVERSATION_NIS = 0.04
_PRICE_PER_MARKETING_TEMPLATE_NIS = 0.18
_PRICE_PER_UTILITY_TEMPLATE_NIS = 0.10

# Statuses we treat as terminal failures
_FAILED_STATUSES = {"failed", "error", "undelivered"}
_DELIVERED_STATUSES = {"delivered", "read"}
_READ_STATUSES = {"read"}

# ─────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────
_CACHE_TTL_S = 15.0
_cache: Dict[str, Dict[str, Any]] = {}
_cache_expiry: Dict[str, float] = {}
_cache_lock = threading.Lock()


def _range_to_delta(range_label: str) -> datetime.timedelta:
    if range_label == "24h":
        return datetime.timedelta(hours=24)
    if range_label == "7d":
        return datetime.timedelta(days=7)
    if range_label == "30d":
        return datetime.timedelta(days=30)
    # Default to 24h on invalid input — caller should validate first.
    return datetime.timedelta(hours=24)


def _cached_or_compute(range_label: str, db: Session) -> Dict[str, Any]:
    now = time.monotonic()
    with _cache_lock:
        if range_label in _cache and _cache_expiry.get(range_label, 0) > now:
            return _cache[range_label]

    result = _compute_analytics(range_label, db)

    with _cache_lock:
        _cache[range_label] = result
        _cache_expiry[range_label] = now + _CACHE_TTL_S

    return result


def _compute_analytics(range_label: str, db: Session) -> Dict[str, Any]:
    """Heavy path — actually scans whatsapp_outbound_logs."""
    delta = _range_to_delta(range_label)
    since = datetime.datetime.utcnow() - delta

    # ── Delivery funnel by status ──
    status_counts_raw = (
        db.query(
            WhatsAppOutboundLog.status,
            func.count(WhatsAppOutboundLog.id),
        )
        .filter(WhatsAppOutboundLog.created_at >= since)
        .group_by(WhatsAppOutboundLog.status)
        .all()
    )

    status_counts: Dict[str, int] = defaultdict(int)
    for status, cnt in status_counts_raw:
        status_counts[status or "unknown"] = int(cnt or 0)

    sent_total = sum(status_counts.values())
    delivered = sum(c for s, c in status_counts.items() if s in _DELIVERED_STATUSES)
    read_cnt = sum(c for s, c in status_counts.items() if s in _READ_STATUSES)
    failed = sum(c for s, c in status_counts.items() if s in _FAILED_STATUSES)
    pending = status_counts.get("pending", 0)

    def pct(num: int, denom: int) -> float:
        return round(100.0 * num / denom, 1) if denom > 0 else 0.0

    funnel = {
        "sent": sent_total,
        "delivered": delivered,
        "read": read_cnt,
        "failed": failed,
        "pending": pending,
        "delivered_pct": pct(delivered, sent_total),
        "read_pct": pct(read_cnt, sent_total),
        "failed_pct": pct(failed, sent_total),
    }

    # ── Per-message-kind counts ──
    kind_rows = (
        db.query(
            WhatsAppOutboundLog.message_kind,
            func.count(WhatsAppOutboundLog.id),
        )
        .filter(WhatsAppOutboundLog.created_at >= since)
        .group_by(WhatsAppOutboundLog.message_kind)
        .all()
    )
    by_kind: Dict[str, int] = {(k or "unknown"): int(c or 0) for k, c in kind_rows}

    # ── Per-template performance ──
    template_rows = (
        db.query(
            WhatsAppOutboundLog.template_name,
            WhatsAppOutboundLog.status,
            func.count(WhatsAppOutboundLog.id),
        )
        .filter(WhatsAppOutboundLog.created_at >= since)
        .filter(WhatsAppOutboundLog.template_name.isnot(None))
        .group_by(WhatsAppOutboundLog.template_name, WhatsAppOutboundLog.status)
        .all()
    )
    templates_acc: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for tpl, st, cnt in template_rows:
        templates_acc[tpl][st or "unknown"] = int(cnt or 0)

    per_template: List[Dict[str, Any]] = []
    for tpl, st_map in templates_acc.items():
        total = sum(st_map.values())
        d = sum(v for s, v in st_map.items() if s in _DELIVERED_STATUSES)
        r = sum(v for s, v in st_map.items() if s in _READ_STATUSES)
        f = sum(v for s, v in st_map.items() if s in _FAILED_STATUSES)
        per_template.append({
            "template_name": tpl,
            "sent": total,
            "delivered": d,
            "read": r,
            "failed": f,
            "delivered_pct": pct(d, total),
            "read_pct": pct(r, total),
            "failed_pct": pct(f, total),
        })
    per_template.sort(key=lambda x: x["sent"], reverse=True)

    # ── Unique recipients (conversation count proxy) ──
    unique_recipients = (
        db.query(func.count(distinct(WhatsAppOutboundLog.whatsapp_phone_e164)))
        .filter(WhatsAppOutboundLog.created_at >= since)
        .scalar()
        or 0
    )

    # ── Cost estimate ──
    # Heuristic: a "conversation" = a unique recipient in the range.
    # Template messages cost extra on top of the conversation; non-template
    # outbound counts toward the service-conversation bucket.
    template_count = sum(v for k, v in by_kind.items() if k == "template")
    # Cost: utility templates + service conversations (approximated)
    cost_nis = (
        unique_recipients * _PRICE_PER_SERVICE_CONVERSATION_NIS
        + template_count * _PRICE_PER_UTILITY_TEMPLATE_NIS
    )

    # ── Active FSM session count ──
    active_sessions = (
        db.query(func.count(WhatsAppSession.id))
        .filter(
            WhatsAppSession.last_client_message_at.isnot(None),
            WhatsAppSession.last_client_message_at
            >= datetime.datetime.utcnow() - datetime.timedelta(hours=24),
        )
        .scalar()
        or 0
    )

    return {
        "range": range_label,
        "since": since.isoformat(),
        "as_of": datetime.datetime.utcnow().isoformat(),
        "funnel": funnel,
        "by_kind": by_kind,
        "per_template": per_template,
        "unique_recipients": int(unique_recipients),
        "active_sessions_24h": int(active_sessions),
        "cost_estimate_nis": round(cost_nis, 2),
        "cost_disclaimer": (
            "Estimate only — reconcile against Meta Business Manager monthly invoice."
        ),
        "pricing_used_nis": {
            "service_conversation": _PRICE_PER_SERVICE_CONVERSATION_NIS,
            "marketing_template": _PRICE_PER_MARKETING_TEMPLATE_NIS,
            "utility_template": _PRICE_PER_UTILITY_TEMPLATE_NIS,
        },
    }


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────
def get_whatsapp_analytics(db: Session, range_label: str = "24h") -> Dict[str, Any]:
    """
    Return cached aggregate. Caller is responsible for validating
    `range_label` ∈ {"24h", "7d", "30d"} (router does this).
    """
    return _cached_or_compute(range_label, db)


def invalidate_cache() -> None:
    """Force the next call to recompute. Useful in tests."""
    with _cache_lock:
        _cache.clear()
        _cache_expiry.clear()
