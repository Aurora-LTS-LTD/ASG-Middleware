"""
Aurora LTS — LLM provider abstraction package (Sprint 4, Appendix L §4.2).

Provider-neutral interface (`LLMProvider`) with concrete implementations:
  • AnthropicProvider  — wraps app/services/copilot/anthropic_client.py
  • VertexGeminiProvider — Google Vertex AI Gemini 1.5 Pro/Flash

Resolve via the registry:
    from app.services.llm import get_provider
    p = get_provider("vertex_gemini")
    text, usage = await p.one_shot("Classify this receipt: ...")

The Copilot chat path stays on Anthropic (proven tool-calling). New
features (receipt classification, daily insights brief, WhatsApp
template drafter, DSAR summarizer) use the Vertex provider to consume
the $300k Google for Startups credits.
"""

from app.services.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMStreamEvent,
    LLMTool,
    LLMUsage,
)
from app.services.llm.registry import get_provider

__all__ = [
    "LLMMessage",
    "LLMProvider",
    "LLMStreamEvent",
    "LLMTool",
    "LLMUsage",
    "get_provider",
]
