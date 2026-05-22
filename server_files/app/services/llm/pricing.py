"""
Aurora LLM — unified per-model cost rate table (Sprint 4, Appendix L §4.7).

Source of truth for "what does an Anthropic-or-Vertex API call cost in
$USD?" Used by:
  • app/services/copilot/guardrails.py (legacy claude-only path) —
    still imports cost_for_usage_usd for backward compat.
  • app/services/llm/{anthropic,vertex}_provider.py — both providers
    compute the `cost_usd` field on LLMUsage via this module.
  • app/services/exec_aggregator.py — future LLM-cost tile aggregation.

Conservative fallback: unknown model uses the most-expensive sonnet rate
so we never UNDER-estimate spend. Daily budget caps should be conservative.

Vertex AI me-west1 list prices (May 2026; refresh after Anthropic /
Google pricing rotations — these are stale within ~1 year of release).
"""

from __future__ import annotations

from typing import Dict, Optional


# USD per 1,000,000 tokens — Anthropic
_ANTHROPIC_RATES: Dict[str, Dict[str, float]] = {
    "claude-sonnet-4-5-20250929": {
        "input": 3.00, "output": 15.00,
        "cache_creation": 3.75, "cache_read": 0.30,
    },
    "claude-sonnet-4-20250514": {
        "input": 3.00, "output": 15.00,
        "cache_creation": 3.75, "cache_read": 0.30,
    },
    "claude-opus-4-20250514": {
        "input": 15.00, "output": 75.00,
        "cache_creation": 18.75, "cache_read": 1.50,
    },
    "claude-3-5-haiku-20241022": {
        "input": 0.80, "output": 4.00,
        "cache_creation": 1.00, "cache_read": 0.08,
    },
}

# USD per 1,000,000 tokens — Vertex AI Gemini
# Rates source: Anthropic + Google published pricing. The credit pool
# from Google for Startups effectively zeros these — we still track for
# audit + future migration off the credit.
_VERTEX_RATES: Dict[str, Dict[str, float]] = {
    "gemini-1.5-pro-002": {
        "input": 1.25, "output": 5.00,
        "cache_creation": 0.0, "cache_read": 0.0,
    },
    "gemini-1.5-flash-002": {
        "input": 0.075, "output": 0.30,
        "cache_creation": 0.0, "cache_read": 0.0,
    },
    # Placeholders for newer models (when released)
    "gemini-2.0-pro": {
        "input": 1.50, "output": 6.00,
        "cache_creation": 0.0, "cache_read": 0.0,
    },
    "gemini-2.0-flash": {
        "input": 0.10, "output": 0.40,
        "cache_creation": 0.0, "cache_read": 0.0,
    },
}

_ALL_RATES = {**_ANTHROPIC_RATES, **_VERTEX_RATES}

# Conservative fallback: Sonnet 4.5 (the most expensive non-Opus model
# in active use). Picked over Opus because Opus is not in our default
# rotation; using Opus as fallback would massively over-estimate Vertex
# costs.
_FALLBACK_RATES = _ANTHROPIC_RATES["claude-sonnet-4-5-20250929"]


def cost_for_usage_usd(
    *,
    model: str,
    tokens_input: int = 0,
    tokens_output: int = 0,
    tokens_cache_creation: int = 0,
    tokens_cache_read: int = 0,
    provider: Optional[str] = None,  # ignored; model name is the key
) -> float:
    """Compute the USD cost for a single API call's token usage.

    Accepts NEGATIVE token counts for the admin budget-extend "credit"
    feature (records a negative-token row that subtracts from spend).
    """
    rates = _ALL_RATES.get(model, _FALLBACK_RATES)
    cost = 0.0
    cost += (tokens_input / 1_000_000) * rates["input"]
    cost += (tokens_output / 1_000_000) * rates["output"]
    cost += (tokens_cache_creation / 1_000_000) * rates.get("cache_creation", 0.0)
    cost += (tokens_cache_read / 1_000_000) * rates.get("cache_read", 0.0)
    return round(cost, 6)


def known_models() -> list[str]:
    return sorted(_ALL_RATES.keys())


def provider_for_model(model: str) -> str:
    """Best-effort: return the provider name a model belongs to."""
    if model in _ANTHROPIC_RATES:
        return "anthropic"
    if model in _VERTEX_RATES:
        return "vertex_gemini"
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gemini-"):
        return "vertex_gemini"
    return "unknown"
