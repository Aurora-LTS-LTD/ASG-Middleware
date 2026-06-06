"""
VertexGeminiProvider — Google Vertex AI Gemini (Sprint 4, Appendix L §4.2).

Lazy-imports `vertexai` so dev venvs without google-cloud-aiplatform
can still load this module (matches the auth_oidc.py + anthropic_client.py
patterns).

Authentication:
  Uses Application Default Credentials. On Cloud Run this is the
  aurora-run@aurora-lts-prod.iam.gserviceaccount.com service account
  (granted roles/aiplatform.user during Sprint 4 deploy).

Config via env (set in Cloud Run during deploy):
  VERTEX_PROJECT=aurora-lts-prod
  VERTEX_LOCATION=me-west1
  VERTEX_DEFAULT_MODEL=gemini-1.5-pro-002      (used by `pro` workloads)
  VERTEX_DEFAULT_FAST_MODEL=gemini-1.5-flash-002  (used by `flash` workloads)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import AsyncIterator, Optional

from app.services.llm.base import (
    LLMMessage,
    LLMStreamEvent,
    LLMTool,
    LLMUsage,
)
from app.services.llm.pricing import cost_for_usage_usd

log = logging.getLogger(__name__)


class VertexConfigError(Exception):
    """Raised when Vertex AI isn't configured (no SDK, no project, etc.)."""


def _get_default_model() -> str:
    return os.getenv("VERTEX_DEFAULT_MODEL", "gemini-1.5-pro-002")


def _get_default_fast_model() -> str:
    return os.getenv("VERTEX_DEFAULT_FAST_MODEL", "gemini-1.5-flash-002")


