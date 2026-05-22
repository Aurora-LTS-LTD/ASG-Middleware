"""
Aurora LLM — provider-neutral interface + canonical types (Sprint 4).

Defines the contract that AnthropicProvider and VertexGeminiProvider
both honor. Callers depend on this module, not on the concrete provider,
so swapping providers (or A/B testing both for the same workload) is a
config change rather than a code change.

Canonical wire shape: Anthropic's content-block format. Vertex provider
adapts inbound/outbound between Gemini's `Content(parts=[Part(text=)])`
and our canonical `LLMMessage(content=[{type:'text',text:''}])`.
"""

from __future__ import annotations

from typing import AsyncIterator, Literal, Optional, Protocol

from pydantic import BaseModel


# ─────────────────────────────────────────────────────────────
# Canonical message / tool / usage / stream-event types
# ─────────────────────────────────────────────────────────────

class LLMMessage(BaseModel):
    """A single message in the conversation history.

    `content` is a list of Anthropic-style content blocks:
      • {"type": "text", "text": str}
      • {"type": "tool_use", "id": str, "name": str, "input": dict}
      • {"type": "tool_result", "tool_use_id": str, "content": str | object}
    """

    role: Literal["user", "assistant", "system"]
    content: list[dict]


class LLMTool(BaseModel):
    """A tool/function declaration shipped to the model."""

    name: str
    description: str
    input_schema: dict  # JSON schema for the tool's arguments


class LLMUsage(BaseModel):
    """Token + cost accounting for a single API call."""

    provider: str  # "anthropic" | "vertex_gemini"
    model: str
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_cache_read: int = 0
    tokens_cache_creation: int = 0
    cost_usd: float = 0.0


class LLMStreamEvent(BaseModel):
    """Provider-agnostic stream event yielded by `stream_chat()`.

    Caller maps these to UI updates or persists them:
      type=text_delta    → append `text` to the streaming assistant turn
      type=tool_use      → assistant emitted a tool_use block (id, name, input)
      type=message_stop  → assistant's turn ended (usage populated)
      type=error         → terminal failure
      type=_final        → wrapper-emitted assembled message + usage
    """

    type: Literal[
        "text_delta", "tool_use", "message_stop", "error", "_final",
    ]
    text: Optional[str] = None
    tool_use: Optional[dict] = None
    error_message: Optional[str] = None
    usage: Optional[LLMUsage] = None
    raw: Optional[dict] = None  # original provider event (debug)


# ─────────────────────────────────────────────────────────────
# Provider Protocol
# ─────────────────────────────────────────────────────────────

class LLMProvider(Protocol):
    """Provider-neutral LLM interface.

    Implementations:
      • app.services.llm.anthropic_provider.AnthropicProvider
      • app.services.llm.vertex_provider.VertexGeminiProvider
    """

    name: str

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        tools: Optional[list[LLMTool]] = None,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Stream a chat turn with optional tool-calling."""
        ...

    async def one_shot(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 2048,
        response_json_schema: Optional[dict] = None,
    ) -> tuple[str, LLMUsage]:
        """Non-streaming convenience for simple text/structured-output tasks.

        If `response_json_schema` is provided AND the provider supports
        structured output, the model is constrained to emit JSON matching
        the schema. Otherwise returns plain text.
        """
        ...


__all__ = [
    "LLMMessage",
    "LLMTool",
    "LLMUsage",
    "LLMStreamEvent",
    "LLMProvider",
]
