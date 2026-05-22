"""
Aurora Copilot — Anthropic client wrapper (Sprint 3).

Thin abstraction over the anthropic SDK:
  • Lazy-imports `anthropic` (so dev venvs without the package can
    still load this module — matches the auth_oidc.py pattern).
  • `stream_chat(...)` returns an async iterator of SSE-style events
    that the FastAPI router forwards to the browser.
  • Tool definitions are sourced from `tools.py` (single source of truth).

Configuration via env:
  ANTHROPIC_API_KEY              — required at runtime (Secret Manager)
  AURORA_COPILOT_MODEL           — default: claude-sonnet-4-5-20250929
  AURORA_COPILOT_MAX_TOKENS      — default: 4096
"""

from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator, Dict, Any, List, Optional

from app.services.copilot.tools import ANTHROPIC_TOOLS
from app.services.copilot.prompts import SYSTEM_PROMPT

log = logging.getLogger(__name__)


DEFAULT_MODEL = os.getenv(
    "AURORA_COPILOT_MODEL", "claude-sonnet-4-5-20250929"
)
DEFAULT_MAX_TOKENS = int(os.getenv("AURORA_COPILOT_MAX_TOKENS", "4096"))


class CopilotConfigError(Exception):
    """Raised when the Copilot is asked to call Claude but isn't configured."""


def _get_async_client():
    """Lazy import + construction. Returns an `AsyncAnthropic` client.

    Raises CopilotConfigError if anthropic isn't installed OR the API key
    isn't set.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise CopilotConfigError(
            "ANTHROPIC_API_KEY env var is not set "
            "(mount via Cloud Run --set-secrets in production)"
        )
    try:
        from anthropic import AsyncAnthropic
    except ImportError as e:
        raise CopilotConfigError(
            f"anthropic SDK not installed: {e}. "
            "Ensure requirements.lock includes anthropic==0.40.x"
        )
    return AsyncAnthropic(api_key=api_key)


async def stream_chat(
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    extra_system: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """
    Stream a Claude chat turn with provisioning tools available.

    Args:
        messages: Anthropic-format conversation history. Each item is
            `{"role": "user"|"assistant"|"tool_result", "content": ...}`.
        model:     Override the default model (env-default applies).
        max_tokens: Override the default token cap.
        extra_system: Optional additional system-prompt text appended
            after the canonical SYSTEM_PROMPT (e.g., founder-name
            personalization or "User is currently on the
            /executive/categories page").

    Yields a sequence of dicts, each one a parsed Anthropic stream
    event. Caller maps these to SSE frames:
        {"type": "message_start",   "message": {...}}
        {"type": "content_block_start", "index": 0, "content_block": {...}}
        {"type": "content_block_delta", "index": 0, "delta": {"text": "..."}}
        {"type": "content_block_stop", "index": 0}
        {"type": "message_delta",   "delta": {...}, "usage": {...}}
        {"type": "message_stop"}
        {"type": "error",           "error": {...}}

    The final assembled message (with assembled content blocks + final
    `usage` numbers) is yielded as a single
        {"type": "_aurora_final", "message": {...}, "usage": {...}}
    event by this wrapper. The router uses it to persist a single
    CopilotMessage row at end-of-stream.
    """
    use_model = model or DEFAULT_MODEL
    use_max = max_tokens or DEFAULT_MAX_TOKENS

    try:
        client = _get_async_client()
    except CopilotConfigError as e:
        yield {
            "type": "error",
            "error": {
                "type": "copilot_config_error",
                "message": str(e),
            },
        }
        return

    system_text = SYSTEM_PROMPT
    if extra_system:
        system_text = SYSTEM_PROMPT + "\n\n## Additional context\n" + extra_system

    # Build the final assembled state as we stream so the router can
    # persist one consolidated CopilotMessage row at message_stop.
    assembled_blocks: List[Dict[str, Any]] = []
    final_usage: Dict[str, Any] = {}
    final_stop_reason: Optional[str] = None

    try:
        async with client.messages.stream(
            model=use_model,
            max_tokens=use_max,
            system=system_text,
            tools=ANTHROPIC_TOOLS,
            messages=messages,
        ) as stream:
            async for event in stream:
                # The SDK yields typed pydantic-style events; convert to
                # plain dicts via model_dump() to forward over SSE.
                if hasattr(event, "model_dump"):
                    payload = event.model_dump()
                elif isinstance(event, dict):
                    payload = event
                else:
                    payload = {"type": "unknown", "repr": repr(event)[:200]}
                yield payload

                # Track stop_reason + usage from message_delta + message_stop
                etype = payload.get("type")
                if etype == "message_delta":
                    delta = payload.get("delta") or {}
                    if "stop_reason" in delta:
                        final_stop_reason = delta["stop_reason"]
                    usage = payload.get("usage") or {}
                    if usage:
                        final_usage.update(usage)
                # message_start carries input_tokens
                if etype == "message_start":
                    msg = payload.get("message") or {}
                    usage = msg.get("usage") or {}
                    if usage:
                        final_usage.update(usage)

            # After the async-iter ends, the SDK has fully assembled the
            # message. Pull it for persistence.
            final_message = await stream.get_final_message()
            if hasattr(final_message, "model_dump"):
                fm_dict = final_message.model_dump()
            else:
                fm_dict = {}
            assembled_blocks = fm_dict.get("content") or []
            final_stop_reason = fm_dict.get("stop_reason") or final_stop_reason
            usage = fm_dict.get("usage") or {}
            if usage:
                final_usage.update(usage)

    except Exception as e:
        log.warning(
            "[copilot.stream_chat] failed (%s): %s",
            type(e).__name__, str(e)[:300],
        )
        yield {
            "type": "error",
            "error": {
                "type": "anthropic_stream_error",
                "message": f"{type(e).__name__}: {str(e)[:300]}",
            },
        }
        return

    # Synthetic final event for the router to consume.
    yield {
        "type": "_aurora_final",
        "message": {
            "role": "assistant",
            "content": assembled_blocks,
            "stop_reason": final_stop_reason,
            "model": use_model,
        },
        "usage": final_usage,
    }


def extract_pending_tool_uses(content_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    From an assistant message's content, return any tool_use blocks
    that need approval. The router uses this to surface "Pending
    Approval" cards in the UI.

    For Sprint 3, only `propose_provisioning_blueprint` + the other
    write tools (update_category, delete_category, assign_org_to_category)
    require approval. `search_existing_categories` is read-only and
    runs immediately when Claude calls it.
    """
    from app.services.copilot.tools import WRITE_TOOLS

    out: List[Dict[str, Any]] = []
    for block in content_blocks or []:
        if block.get("type") == "tool_use" and block.get("name") in WRITE_TOOLS:
            out.append(block)
    return out


def messages_jsonsafe_dump(content) -> str:
    """JSON-encode an Anthropic content block list for DB storage."""
    return json.dumps(content, default=str, ensure_ascii=False)


def messages_jsonsafe_load(blob: str):
    """Inverse of dump — read a stored content JSON back to a Python list."""
    try:
        return json.loads(blob)
    except Exception:
        return [{"type": "text", "text": blob}]