class VertexGeminiProvider:
    name = "vertex_gemini"

    def __init__(self):
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Initialize vertexai exactly once per process."""
        if self._initialized:
            return
        try:
            import vertexai  # type: ignore
        except ImportError as e:
            raise VertexConfigError(
                f"google-cloud-aiplatform not installed: {e}. "
                "Add it to requirements.lock and rebuild."
            )

        project = os.getenv("VERTEX_PROJECT", "aurora-lts-prod")
        location = os.getenv("VERTEX_LOCATION", "me-west1")

        vertexai.init(project=project, location=location)
        self._initialized = True
        log.info(
            "[vertex_provider] initialized project=%s location=%s",
            project, location,
        )

    def _get_model(self, model_name: Optional[str] = None):
        self._ensure_initialized()
        from vertexai.generative_models import GenerativeModel  # type: ignore

        name = model_name or _get_default_model()
        return GenerativeModel(name)

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        tools: Optional[list[LLMTool]] = None,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Stream a Gemini chat turn; emit canonical events.

        Adapts:
          • Canonical {role, content[]} → Gemini Content(role, parts=[Part])
          • Canonical LLMTool → Gemini FunctionDeclaration (best-effort
            from the JSON-schema input_schema)
        """
        try:
            self._ensure_initialized()
            from vertexai.generative_models import (  # type: ignore
                Content,
                Part,
                FunctionDeclaration,
                Tool,
            )
        except VertexConfigError as e:
            yield LLMStreamEvent(type="error", error_message=str(e))
            return
        except ImportError as e:
            yield LLMStreamEvent(
                type="error",
                error_message=f"vertexai sub-imports failed: {e}",
            )
            return

        use_model_name = model or _get_default_model()
        gen_model = self._get_model(use_model_name)

        # Convert canonical messages to Gemini Content format.
        gemini_messages = []
        for m in messages:
            # Gemini uses role="user" | "model"; map "assistant" → "model"
            g_role = "model" if m.role == "assistant" else "user"
            parts: list = []
            for block in m.content:
                btype = block.get("type")
                if btype == "text":
                    parts.append(Part.from_text(block.get("text") or ""))
                elif btype == "tool_use":
                    # Gemini's "function_call" part shape
                    # Note: outgoing tool_use is rare for a "user" turn;
                    # we forward verbatim text representation as a fallback.
                    parts.append(
                        Part.from_text(
                            f"[tool_use {block.get('name')}: "
                            f"{json.dumps(block.get('input') or {})}]"
                        )
                    )
                elif btype == "tool_result":
                    parts.append(
                        Part.from_text(
                            f"[tool_result] {json.dumps(block.get('content'))}"
                        )
                    )
                else:
                    parts.append(Part.from_text(json.dumps(block)))

            if parts:
                gemini_messages.append(Content(role=g_role, parts=parts))

        # Tools — best-effort adaptation
        gemini_tools = None
        if tools:
            try:
                fns = [
                    FunctionDeclaration(
                        name=t.name,
                        description=t.description,
                        parameters=t.input_schema,
                    )
                    for t in tools
                ]
                gemini_tools = [Tool(function_declarations=fns)]
            except Exception as e:
                log.warning("[vertex_provider] tool adaptation failed: %s", e)

        gen_config = {"max_output_tokens": max_tokens}
        if system_prompt:
            # Vertex supports system_instruction on GenerativeModel constructor;
            # we re-construct for this single call to inject it. (Cheap; the
            # model object is just a thin wrapper around the SDK.)
            from vertexai.generative_models import GenerativeModel  # type: ignore
            gen_model = GenerativeModel(
                use_model_name, system_instruction=system_prompt,
            )

        usage = LLMUsage(provider="vertex_gemini", model=use_model_name)

        try:
            # Vertex SDK async streaming
            stream = await gen_model.generate_content_async(
                gemini_messages,
                tools=gemini_tools,
                generation_config=gen_config,
                stream=True,
            )

            async for chunk in stream:
                # Each chunk has .candidates[0].content.parts and .usage_metadata.
                cands = getattr(chunk, "candidates", None) or []
                if cands:
                    cand_content = getattr(cands[0], "content", None)
                    if cand_content is not None:
                        for part in getattr(cand_content, "parts", []) or []:
                            text = getattr(part, "text", None)
                            if text:
                                yield LLMStreamEvent(
                                    type="text_delta", text=text,
                                )
                            fc = getattr(part, "function_call", None)
                            if fc is not None and getattr(fc, "name", None):
                                # Convert Gemini args (proto Struct) to dict
                                try:
                                    args = dict(fc.args) if fc.args else {}
                                except Exception:
                                    args = {}
                                yield LLMStreamEvent(
                                    type="tool_use",
                                    tool_use={
                                        "id": f"vertex_fc_{int(time.time()*1000)}",
                                        "name": fc.name,
                                        "input": args,
                                    },
                                )

                # Usage metadata is populated in the FINAL chunk
                u = getattr(chunk, "usage_metadata", None)
                if u is not None:
                    usage.tokens_input = int(
                        getattr(u, "prompt_token_count", 0) or 0
                    )
                    usage.tokens_output = int(
                        getattr(u, "candidates_token_count", 0) or 0
                    )

        except Exception as e:
            yield LLMStreamEvent(
                type="error",
                error_message=f"{type(e).__name__}: {str(e)[:300]}",
            )
            return

        usage.cost_usd = cost_for_usage_usd(
            model=use_model_name,
            tokens_input=usage.tokens_input,
            tokens_output=usage.tokens_output,
        )
        yield LLMStreamEvent(type="message_stop", usage=usage)
        yield LLMStreamEvent(type="_final", usage=usage)

    async def one_shot(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 2048,
        response_json_schema: Optional[dict] = None,
    ) -> tuple[str, LLMUsage]:
        """Single-turn convenience for receipts / template draft / DSAR / brief.

        When `response_json_schema` is provided, uses Vertex's
        controlled-generation mode (`response_mime_type=application/json`
        + `response_schema=<schema>`) so the model is constrained to emit
        valid JSON matching the shape. Caller post-validates via Pydantic.
        """
        try:
            self._ensure_initialized()
        except VertexConfigError as e:
            raise

        use_model_name = model or _get_default_model()
        gen_model = self._get_model(use_model_name)

        gen_config: dict = {"max_output_tokens": max_tokens}
        if response_json_schema is not None:
            gen_config["response_mime_type"] = "application/json"
            gen_config["response_schema"] = response_json_schema

        try:
            resp = await gen_model.generate_content_async(
                prompt,
                generation_config=gen_config,
            )
        except Exception as e:
            raise RuntimeError(
                f"vertex generate_content_async failed: "
                f"{type(e).__name__}: {str(e)[:300]}"
            )

        # Extract response text. resp.text is the convenience accessor;
        # falls back to concatenating parts if it raises.
        try:
            text = resp.text or ""
        except Exception:
            text_parts = []
            for cand in getattr(resp, "candidates", []) or []:
                content = getattr(cand, "content", None)
                if content is None:
                    continue
                for part in getattr(content, "parts", []) or []:
                    pt = getattr(part, "text", None)
                    if pt:
                        text_parts.append(pt)
            text = "".join(text_parts)

        u = getattr(resp, "usage_metadata", None)
        usage = LLMUsage(
            provider="vertex_gemini",
            model=use_model_name,
            tokens_input=int(getattr(u, "prompt_token_count", 0) or 0)
            if u else 0,
            tokens_output=int(getattr(u, "candidates_token_count", 0) or 0)
            if u else 0,
        )
        usage.cost_usd = cost_for_usage_usd(
            model=use_model_name,
            tokens_input=usage.tokens_input,
            tokens_output=usage.tokens_output,
        )
        return text, usage
