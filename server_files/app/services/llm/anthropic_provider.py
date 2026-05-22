"""
AnthropicProvider — Sprint 4 wrapper over the existing anthropic_client.py.

Preserves the proven Copilot tool-calling path. Translates the existing
`stream_chat` generator's anthropic-typed events into the canonical
`LLMStreamEvent` shape so callers depend on the provider interface,
not on Anthropic specifics.

The Copilot's /api/v1/admin/exec/copilot/chat handler in admin_exec.py
continues to use anthropic_client.py directly for now (no churn). This
provider is for NEW callers (Vertex-style features that want
Anthropic-as-fallback, or future A/B paths).
"""

from __future__ import annotations

import logging
import os
from typing import AsyncIterator, Optional

from app.services.llm.base import (
    LLMMessage,
    LLMStreamEvent,
    LLMTool,
    LLMUsage,
)
from app.services.llm.pricing import cost_for_usage_usd

log = logging.getLogger(__name__)


class AnthropicProvider:
    name = "anthropic"

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        tools: Optional[list[LLMTool]] = None,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Stream a chat turn via Anthropic SDK; emit canonical events."""
        from app.services.copilot.anthropic_client import (
            stream_chat as _underlying_stream,
        )

        # Canonical → Anthropic message shape (they match natively)
        anthropic_messages = [
            {"role": m.role, "content": m.content} for m in messages
        ]

        async for event in _underlying_stream(
            anthropic_messages,
            model=model,
            max_tokens=max_tokens,
            extra_system=system_prompt,
        ):
            etype = event.get("type")

            if etype == "_aurora_final":
                msg = event.get("message", {})
                usage_dict = event.get("usage", {})
                usage = LLMUsage(
                    provider="anthropic",
                    model=msg.get("model") or model or "unknown",
                    tokens_input=int(usage_dict.get("input_tokens") or 0),
                    tokens_output=int(usage_dict.get("output_tokens") or 0),
                    tokens_cache_creation=int(
                        usage_dict.get("cache_creation_input_tokens") or 0
                    ),
                    tokens_cache_read=int(
                        usage_dict.get("cache_read_input_tokens") or 0
                    ),
                )
                usage.cost_usd = cost_for_usage_usd(
                    model=usage.model,
                    tokens_input=usage.tokens_input,
                    tokens_output=usage.tokens_output,
                    tokens_cache_creation=usage.tokens_cache_creation,
                    tokens_cache_read=usage.tokens_cache_read,
                )
                yield LLMStreamEvent(
                    type="_final",
                    usage=usage,
                    raw=event,
                )
                return

            if etype == "error":
                yield LLMStreamEvent(
                    type="error",
                    error_message=str(event.get("error") or "anthropic error"),
                    raw=event,
                )
                return

            if etype == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    yield LLMStreamEvent(
                        type="text_delta",
                        text=delta.get("text") or "",
                        raw=event,
                    )

            elif etype == "content_block_start":
                block = event.get("content_block") or {}
                if block.get("type") == "tool_use":
                    yield LLMStreamEvent(
                        type="tool_use",
                        tool_use={
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "input": block.get("input") or {},
                        },
                        raw=event,
                    )

            elif etype == "message_stop":
                yield LLMStreamEvent(type="message_stop", raw=event)

            # message_start / content_block_stop / message_delta —
            # not surfaced in canonical events; usage rolls into _final.

    async def one_shot(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 2048,
        response_json_schema: Optional[dict] = None,
    ) -> tuple[str, LLMUsage]:
        """Single-turn convenience for simple text generation tasks."""
        from app.services.copilot.anthropic_client import _get_async_client

        client = _get_async_client()
        use_model = model or os.getenv(
            "AURORA_COPILOT_MODEL", "claude-sonnet-4-5-20250929"
        )

        # response_json_schema is currently ignored for Anthropic (their
        # JSON-mode support differs from Vertex). Caller should
        # post-validate with Pydantic.

        resp = await client.messages.create(
            model=use_model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract text from the first text content block
        text_parts = []
        for block in resp.content or []:
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", "") or "")
        text = "".join(text_parts)

        usage = LLMUsage(
            provider="anthropic",
            model=use_model,
            tokens_input=int(getattr(resp.usage, "input_tokens", 0) or 0),
            tokens_output=int(getattr(resp.usage, "output_tokens", 0) or 0),
        )
        usage.cost_usd = cost_for_usage_usd(
            model=use_model,
            tokens_input=usage.tokens_input,
            tokens_output=usage.tokens_output,
        )
        return text, usage
