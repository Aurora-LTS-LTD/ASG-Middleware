"""
Aurora LTS — Real-Time Push Notifications Service  (P2-25)
============================================================

Manages APNs (Apple Push Notification Service) push notifications
and in-app SSE (Server-Sent Events) delivery for:

  • New invoice created / finalised
  • Payment received
  • Document uploaded to vault
  • Anomaly detected (high/critical severity)
  • VAT return due within 7 days
  • Sanctions screening hit (medium+)

TWO DELIVERY CHANNELS
──────────────────────
  1. SSE (in-app background sync) — for the macOS shell and Tauri portal
     while the app is open. Reuses the existing exec_events ring buffer.
     Client connects to GET /api/v1/realtime/stream and receives events.

  2. APNs (background push) — for macOS shell + iPadOS when the app is
     closed. Sends a background notification that wakes the app to fetch
     fresh data. No user-visible alert for most events (silent push).

APNs CONFIGURATION
──────────────────
  APNS_BACKEND=stub           Log-only; no network calls
  APNS_BACKEND=production     Real APNs HTTP/2 calls via JWT auth

  APNS_TEAM_ID                Apple Team ID (10-char)
  APNS_KEY_ID                 APNs auth key ID (10-char)
  APNS_PRIVATE_KEY_SECRET     Secret Manager name for the .p8 private key
  APNS_BUNDLE_ID              Bundle ID (default: com.api-aurora-lts.AuroraMacShell.AuroraMacShell)
  APNS_PRODUCTION             "1" for production APNs; "0" or absent for sandbox

SSE STREAM
──────────
  GET /api/v1/realtime/stream?token=<jwt>
    → text/event-stream
    → heartbeat every 30s (empty : comment)
    → event: aurora-update\ndata: {...}\n\n on new activity
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import time
from typing import AsyncGenerator, Optional, Dict, List
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# APNs configuration
# ─────────────────────────────────────────────────────────────

def _apns_backend() -> str:
    return (os.getenv("APNS_BACKEND") or "stub").strip().lower()


def _apns_bundle_id() -> str:
    return os.getenv("APNS_BUNDLE_ID", "com.api-aurora-lts.AuroraMacShell.AuroraMacShell")


def _apns_host() -> str:
    prod = os.getenv("APNS_PRODUCTION", "0") == "1"
    return "api.push.apple.com" if prod else "api.sandbox.push.apple.com"


# ─────────────────────────────────────────────────────────────
# Event types
# ─────────────────────────────────────────────────────────────

@dataclass
class PushEvent:
    event_type: str          # "invoice_paid" | "anomaly_detected" | etc.
    title: str               # Hebrew notification title
    body: str                # Hebrew notification body
    badge: Optional[int] = None
    data: Dict = field(default_factory=dict)
    target_device_tokens: List[str] = field(default_factory=list)
    target_user_ids: List[int] = field(default_factory=list)
    sound: str = "default"
    content_available: bool = True   # silent background refresh
    alert_push: bool = False         # True = show a visible alert


# Predefined events for common triggers
EVENTS = {
    "invoice_paid": PushEvent(
        event_type="invoice_paid",
        title="✅ תשלום התקבל",
        body="חשבונית שולמה",
        alert_push=True,
    ),
    "invoice_finalized": PushEvent(
        event_type="invoice_finalized",
        title="📄 חשבונית אושרה",
        body="חשבונית קיבלה מספר הקצאה מרשות המסים",
        content_available=True,
    ),
    "document_uploaded": PushEvent(
        event_type="document_uploaded",
        title="📁 מסמך חדש בכספת",
        body="לקוח העלה מסמך חדש",
        content_available=True,
    ),
    "anomaly_high": PushEvent(
        event_type="anomaly_high",
        title="⚠️ חריגה פיננסית זוהתה",
        body="פעולה חריגה בחשבון — נדרשת בדיקה",
        alert_push=True,
    ),
    "vat_due_reminder": PushEvent(
        event_type="vat_due_reminder",
        title="📅 הגשת מע\"מ בקרוב",
        body="מועד הגשת הדו\"ח ב-7 ימים",
        alert_push=True,
    ),
    "sanctions_hit": PushEvent(
        event_type="sanctions_hit",
        title="🚨 התראת סנקציות",
        body="התאמה ברשימת סנקציות — נדרסת סקירה",
        alert_push=True,
    ),
}


# ─────────────────────────────────────────────────────────────
# In-memory SSE event bus (per-process ring buffer)
# ─────────────────────────────────────────────────────────────

class _EventBus:
    """
    Simple in-memory pub/sub for SSE delivery.
    On Cloud Run with multiple instances, this is per-instance.
    For cross-instance delivery, publish via ActionLog + poll, or
    use Pub/Sub as the cross-instance bus (future enhancement).
    """

    def __init__(self, max_queued: int = 50) -> None:
        self._subscribers: Dict[int, asyncio.Queue] = {}  # user_id → Queue
        self._max_queued = max_queued

    def subscribe(self, user_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queued)
        self._subscribers[user_id] = q
        return q

    def unsubscribe(self, user_id: int) -> None:
        self._subscribers.pop(user_id, None)

    def publish(self, event: PushEvent, user_id: Optional[int] = None) -> None:
        frame = {
            "event": event.event_type,
            "title": event.title,
            "body": event.body,
            "ts": datetime.datetime.utcnow().isoformat(),
            "data": event.data,
        }
        if user_id is not None:
            q = self._subscribers.get(user_id)
            if q:
                try:
                    q.put_nowait(frame)
                except asyncio.QueueFull:
                    pass  # client too slow — drop oldest via a future improvement
        else:
            for q in self._subscribers.values():
                try:
                    q.put_nowait(frame)
                except asyncio.QueueFull:
                    pass

    def broadcast(self, event: PushEvent) -> None:
        self.publish(event, user_id=None)


_bus = _EventBus()


def get_event_bus() -> _EventBus:
    return _bus


# ─────────────────────────────────────────────────────────────
# SSE stream generator
# ─────────────────────────────────────────────────────────────

async def sse_stream(user_id: int) -> AsyncGenerator[str, None]:
    """
    Yields SSE frames for the given user until the client disconnects.
    Used by GET /api/v1/realtime/stream.
    """
    bus = get_event_bus()
    queue = bus.subscribe(user_id)
    heartbeat_interval = 30  # seconds

    try:
        last_heartbeat = time.monotonic()
        while True:
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield f"event: aurora-update\ndata: {json.dumps(frame, ensure_ascii=False)}\n\n"
                last_heartbeat = time.monotonic()
            except asyncio.TimeoutError:
                # Send heartbeat if idle
                if time.monotonic() - last_heartbeat >= heartbeat_interval:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.monotonic()
    finally:
        bus.unsubscribe(user_id)


# ─────────────────────────────────────────────────────────────
# APNs push delivery
# ─────────────────────────────────────────────────────────────

def send_push(
    device_token: str,
    event: PushEvent,
) -> bool:
    """
    Send a push notification to a single APNs device token.
    Returns True on success.
    """
    backend = _apns_backend()

    if backend == "stub":
        log.info(
            "[APNs stub] token=%s event=%s alert=%s title=%r",
            device_token[:8] + "…",
            event.event_type,
            event.alert_push,
            event.title,
        )
        return True

    if backend == "production":
        return _apns_http2_send(device_token, event)

    log.warning("Unknown APNS_BACKEND=%r", backend)
    return False


def send_push_to_users(event_key: str, user_ids: List[int], data: dict = None) -> dict:
    """
    Look up device tokens for user_ids from DB and send push.
    Also broadcasts to in-memory SSE bus.
    Returns {sent: N, failed: N}.
    """
    from app.database.connection import SessionLocal
    from app.database.models import NativeDeviceKey, AccountantDevice

    event = EVENTS.get(event_key)
    if not event:
        log.warning("Unknown push event key: %s", event_key)
        return {"sent": 0, "failed": 0, "reason": "unknown_event_key"}

    if data:
        event.data = data

    # Broadcast to SSE bus
    for uid in user_ids:
        _bus.publish(event, user_id=uid)

    # Collect APNs tokens from DB
    sent = failed = 0
    with SessionLocal() as db:
        for user_id in user_ids:
            # macOS shell device keys
            shell_keys = (
                db.query(NativeDeviceKey)
                .filter_by(user_id=user_id, is_revoked=False)
                .all()
            )
            for key in shell_keys:
                apns_token = getattr(key, "apns_device_token", None)
                if apns_token:
                    ok = send_push(apns_token, event)
                    if ok: sent += 1
                    else:  failed += 1

            # Accountant portal devices
            portal_devices = (
                db.query(AccountantDevice)
                .filter_by(user_id=user_id, is_revoked=False)
                .all()
            )
            for dev in portal_devices:
                apns_token = getattr(dev, "apns_device_token", None)
                if apns_token:
                    ok = send_push(apns_token, event)
                    if ok: sent += 1
                    else:  failed += 1

    return {"sent": sent, "failed": failed}


# ─────────────────────────────────────────────────────────────
# APNs HTTP/2 implementation
# ─────────────────────────────────────────────────────────────

def _apns_http2_send(device_token: str, event: PushEvent) -> bool:
    """
    Send via APNs HTTP/2 API using JWT-signed auth.
    Requires httpx with HTTP/2 support: pip install httpx[http2]
    """
    try:
        import httpx
        import jwt as pyjwt  # PyJWT
    except ImportError:
        log.warning("httpx[http2] or PyJWT not installed — cannot send APNs")
        return False

    from app.config.secrets import optional_secret

    team_id = os.getenv("APNS_TEAM_ID", "")
    key_id = os.getenv("APNS_KEY_ID", "")
    private_key_pem = optional_secret("APNS_PRIVATE_KEY_SECRET") or ""

    if not all([team_id, key_id, private_key_pem]):
        log.warning("APNs credentials incomplete — stub fallback")
        return False

    # Build JWT auth token (valid 60 min; rotate if needed)
    now = int(time.time())
    token_payload = {"iss": team_id, "iat": now}
    auth_token = pyjwt.encode(
        token_payload, private_key_pem, algorithm="ES256",
        headers={"kid": key_id},
    )

    # Build APNs payload
    aps: dict = {"content-available": 1}
    if event.alert_push:
        aps["alert"] = {"title": event.title, "body": event.body}
        aps["sound"] = event.sound
    if event.badge is not None:
        aps["badge"] = event.badge

    apns_payload = {"aps": aps, "aurora": event.data}

    host = _apns_host()
    url = f"https://{host}/3/device/{device_token}"
    headers = {
        "authorization": f"bearer {auth_token}",
        "apns-topic": _apns_bundle_id(),
        "apns-push-type": "alert" if event.alert_push else "background",
        "apns-priority": "10" if event.alert_push else "5",
    }

    try:
        with httpx.Client(http2=True, timeout=10) as client:
            resp = client.post(url, json=apns_payload, headers=headers)
        if resp.status_code == 200:
            return True
        log.warning("APNs rejected push: status=%d body=%s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.warning("APNs HTTP/2 request failed: %s", e)
        return False
