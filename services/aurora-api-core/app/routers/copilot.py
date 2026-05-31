"""
Aurora LTS — AI Copilot Router  (M2 Core)
==========================================
Extracted verbatim from admin_exec.py as part of the operational-core split
(feature/operational-core-split). Owns the Claude/Gemini Copilot console:

  POST   /api/v1/admin/exec/copilot/conversations          create
  GET    /api/v1/admin/exec/copilot/conversations          list
  GET    /api/v1/admin/exec/copilot/conversations/{id}     full thread
  POST   /api/v1/admin/exec/copilot/chat                   SSE chat turn
  POST   /api/v1/admin/exec/copilot/approve                execute write tool (step-up)
  GET    /api/v1/admin/exec/copilot/usage                  spend/limit snapshot
  POST   /api/v1/admin/exec/copilot/budget-extend          extend daily $-budget

Mounted on aurora-api-core (app.main_core). The URL prefix is preserved
exactly (/api/v1/admin/exec/copilot/*) so existing clients and the cockpit
M2 panel are unaffected. Shares the Aurora schema via aurora_shared.database — no
schema drift. Write-tool approval still defers to aurora_shared.services.webauthn_service
for step-up verification (shared DB), so step-up works across both engines.
"""

from __future__ import annotations

import datetime
import json as _json
import logging
import os as _os
from collections import defaultdict, deque
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from aurora_shared.database import (
    get_db,
    User,
    CopilotConversation,
    CopilotMessage,
    CopilotProvisioningRun,
    ClaudeApiUsage,
)
from aurora_shared.middleware.auth_middleware import require_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/exec", tags=["admin-copilot"])

class CopilotConversationCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)


class CopilotChatTurnRequest(BaseModel):
    conversation_id: int
    user_text: str = Field(min_length=1, max_length=8000)


class CopilotApproveRequest(BaseModel):
    conversation_id: int
    tool_use_id: str = Field(min_length=1, max_length=120)
    # Step-up token from /webauthn/assert/finish. Sprint 3 ships the
    # endpoint stub; full WebAuthn verification lands in T3.3.
    step_up_token: Optional[str] = None


# ── In-process rate limiter (per user) ──
# Single-CEO scale; not horizontally consistent across Cloud Run
# instances, but acceptable for Tier 1 with min-instances=1.
_RATE_LIMIT_WINDOW_S = 3600
_TURNS_PER_HOUR = int(_os.getenv("AURORA_COPILOT_MAX_TURNS_PER_HOUR", "30"))
_turn_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=_TURNS_PER_HOUR * 2))


