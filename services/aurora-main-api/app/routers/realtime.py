"""
Aurora LTS — Real-Time SSE Stream Router  (P2-25)

Endpoints
─────────
  GET /api/v1/realtime/stream
      Server-Sent Events stream for the authenticated user.
      Used by AuroraMacShell and accountant-portal for background sync.

  POST /api/v1/realtime/register-push-token
      Store an APNs device token for the current session.
      Called by AuroraMacShell and accountant-portal on startup.

  DELETE /api/v1/realtime/push-token
      Remove the APNs token for the current device (on logout).

  POST /api/v1/realtime/test-push
      Admin-only: send a test push notification.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aurora_shared.database import get_db
from aurora_shared.middleware.auth_middleware import get_current_user, require_admin
from app.services.realtime.push_notifications import sse_stream, send_push, EVENTS

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/realtime", tags=["realtime"])


# ─────────────────────────────────────────────────────────────
# SSE stream
# ─────────────────────────────────────────────────────────────

@router.get("/stream", summary="Server-Sent Events stream for background sync")
async def event_stream(
    request: Request,
    current_user=Depends(get_current_user),
) -> StreamingResponse:
    """
    Long-lived SSE connection. The client receives:
      - "aurora-update" events for invoices, payments, anomalies, etc.
      - A ": heartbeat" comment every 30 seconds to keep the connection alive.

    Clients should reconnect on disconnect (EventSource does this automatically).
    """
    user_id = current_user.id

    async def _stream():
        async for chunk in sse_stream(user_id):
            # Check if client disconnected
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",     # disable nginx buffering
            "Connection": "keep-alive",
        },
    )


# ─────────────────────────────────────────────────────────────
# APNs token registration
# ─────────────────────────────────────────────────────────────

class RegisterPushTokenRequest(BaseModel):
    device_token: str
    platform: str = "macos"    # "macos" | "ipados" | "ios"
    device_id: Optional[str] = None


@router.post("/register-push-token", summary="Register APNs device token")
async def register_push_token(
    req: RegisterPushTokenRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    """
    Store the APNs device token against the current user's device binding.
    The token is used by the push notification service when background
    events are published.
    """
    from aurora_shared.database.models import NativeDeviceKey, AccountantDevice

    token = req.device_token.strip()
    if len(token) < 32:
        raise HTTPException(400, "Invalid APNs device token (too short)")

    # Try NativeDeviceKey first (macOS shell)
    device = (
        db.query(NativeDeviceKey)
        .filter_by(user_id=current_user.id, is_revoked=False)
        .order_by(NativeDeviceKey.last_used_at.desc())
        .first()
    )
    if device:
        device.apns_device_token = token[:200]
        db.commit()
        return {"ok": True, "registered_for": "native_shell"}

    # Try AccountantDevice (Tauri portal)
    acct_device = (
        db.query(AccountantDevice)
        .filter_by(user_id=current_user.id, is_revoked=False)
        .order_by(AccountantDevice.last_used_at.desc())
        .first()
    )
    if acct_device:
        acct_device.apns_device_token = token[:200]
        db.commit()
        return {"ok": True, "registered_for": "accountant_portal"}

    raise HTTPException(404, "No active device binding found for this user")


@router.delete("/push-token", summary="Remove APNs device token on logout")
async def deregister_push_token(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    from aurora_shared.database.models import NativeDeviceKey, AccountantDevice

    cleared = 0
    for row in db.query(NativeDeviceKey).filter_by(user_id=current_user.id).all():
        if getattr(row, "apns_device_token", None):
            row.apns_device_token = None
            cleared += 1
    for row in db.query(AccountantDevice).filter_by(user_id=current_user.id).all():
        if getattr(row, "apns_device_token", None):
            row.apns_device_token = None
            cleared += 1
    db.commit()
    return {"ok": True, "cleared": cleared}


# ─────────────────────────────────────────────────────────────
# Test push (admin only)
# ─────────────────────────────────────────────────────────────

class TestPushRequest(BaseModel):
    device_token: str
    event_key: str = "invoice_paid"


@router.post("/test-push", summary="Admin: send test push notification")
async def test_push(
    req: TestPushRequest,
    _admin=Depends(require_admin),
) -> dict:
    event = EVENTS.get(req.event_key)
    if not event:
        raise HTTPException(400, f"Unknown event key: {req.event_key}. Valid: {list(EVENTS)}")
    ok = send_push(req.device_token, event)
    return {"ok": ok, "event_key": req.event_key, "device_token": req.device_token[:8] + "…"}
