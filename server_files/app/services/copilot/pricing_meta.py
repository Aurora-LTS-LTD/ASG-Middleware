"""
Aurora Copilot — Aurora ops cost rate table (Sprint 3, Appendix K §1C).

Per-model USD-per-million-tokens rates for Anthropic Claude models.
USED ONLY for Aurora's INTERNAL ops cost guardrails (daily $-budget cap
on the founder's Copilot usage). NEVER exposed to Claude in prompts and
NEVER surfaced as a "client price" — per the founder pivot 2026-05-20
that removed the pricing engine from the Copilot scope.

Source: Anthropic's published pricing page. Refresh when Anthropic
rotates pricing (rare; typically annual).

Conservative defaults:
  • Unknown model → fall back to claude-sonnet-4-5 rates (the most
    expensive model in the table). This means we OVERESTIMATE spend
    for cheaper models, which is the safe failure mode.
"""

from __future__ import annotations

from typing import Dict


# USD per 1,000,000 tokens
_MODEL_RATES_USD_PER_MTOK: Dict[str, Dict[str, float]] = {
    # Claude Sonnet 4.5 (current default in AURORA_COPILOT_MODEL)
    "claude-sonnet-4-5-20250929": {
        "input": 3.00,
        "output": 15.00,
        "cache_creation": 3.75,
        "cache_read": 0.30,
    },
    # Claude Sonnet 4 (older)
    "claude-sonnet-4-20250514": {
        "input": 3.00,
        "output": 15.00,
        "cache_creation": 3.75,
        "cache_read": 0.30,
    },
    # Claude Opus 4 — currently NOT used (defensive entry for future)
    "claude-opus-4-20250514": {
        "input": 15.00,
        "output": 75.00,
        "cache_creation": 18.75,
        "cache_read": 1.50,
    },
    # Claude Haiku 3.5 — cheap; we could use it for summarization later
    "claude-3-5-haiku-20241022": {
        "input": 0.80,
        "output": 4.00,
        "cache_creation": 1.00,
        "cache_read": 0.08,
    },
}

# Conservative fallback (most expensive of the commonly-used Sonnet line).
_FALLBACK_RATES = _MODEL_RATES_USD_PER_MTOK["claude-sonnet-4-5-20250929"]


def cost_for_usage_usd(
    *,
    model: str,
    tokens_input: int = 0,
    tokens_output: int = 0,
    tokens_cache_creation: int = 0,
    tokens_cache_read: int = 0,
) -> float:
    """
    Compute the USD cost for a single API call's token usage.

    Returns 0.0 for negative token counts (used by the budget-extend
    feature to inject offsetting "credit" rows into claude_api_usage).
    """
    rates = _MODEL_RATES_USD_PER_MTOK.get(model, _FALLBACK_RATES)
    cost = 0.0
    if tokens_input > 0:
        cost += (tokens_input / 1_000_000) * rates["input"]
    if tokens_output > 0:
        cost += (tokens_output / 1_000_000) * rates["output"]
    if tokens_cache_creation > 0:
        cost += (tokens_cache_creation / 1_000_000) * rates["cache_creation"]
    if tokens_cache_read > 0:
        cost += (tokens_cache_read / 1_000_000) * rates["cache_read"]

    # Allow negative-token "credit" rows (admin budget-extend) to reduce spend
    if tokens_input < 0:
        cost += (tokens_input / 1_000_000) * rates["input"]
    if tokens_output < 0:
        cost += (tokens_output / 1_000_000) * rates["output"]

    return round(cost, 6)


def known_models() -> list[str]:
    return sorted(_MODEL_RATES_USD_PER_MTOK.keys())
