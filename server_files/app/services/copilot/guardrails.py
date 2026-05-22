"""
Aurora Copilot — Operator guardrails (Sprint 3, Appendix K §1C).

Two-tier budget guardrails for the Copilot's Anthropic API spend:
  • SOFT cap (default $4/day): warns via response header, dashboard chip
  • HARD cap (default $5/day): returns HTTP 402, blocks new chats
  • Admin override: POST /copilot/budget-extend writes a NEGATIVE-token
    "credit" row to claude_api_usage, offsetting spend until the next
    UTC-midnight rolling-window boundary.

Plus a DB-backed rate limiter (replaces the in-process deque). Counts
the user's Copilot turns in the last hour via claude_api_usage.
Horizontally consistent across Cloud Run instances.

All functions are DEFENSIVE:
  • DB failures are logged and treated as "no guardrail data available"
    rather than blocking the founder. The hard cap NEVER stalls the
    founder due to a transient DB hiccup — the spend cap is a soft
    operational signal, not a circuit breaker.
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Tuple

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database.models import ClaudeApiUsage
from app.services.copilot.pricing_meta import cost_for_usage_usd

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Configurable thresholds (env-driven)
# ─────────────────────────────────────────────────────────────

def _soft_budget_usd() -> float:
    try:
        return float(os.getenv("AURORA_COPILOT_SOFT_BUDGET_USD", "4"))
    except ValueError:
        return 4.0


def _hard_budget_usd() -> float:
    try:
        return float(os.getenv("AURORA_COPILOT_HARD_BUDGET_USD", "5"))
    except ValueError:
        return 5.0


def _max_turns_per_hour() -> int:
    try:
        return int(os.getenv("AURORA_COPILOT_MAX_TURNS_PER_HOUR", "30"))
    except ValueError:
        return 30


# ─────────────────────────────────────────────────────────────
# Spend computation
# ─────────────────────────────────────────────────────────────

def daily_spend_usd(user_id: int, db: Session) -> float:
    """
    Sum the USD cost across all claude_api_usage rows in the last 24h
    for this user. Returns 0.0 on DB failure (fail-open).
    """
    try:
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
        rows = (
            db.query(
                ClaudeApiUsage.model,
                ClaudeApiUsage.tokens_input,
                ClaudeApiUsage.tokens_output,
                ClaudeApiUsage.tokens_cache_creation,
                ClaudeApiUsage.tokens_cache_read,
            )
            .filter(ClaudeApiUsage.user_id == user_id)
            .filter(ClaudeApiUsage.created_at >= cutoff)
            .all()
        )
    except Exception as e:
        log.warning("[guardrails.daily_spend_usd] DB query failed: %s", e)
        return 0.0

    total = 0.0
    for r in rows:
        total += cost_for_usage_usd(
            model=r.model or "claude-sonnet-4-5-20250929",
            tokens_input=int(r.tokens_input or 0),
            tokens_output=int(r.tokens_output or 0),
            tokens_cache_creation=int(r.tokens_cache_creation or 0),
            tokens_cache_read=int(r.tokens_cache_read or 0),
        )
    return round(total, 4)


def count_turns_last_hour(user_id: int, db: Session) -> int:
    """
    DB-backed turn counter. Replaces the in-process deque so the limit
    is horizontally consistent across Cloud Run instances.

    Returns 0 on DB failure (fail-open — never block the founder due
    to a hiccup querying counters).
    """
    try:
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
        count = (
            db.query(func.count(ClaudeApiUsage.id))
            .filter(ClaudeApiUsage.user_id == user_id)
            .filter(ClaudeApiUsage.created_at >= cutoff)
            .scalar()
        )
        return int(count or 0)
    except Exception as e:
        log.warning("[guardrails.count_turns_last_hour] DB query failed: %s", e)
        return 0


# ─────────────────────────────────────────────────────────────
# Enforcement
# ─────────────────────────────────────────────────────────────

class BudgetExceeded(Exception):
    """Raised when the daily HARD cap is exceeded."""
    def __init__(self, spent_usd: float, limit_usd: float, resets_at_iso: str):
        self.spent_usd = spent_usd
        self.limit_usd = limit_usd
        self.resets_at_iso = resets_at_iso
        super().__init__(f"daily budget exceeded: ${spent_usd:.2f} > ${limit_usd:.2f}")


class RateLimited(Exception):
    """Raised when the per-hour turn limit is exceeded."""
    def __init__(self, turns: int, limit: int, retry_after_s: int):
        self.turns = turns
        self.limit = limit
        self.retry_after_s = retry_after_s
        super().__init__(f"rate limited: {turns}/{limit} turns in the last hour")


def _next_utc_midnight_iso() -> str:
    now = datetime.datetime.utcnow()
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return tomorrow.isoformat() + "Z"


def check_chat_guardrails(user_id: int, db: Session) -> dict:
    """
    Call BEFORE issuing a Claude API request from /copilot/chat.

    Raises:
      RateLimited     — per-hour turn cap exceeded (caller returns 429)
      BudgetExceeded  — daily HARD $-cap exceeded (caller returns 402)

    Returns a dict with current usage state for response headers:
      {
        "spent_usd": 1.23,
        "soft_limit_usd": 4.00,
        "hard_limit_usd": 5.00,
        "soft_warning": False,
        "turns_last_hour": 7,
        "turns_limit": 30,
      }

    Caller adds `X-Aurora-Token-Budget-Warning` header when
    `soft_warning=True` so the UI can render the yellow chip.
    """
    # Rate limit
    turns = count_turns_last_hour(user_id, db)
    limit = _max_turns_per_hour()
    if turns >= limit:
        # Retry after estimate: 1 hour minus elapsed since the oldest
        # turn — but DB doesn't trivially expose that without another
        # query. Use a conservative 60s for now.
        raise RateLimited(turns=turns, limit=limit, retry_after_s=60)

    # Budget
    spent = daily_spend_usd(user_id, db)
    soft = _soft_budget_usd()
    hard = _hard_budget_usd()
    if spent >= hard:
        raise BudgetExceeded(
            spent_usd=spent,
            limit_usd=hard,
            resets_at_iso=_next_utc_midnight_iso(),
        )

    return {
        "spent_usd": spent,
        "soft_limit_usd": soft,
        "hard_limit_usd": hard,
        "soft_warning": spent >= soft,
        "turns_last_hour": turns,
        "turns_limit": limit,
    }


# ─────────────────────────────────────────────────────────────
# Admin override (budget extend)
# ─────────────────────────────────────────────────────────────

def record_budget_extension(
    *,
    user_id: int,
    delta_usd: float,
    reason: str,
    db: Session,
) -> dict:
    """
    Record an admin-issued budget extension as a NEGATIVE-token credit
    row in claude_api_usage. The negative tokens flow through
    `cost_for_usage_usd()` which subtracts from the daily spend total.

    `delta_usd` is the dollar amount to credit (e.g., 5.0 means "give
    me $5 more headroom today"). We back-compute negative output
    tokens at the current model's output rate so the spend reduces
    by exactly delta_usd.

    Returns the inserted row dict + the new resulting spend.
    """
    from app.services.copilot.pricing_meta import _MODEL_RATES_USD_PER_MTOK, _FALLBACK_RATES

    model = (os.getenv("AURORA_COPILOT_MODEL") or "claude-sonnet-4-5-20250929").strip()
    rates = _MODEL_RATES_USD_PER_MTOK.get(model, _FALLBACK_RATES)
    # Negative output tokens: delta_usd / output_rate * 1_000_000
    # Then negate to make it subtract from spend.
    out_rate = rates["output"]
    if out_rate <= 0:
        out_rate = _FALLBACK_RATES["output"]
    negative_tokens = -int((delta_usd / out_rate) * 1_000_000)

    row = ClaudeApiUsage(
        user_id=user_id,
        conversation_id=None,
        model=model,
        tokens_input=0,
        tokens_output=negative_tokens,
        tokens_cache_creation=0,
        tokens_cache_read=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # Audit event
    try:
        from app.services.exec_events import publish_exec_event
        publish_exec_event(
            db,
            kind="copilot_budget_extended",
            severity="warning",
            title=f"Copilot budget extended +${delta_usd:.2f}",
            detail=f"user_id={user_id} reason={reason[:200]} negative_tokens={negative_tokens}",
            related_entity_type="claude_api_usage",
            related_entity_id=row.id,
        )
    except Exception:
        pass

    new_spent = daily_spend_usd(user_id, db)
    return {
        "ok": True,
        "credit_row_id": row.id,
        "delta_usd": delta_usd,
        "reason": reason,
        "new_spent_usd": new_spent,
        "hard_limit_usd": _hard_budget_usd(),
    }


def get_usage_snapshot(user_id: int, db: Session) -> dict:
    """Read-only snapshot for the UI / palette / admin views."""
    return {
        "spent_usd": daily_spend_usd(user_id, db),
        "soft_limit_usd": _soft_budget_usd(),
        "hard_limit_usd": _hard_budget_usd(),
        "turns_last_hour": count_turns_last_hour(user_id, db),
        "turns_limit": _max_turns_per_hour(),
        "resets_at": _next_utc_midnight_iso(),
    }
