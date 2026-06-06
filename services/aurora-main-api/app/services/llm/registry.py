"""
LLM provider registry — single lookup point for callers (Sprint 4).

Usage:
    from app.services.llm import get_provider
    p = get_provider("vertex_gemini")
    text, usage = await p.one_shot("Classify: ...")

Providers are constructed lazily; first call materializes the instance,
subsequent calls return the same instance (for the SDK-init reuse).
"""

from __future__ import annotations

from typing import Dict

from app.services.llm.base import LLMProvider


_PROVIDERS: Dict[str, LLMProvider] = {}


def get_provider(name: str) -> LLMProvider:
    """Return the provider singleton for `name`.

    Known providers:
      • "vertex_gemini" — Google Vertex AI Gemini 1.5 Pro/Flash

    NOTE (monorepo split): the "anthropic" provider was SEVERED from the M1
    monolith. Claude/Anthropic is M2-only — the Copilot calls
    app.services.copilot.anthropic_client directly, not via this registry. The
    only M1->copilot static edge ran through anthropic_provider.py here; removing
    this branch drops copilot.* out of M1's import closure. anthropic_provider.py
    relocates to aurora-api-core in Phase 2B.
    """
    if name in _PROVIDERS:
        return _PROVIDERS[name]

    if name == "vertex_gemini":
        from app.services.llm.vertex_provider import VertexGeminiProvider
        _PROVIDERS[name] = VertexGeminiProvider()
    else:
        raise ValueError(
            f"Unknown LLM provider: {name!r}. Supported: 'vertex_gemini'. "
            f"('anthropic' is M2-only — use app.services.copilot.anthropic_client.)"
        )

    return _PROVIDERS[name]


def list_providers() -> list[str]:
    return ["vertex_gemini"]