def _check_rate_limit(user_id: int) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds)."""
    import time as _t
    now = _t.monotonic()
    dq = _turn_history[user_id]
    # Drop turns outside the window
    while dq and now - dq[0] > _RATE_LIMIT_WINDOW_S:
        dq.popleft()
    if len(dq) >= _TURNS_PER_HOUR:
        oldest = dq[0]
        retry_after = int(_RATE_LIMIT_WINDOW_S - (now - oldest)) + 1
        return False, max(retry_after, 1)
    dq.append(now)
    return True, 0


# ─────────────────────────────────────────────────────────────
# Conversations CRUD
# ─────────────────────────────────────────────────────────────

@router.post("/copilot/conversations", status_code=201)
def copilot_create_conversation(
    body: CopilotConversationCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    conv = CopilotConversation(
        user_id=current_user.id,
        title=body.title,
        status="active",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {
        "id": conv.id,
        "user_id": conv.user_id,
        "title": conv.title,
        "status": conv.status,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
    }


@router.get("/copilot/conversations")
def copilot_list_conversations(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    q = (
        db.query(CopilotConversation)
        .filter(CopilotConversation.user_id == current_user.id)
        .order_by(CopilotConversation.id.desc())
    )
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "conversations": [
            {
                "id": r.id,
                "title": r.title,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


@router.get("/copilot/conversations/{conversation_id}")
def copilot_get_conversation(
    conversation_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    conv = (
        db.query(CopilotConversation)
        .filter(
            CopilotConversation.id == conversation_id,
            CopilotConversation.user_id == current_user.id,
        )
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg_rows = (
        db.query(CopilotMessage)
        .filter(CopilotMessage.conversation_id == conv.id)
        .order_by(CopilotMessage.id.asc())
        .all()
    )

    messages: list[dict] = []
    for m in msg_rows:
        try:
            content = _json.loads(m.content_json)
        except Exception:
            content = [{"type": "text", "text": m.content_json or ""}]
        messages.append({
            "id": m.id,
            "role": m.role,
            "content": content,
            "model": m.model,
            "stop_reason": m.stop_reason,
            "tokens_input": m.tokens_input,
            "tokens_output": m.tokens_output,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })

    return {
        "id": conv.id,
        "title": conv.title,
        "status": conv.status,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "messages": messages,
    }


# ─────────────────────────────────────────────────────────────
# Chat — SSE streaming endpoint
# ─────────────────────────────────────────────────────────────

@router.post("/copilot/chat")
async def copilot_chat(
    body: CopilotChatTurnRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Stream a Claude turn over SSE.

    Flow:
      1. Append user message to copilot_messages
      2. Build Anthropic-format messages array from full DB history
      3. Stream tokens via anthropic_client.stream_chat
      4. Forward each event to the client as `data: {...}\n\n` SSE frames
      5. After _aurora_final, persist the assistant message + usage row
      6. If assistant emitted a READ tool_use (search_existing_categories),
         execute it inline, append tool_result to messages, and stream a
         SECOND Claude turn so the model can respond with reasoning.
         Write tool_uses (propose_provisioning_blueprint etc.) are NOT
         executed — they surface as Pending Approval cards in the UI.
    """
    # Guardrails (Appendix K §1C — DB-backed rate limit + daily budget cap).
    # Structured error contract (so Playwright + UI can render rich states):
    #   429 → {error:"copilot_rate_limited", retry_after_seconds, resets_at,
    #          current_turns, limit}
    #   402 → {error:"daily_budget_exceeded", retry_after_seconds, resets_at,
    #          spent_usd, limit_usd}
    # Both responses also set the standard Retry-After header in seconds.
    try:
        from app.services.copilot.guardrails import (
            check_chat_guardrails,
            RateLimited,
            BudgetExceeded,
            _next_utc_midnight_iso,
        )
        usage_state = check_chat_guardrails(current_user.id, db)
    except RateLimited as e:
        # 429 — per-hour turn cap. resets_at is the next hour boundary in UTC.
        now = datetime.datetime.utcnow()
        next_hour = (
            now + datetime.timedelta(seconds=e.retry_after_s)
        ).replace(microsecond=0)
        raise HTTPException(
            status_code=429,
            detail={
                "error": "copilot_rate_limited",
                "message": (
                    f"Copilot rate limit ({e.limit} turns/hour). "
                    f"Retry in {e.retry_after_s}s."
                ),
                "retry_after_seconds": int(e.retry_after_s),
                "resets_at": next_hour.isoformat() + "Z",
                "current_turns": int(e.turns),
                "limit": int(e.limit),
            },
            headers={"Retry-After": str(int(e.retry_after_s))},
        )
    except BudgetExceeded as e:
        # 402 — daily $-cap. resets_at is next UTC midnight (already on `e`).
        # retry_after_seconds derived from now → midnight for client UX.
        try:
            resets_dt = datetime.datetime.fromisoformat(
                e.resets_at_iso.replace("Z", "+00:00")
            )
            retry_after = max(
                1,
                int(
                    (
                        resets_dt
                        - datetime.datetime.utcnow().replace(
                            tzinfo=resets_dt.tzinfo
                        )
                    ).total_seconds()
                ),
            )
        except Exception:
            retry_after = 3600  # safe fallback: 1h
        raise HTTPException(
            status_code=402,
            detail={
                "error": "daily_budget_exceeded",
                "message": (
                    f"Daily Copilot budget exceeded "
                    f"(${e.spent_usd:.2f} of ${e.limit_usd:.2f}). "
                    f"Resets at {e.resets_at_iso}."
                ),
                "retry_after_seconds": retry_after,
                "resets_at": e.resets_at_iso,
                "spent_usd": float(e.spent_usd),
                "limit_usd": float(e.limit_usd),
            },
            headers={"Retry-After": str(retry_after)},
        )

    conv = (
        db.query(CopilotConversation)
        .filter(
            CopilotConversation.id == body.conversation_id,
            CopilotConversation.user_id == current_user.id,
        )
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Persist the user message immediately so a network drop preserves intent.
    user_content = [{"type": "text", "text": body.user_text}]
    user_msg = CopilotMessage(
        conversation_id=conv.id,
        role="user",
        content_json=_json.dumps(user_content, ensure_ascii=False),
        model=None,
        stop_reason=None,
    )
    db.add(user_msg)

    # Auto-title the conversation on first turn
    if not conv.title:
        conv.title = body.user_text[:120].strip()

    conv.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(user_msg)

    # Build Anthropic-format messages history.
    all_msgs = (
        db.query(CopilotMessage)
        .filter(CopilotMessage.conversation_id == conv.id)
        .order_by(CopilotMessage.id.asc())
        .all()
    )
    anthropic_messages: list[dict] = []
    for m in all_msgs:
        try:
            content = _json.loads(m.content_json)
        except Exception:
            content = [{"type": "text", "text": m.content_json or ""}]
        # tool_result rows are stored as role='tool_result' for our query
        # ergonomics; Anthropic expects role='user' with a tool_result
        # content block. Normalize on the way out.
        role = "user" if m.role in ("user", "tool_result") else m.role
        anthropic_messages.append({"role": role, "content": content})

    # Capture conv id outside the generator closure for clarity
    conv_id = conv.id
    user_id = current_user.id

    async def event_stream():
        """Async generator that yields SSE frames."""
        from app.services.copilot.anthropic_client import (
            stream_chat,
            messages_jsonsafe_dump,
            extract_pending_tool_uses,
        )
        from app.services.copilot.executor import execute_search_categories
        from aurora_shared.database.connection import SessionLocal as _SL

        def _frame(payload: dict, event: str | None = None) -> str:
            lines = []
            if event:
                lines.append(f"event: {event}")
            lines.append(f"data: {_json.dumps(payload, default=str, ensure_ascii=False)}")
            return "\n".join(lines) + "\n\n"

        # Initial hello + heartbeat
        yield _frame({"ok": True, "conversation_id": conv_id}, event="hello")

        # We may do up to 3 Claude rounds (initial turn + read-tool follow-ups)
        round_messages = list(anthropic_messages)
        for round_idx in range(3):
            assembled_msg = None
            assembled_usage = {}

            async for ev in stream_chat(round_messages):
                etype = ev.get("type")
                if etype == "_aurora_final":
                    assembled_msg = ev.get("message")
                    assembled_usage = ev.get("usage") or {}
                    continue
                if etype == "error":
                    yield _frame(ev, event="error")
                    return
                yield _frame(ev, event="anthropic")

            if not assembled_msg:
                yield _frame({"error": "no_assembled_message"}, event="error")
                return

            # Persist assistant message + usage in a short-lived session
            with _SL() as wdb:
                a_msg = CopilotMessage(
                    conversation_id=conv_id,
                    role="assistant",
                    content_json=messages_jsonsafe_dump(assembled_msg.get("content") or []),
                    model=assembled_msg.get("model"),
                    stop_reason=assembled_msg.get("stop_reason"),
                    tokens_input=assembled_usage.get("input_tokens"),
                    tokens_output=assembled_usage.get("output_tokens"),
                    tokens_cache_creation=assembled_usage.get("cache_creation_input_tokens"),
                    tokens_cache_read=assembled_usage.get("cache_read_input_tokens"),
                )
                wdb.add(a_msg)
                wdb.add(ClaudeApiUsage(
                    user_id=user_id,
                    conversation_id=conv_id,
                    model=assembled_msg.get("model") or "unknown",
                    tokens_input=int(assembled_usage.get("input_tokens") or 0),
                    tokens_output=int(assembled_usage.get("output_tokens") or 0),
                    tokens_cache_creation=int(assembled_usage.get("cache_creation_input_tokens") or 0),
                    tokens_cache_read=int(assembled_usage.get("cache_read_input_tokens") or 0),
                ))
                # Bump conversation updated_at
                cnv = wdb.query(CopilotConversation).filter(CopilotConversation.id == conv_id).first()
                if cnv:
                    cnv.updated_at = datetime.datetime.utcnow()
                wdb.commit()
                wdb.refresh(a_msg)
                persisted_assistant_id = a_msg.id

            # Append the assistant turn to the running history
            round_messages.append({
                "role": "assistant",
                "content": assembled_msg.get("content") or [],
            })

            # Inspect for tool_use blocks
            content_blocks = assembled_msg.get("content") or []
            read_tool_results: list[dict] = []
            pending_writes = extract_pending_tool_uses(content_blocks)

            for block in content_blocks:
                if block.get("type") != "tool_use":
                    continue
                tname = block.get("name")
                if tname == "search_existing_categories":
                    # Execute inline
                    with _SL() as rdb:
                        result = execute_search_categories(block.get("input") or {}, rdb)
                    read_tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.get("id"),
                        "content": _json.dumps(result, ensure_ascii=False),
                    })

            if pending_writes:
                # Surface pending approval cards to the client
                yield _frame(
                    {
                        "pending_writes": pending_writes,
                        "assistant_message_id": persisted_assistant_id,
                    },
                    event="pending_writes",
                )

            if read_tool_results:
                # Persist a tool_result message + continue the loop
                with _SL() as wdb:
                    tr_msg = CopilotMessage(
                        conversation_id=conv_id,
                        role="tool_result",
                        content_json=_json.dumps(read_tool_results, ensure_ascii=False),
                        model=None,
                        stop_reason=None,
                    )
                    wdb.add(tr_msg)
                    wdb.commit()
                round_messages.append({
                    "role": "user",
                    "content": read_tool_results,
                })
                yield _frame(
                    {"resuming": True, "tool_results": read_tool_results},
                    event="resume",
                )
                # Loop again for Claude's next turn
                continue

            # No read tools fired → done streaming this turn
            yield _frame({"reason": "end_of_turn"}, event="bye")
            return

        # Safety: hit the round cap
        yield _frame({"reason": "max_rounds"}, event="bye")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────────────────────
