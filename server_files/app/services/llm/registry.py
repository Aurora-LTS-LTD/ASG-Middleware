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
      • "anthropic"     — Claude via Anthropic SDK
      • "vertex_gemini" — Google Vertex AI Gemini 1.5 Pro/Flash
    """
    if name in _PROVIDERS:
        return _PROVIDERS[name]

    if name == "anthropic":
        from app.services.llm.anthropic_provider import AnthropicProvider
        _PROVIDERS[name] = AnthropicProvider()
    elif name == "vertex_gemini":
        from app.services.llm.vertex_provider import VertexGeminiProvider
        _PROVIDERS[name] = VertexGeminiProvider()
    else:
        raise ValueError(
            f"Unknown LLM provider: {name!r}. "
            f"Supported: 'anthropic', 'vertex_gemini'."
        )

    return _PROVIDERS[name]


def list_providers() -> list[str]:
    return ["anthropic", "vertex_gemini"]