# Approve & Build — execute an approved WRITE tool_use
# ─────────────────────────────────────────────────────────────

@router.post("/copilot/approve")
def copilot_approve(
    body: CopilotApproveRequest,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """
    Execute a WRITE tool_use that was previously surfaced as a Pending
    Approval card. Requires WebAuthn step-up (T3.3 — verified by the
    `require_step_up` dep once shipped). For Sprint 3 day-1 deploy,
    AURORA_EXEC_REQUIRE_STEP_UP=0 short-circuits step-up so we can
    smoke-test the executor end-to-end before the WebAuthn UI ships.
    """
    from app.services.copilot.executor import execute_approved_tool

    require_step_up = (_os.getenv("AURORA_EXEC_REQUIRE_STEP_UP", "0") == "1")
    step_up_credential_id: Optional[int] = None
    if require_step_up:
        # T3.3 — verify body.step_up_token via webauthn_service
        from aurora_shared.services.webauthn_service import (
            verify_step_up_token,
            StepUpVerificationError,
        )
        try:
            step_up_credential_id = verify_step_up_token(
                token=body.step_up_token or "",
                expected_action="copilot_provision",
                user_id=current_user.id,
                db=db,
            )
        except StepUpVerificationError as e:
            raise HTTPException(
                status_code=403,
                detail=f"WebAuthn step-up required: {e}",
            )

    # Look up the conversation + the tool_use block
    conv = (
        db.query(CopilotConversation)
        .filter(
            CopilotConversation.id == body.conversation_id,
            CopilotConversation.user_id == current_user.id,
        )
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Scan all assistant messages in this conversation for the tool_use
    tool_use_block: Optional[dict] = None
    msg_rows = (
        db.query(CopilotMessage)
        .filter(
            CopilotMessage.conversation_id == conv.id,
            CopilotMessage.role == "assistant",
        )
        .order_by(CopilotMessage.id.desc())
        .all()
    )
    for m in msg_rows:
        try:
            content = _json.loads(m.content_json)
        except Exception:
            continue
        for block in content:
            if block.get("type") == "tool_use" and block.get("id") == body.tool_use_id:
                tool_use_block = block
                break
        if tool_use_block:
            break

    if not tool_use_block:
        raise HTTPException(status_code=404, detail="tool_use_id not found in this conversation")

    # Has this tool_use already been executed?
    prev = (
        db.query(CopilotProvisioningRun)
        .filter(
            CopilotProvisioningRun.conversation_id == conv.id,
            CopilotProvisioningRun.tool_use_id == body.tool_use_id,
        )
        .first()
    )
    if prev:
        return {
            "ok": True,
            "already_executed": True,
            "run_id": prev.id,
            "status": prev.status,
            "outcome": _json.loads(prev.outcome_json or "{}"),
        }

    # Execute the tool inside a fresh transaction
    try:
        outcome = execute_approved_tool(
            db=db,
            conversation_id=conv.id,
            tool_use_id=body.tool_use_id,
            tool_name=tool_use_block.get("name") or "",
            tool_input=tool_use_block.get("input") or {},
            actor_user_id=current_user.id,
            step_up_credential_id=step_up_credential_id,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        log.error("[copilot.approve] execution failed: %s", e)
        raise HTTPException(status_code=500, detail=f"executor_error: {str(e)[:240]}")

    # Persist a tool_result message so the next chat turn sees the outcome
    try:
        tr_msg = CopilotMessage(
            conversation_id=conv.id,
            role="tool_result",
            content_json=_json.dumps([{
                "type": "tool_result",
                "tool_use_id": body.tool_use_id,
                "content": _json.dumps(outcome, default=str, ensure_ascii=False),
            }], ensure_ascii=False),
        )
        db.add(tr_msg)
        db.commit()
    except Exception as e:
        log.warning("[copilot.approve] tool_result persist failed (non-fatal): %s", e)

    return {
        "ok": True,
        "tool_use_id": body.tool_use_id,
        "tool_name": tool_use_block.get("name"),
        "outcome": outcome,
    }


class CopilotBudgetExtendBody(BaseModel):
    delta_usd: float = Field(gt=0, le=50)
    # Single extension is capped at $50; for larger, file multiple.
    reason: str = Field(min_length=3, max_length=240)


@router.get("/copilot/usage")
def copilot_usage_snapshot(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Read-only operator snapshot: current spend, limits, rate-counter."""
    from app.services.copilot.guardrails import get_usage_snapshot
    return get_usage_snapshot(current_user.id, db)


@router.post("/copilot/budget-extend")
def copilot_budget_extend(
    body: CopilotBudgetExtendBody,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """
    Admin-only: extend today's Copilot $-budget by `delta_usd`.

    Records a negative-token "credit" row in claude_api_usage so the
    rolling 24h spend calc subtracts the extension from total spend.
    Audit-logged via ExecEvent.

    (Step-up is NOT enforced here in Sprint 3 since the only caller is
    the founder, already IAP-gated. A future tightening could require
    WebAuthn step-up for this endpoint too — wire via
    `require_step_up("copilot_budget_extend")` when ready.)
    """
    from app.services.copilot.guardrails import record_budget_extension
    return record_budget_extension(
        user_id=current_user.id,
        delta_usd=body.delta_usd,
        reason=body.reason,
        db=db,
    )
