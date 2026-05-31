"""
ASG Solutions — WhatsApp Router (v2)
=====================================
HTTP endpoints for the Meta WhatsApp Business Cloud API integration.

ENDPOINTS:
  GET  /webhook/whatsapp/{secret}        — Meta's one-time verification handshake
  POST /webhook/whatsapp/{secret}        — Inbound message hook (HMAC-verified)
  POST /api/v1/whatsapp/pairing-code     — Dashboard: generate a 6-digit code
                                           the user sends to the bot as LINK-XXXXXX
  GET  /api/v1/whatsapp/health           — Liveness probe + last-inbound timestamp

HOW IT FITS TOGETHER:
  Meta POST → this router verifies HMAC + secret → parses payload
           → dedup on wamid → calls whatsapp_engine.handle_inbound()
           → engine talks back via whatsapp_meta_client

SECURITY LAYERS:
  1. The {secret} path segment must match WHATSAPP_WEBHOOK_SECRET
  2. The X-Hub-Signature-256 header must be a valid HMAC-SHA256 of the
     raw request body, keyed by WHATSAPP_APP_SECRET (Meta's app secret)
  3. Inbound is always answered 200 OK fast — never return 4xx/5xx to
     Meta or they'll retry + eventually disable the webhook.
  4. Every inbound message is dedup'd on `wamid` so Meta retries don't
     double-process anything.

REAL-WORLD ANALOGY:
  This is the security checkpoint at the entrance to the office.
  Every letter (webhook) gets its envelope (HMAC) checked, the name
  on the delivery slip (secret) verified, and a ledger stamp (wamid
  dedup) before it's handed off to the back office (engine).
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import datetime
import hashlib
import hmac
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from aurora_shared.database import SessionLocal, User, WhatsAppOutboundLog, get_db
from aurora_shared.middleware.auth_middleware import get_current_user
from app.services import whatsapp_engine
from aurora_shared.services.whatsapp_identity import generate_pairing_code


# ─────────────────────────────────────────────────────────────
# ROUTER
# ─────────────────────────────────────────────────────────────
router = APIRouter(tags=["WhatsApp"])


# ─────────────────────────────────────────────────────────────
# STATE: last inbound timestamp (for /health)
# ─────────────────────────────────────────────────────────────
_last_inbound_at: datetime.datetime | None = None


def _get_last_inbound_at() -> datetime.datetime | None:
    return _last_inbound_at


# ─────────────────────────────────────────────────────────────
# IDEMPOTENCY: in-memory wamid cache
# ─────────────────────────────────────────────────────────────
# We keep a rolling set of the last ~1000 wamid values we've seen.
# If Meta retries a webhook (it will — up to 21h of retries on 5xx),
# we won't double-process the same message. For a single-instance
# dev setup this is sufficient; in production behind a load balancer
# we'd move this to Redis or a DB unique index on wamid.
_seen_wamids: set[str] = set()
_wamid_order: list[str] = []
_WAMID_CACHE_MAX = 1000


def _is_duplicate_wamid(wamid: str) -> bool:
    """True iff we've already processed this wamid in this process."""
    if not wamid:
        return False
    if wamid in _seen_wamids:
        return True
    _seen_wamids.add(wamid)
    _wamid_order.append(wamid)
    # Evict oldest entries once we're past the cap
    while len(_wamid_order) > _WAMID_CACHE_MAX:
        old = _wamid_order.pop(0)
        _seen_wamids.discard(old)
    return False


# ─────────────────────────────────────────────────────────────
# SECURITY: HMAC signature verification
# ─────────────────────────────────────────────────────────────
def _signature_must_enforce() -> bool:
    """
    True when HMAC verification is mandatory.

    Sprint 3 hardening: signature is enforced on Cloud Run regardless
    of whether WHATSAPP_APP_SECRET is set. Missing/placeholder secret
    on Cloud Run → 503-style refusal (signature returns False) so a
    misconfiguration is loud, not silently insecure.

    Local dev (AURORA_RUNTIME != 'cloud_run') keeps the legacy skip
    behaviour for ease of testing — unless WA_REQUIRE_SIGNATURE=1 is
    set explicitly to opt in.
    """
    if (os.getenv("WA_REQUIRE_SIGNATURE") or "").strip() in ("1", "true", "TRUE"):
        return True
    if os.getenv("AURORA_RUNTIME", "").lower() == "cloud_run":
        return True
    return False


def _is_placeholder_secret(value: str) -> bool:
    """Detect the 'YOUR_..._HERE' template strings shipped in the example .env."""
    if not value:
        return True
    upper = value.upper()
    return upper.endswith("_HERE") or "YOUR_META_APP_SECRET" in upper


def _verify_signature(raw_body: bytes, header_sig: str | None) -> bool:
    """
    Verify Meta's X-Hub-Signature-256 header.

    Meta computes: 'sha256=' + hex(HMAC_SHA256(app_secret, raw_body)).

    PRODUCTION (AURORA_RUNTIME=cloud_run or WA_REQUIRE_SIGNATURE=1):
      - Missing secret OR placeholder secret → return False (refuse).
      - Missing/malformed signature header   → return False.
      - Any mismatch (constant-time compare) → return False.

    DEV (default):
      - Missing secret → return True with a warning (faster local iteration).
      - Otherwise verify normally.
    """
    app_secret = os.getenv("WHATSAPP_APP_SECRET", "")

    if _signature_must_enforce():
        if _is_placeholder_secret(app_secret):
            print(
                "[WA_WEBHOOK] ❌ Production HMAC enforcement is ON but "
                "WHATSAPP_APP_SECRET is unset or a placeholder. Refusing."
            )
            return False
    else:
        if not app_secret:
            print("[WA_WEBHOOK] ⚠️ WHATSAPP_APP_SECRET not set — signature skipped (dev only)")
            return True
        if _is_placeholder_secret(app_secret):
            print("[WA_WEBHOOK] ⚠️ WHATSAPP_APP_SECRET is a placeholder — signature skipped (dev only)")
            return True

    if not header_sig or not header_sig.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    given = header_sig.split("=", 1)[1]
    # Constant-time compare to prevent timing attacks
    return hmac.compare_digest(expected, given)


# ─────────────────────────────────────────────────────────────
# PARSE: normalize Meta's deep payload into a flat dict
# ─────────────────────────────────────────────────────────────
def _parse_inbound(body: dict) -> list[dict]:
    """
    Walk Meta's webhook JSON and produce a flat list of parsed events.

    Meta nests: body → entry[] → changes[] → value → messages[] / statuses[].
    A single webhook can batch several messages or status updates.

    Returns a list of event dicts, one per message:
      {
        "wamid": str,
        "from":  str (phone in '+E.164'),
        "type":  "text" | "interactive" | "image" | "document" | ...
        "text":  str | None,
        "button_id": str | None,
        "list_id":   str | None,
        "media_id":  str | None,
      }

    Status updates (delivered/read/failed) are NOT included here — they
    are handled separately by _handle_statuses().
    """
    events: list[dict] = []
    for entry in body.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for msg in value.get("messages", []) or []:
                event = {
                    "wamid": msg.get("id"),
                    "from": _ensure_plus(msg.get("from")),
                    "type": msg.get("type"),
                    "text": None,
                    "button_id": None,
                    "list_id": None,
                    "media_id": None,
                }
                t = event["type"]
                if t == "text":
                    event["text"] = (msg.get("text") or {}).get("body")
                elif t == "interactive":
                    interactive = msg.get("interactive") or {}
                    kind = interactive.get("type")
                    if kind == "button_reply":
                        event["button_id"] = (interactive.get("button_reply") or {}).get("id")
                        event["text"] = (interactive.get("button_reply") or {}).get("title")
                    elif kind == "list_reply":
                        event["list_id"] = (interactive.get("list_reply") or {}).get("id")
                        event["text"] = (interactive.get("list_reply") or {}).get("title")
                elif t == "button":
                    # Legacy template-reply button (not interactive)
                    event["button_id"] = (msg.get("button") or {}).get("payload")
                    event["text"] = (msg.get("button") or {}).get("text")
                elif t in ("image", "document", "audio", "video", "sticker"):
                    media = msg.get(t) or {}
                    event["media_id"] = media.get("id")
                    event["text"] = media.get("caption")
                # Everything else (location, contacts, order…) is kept as-is
                events.append(event)
    return events


def _ensure_plus(phone: str | None) -> str | None:
    """Meta sends phones without '+' — we always store with '+' prefix."""
    if not phone:
        return phone
    return phone if phone.startswith("+") else f"+{phone}"


# ─────────────────────────────────────────────────────────────
# STATUS UPDATES: delivered / read / failed
# ─────────────────────────────────────────────────────────────
def _handle_statuses(body: dict, db: Session) -> None:
    """
    Update WhatsAppOutboundLog rows when Meta reports delivery state.
    Non-critical — failures here are swallowed.
    """
    for entry in body.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            for st in value.get("statuses", []) or []:
                wamid = st.get("id")
                status_str = st.get("status")  # sent | delivered | read | failed
                if not wamid:
                    continue
                try:
                    log = (
                        db.query(WhatsAppOutboundLog)
                        .filter(WhatsAppOutboundLog.wamid == wamid)
                        .first()
                    )
                    if log:
                        log.status = status_str or log.status
                        log.updated_at = datetime.datetime.utcnow()
                        if status_str == "failed":
                            errs = st.get("errors") or []
                            if errs:
                                log.last_error = str(errs[0])[:500]
                        db.commit()
                except Exception as e:
                    print(f"[WA_WEBHOOK] status update error (non-fatal): {e}")


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 1: GET /webhook/whatsapp/{secret} — Meta verification
# ═══════════════════════════════════════════════════════════════
@router.get("/webhook/whatsapp/{secret}")
async def verify_webhook(secret: str, request: Request):
    """
    Meta's one-time webhook verification handshake.

    Meta sends:
      GET /webhook/whatsapp/{secret}?hub.mode=subscribe
          &hub.verify_token=YOUR_VERIFY_TOKEN
          &hub.challenge=RANDOM

    We verify:
      1. The {secret} matches WHATSAPP_WEBHOOK_SECRET
      2. hub.verify_token matches WHATSAPP_VERIFY_TOKEN
    …and return the challenge number plain as the response body.
    """
    expected_secret = os.getenv("WHATSAPP_WEBHOOK_SECRET", "")
    if expected_secret and secret != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "asg-verify-token-2026")

    if mode == "subscribe" and token == verify_token and challenge:
        print(f"[WA_WEBHOOK] ✅ Verification OK for secret={secret[:4]}…")
        try:
            return int(challenge)
        except ValueError:
            return challenge

    print(f"[WA_WEBHOOK] ❌ Verification failed (mode={mode})")
    raise HTTPException(status_code=403, detail="Verification failed")


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 2: POST /webhook/whatsapp/{secret} — inbound hook
# ═══════════════════════════════════════════════════════════════
@router.post("/webhook/whatsapp/{secret}")
async def receive_whatsapp_webhook(secret: str, request: Request):
    """
    Handle inbound WhatsApp events from Meta.

    Design:
      1. Verify URL secret + HMAC signature (fail 403 on mismatch)
      2. Decode body, dedup on wamid, update status logs
      3. Hand each fresh message to whatsapp_engine.handle_inbound()
      4. ALWAYS return 200 OK (even on internal errors) — otherwise
         Meta retries aggressively and will eventually disable the hook.
    """
    global _last_inbound_at
    _last_inbound_at = datetime.datetime.utcnow()

    # ── 1. Secret check ──
    expected_secret = os.getenv("WHATSAPP_WEBHOOK_SECRET", "")
    if expected_secret and secret != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    # ── 2. Signature check ──
    raw = await request.body()
    if not _verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        print("[WA_WEBHOOK] ❌ Bad HMAC signature")
        raise HTTPException(status_code=403, detail="Bad signature")

    # ── 3. Decode JSON ──
    try:
        body = await request.json()
    except Exception:
        print("[WA_WEBHOOK] ⚠️ Non-JSON body — returning 200 to avoid retries")
        return {"status": "received"}

    # ── 4. Parse + dispatch ──
    db = SessionLocal()
    try:
        # Status updates (delivery receipts) come through the same webhook
        _handle_statuses(body, db)

        events = _parse_inbound(body)
        for event in events:
            wamid = event.get("wamid")
            if _is_duplicate_wamid(wamid):
                print(f"[WA_WEBHOOK] ⏩ duplicate wamid {wamid} — skipped")
                continue
            try:
                await whatsapp_engine.handle_inbound(event, db)
            except Exception as e:
                # Log + swallow so one bad event doesn't kill the batch
                print(f"[WA_WEBHOOK] ⚠️ engine error for {wamid}: {e}")
    finally:
        db.close()

    return {"status": "received"}


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 3: POST /api/v1/whatsapp/pairing-code
# ═══════════════════════════════════════════════════════════════
@router.post("/api/v1/whatsapp/pairing-code")
def request_pairing_code(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Dashboard endpoint. Returns a fresh 6-digit pairing code + a
    pre-filled wa.me deep link. The user taps the link → WhatsApp
    opens with 'LINK-482913' typed → they send → bot binds the phone.
    """
    try:
        payload = generate_pairing_code(current_user.id, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "code": payload["code"],
        "expires_in_seconds": payload["expires_in_seconds"],
        "instruction": payload["instruction"],
        "wa_me_url": payload["wa_me_url"],
    }


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 4: GET /api/v1/whatsapp/health
# ═══════════════════════════════════════════════════════════════
@router.get("/api/v1/whatsapp/health")
def whatsapp_health():
    """Liveness + last-inbound timestamp for monitoring/alerting."""
    last = _get_last_inbound_at()
    return {
        "ok": True,
        "configured": bool(
            os.getenv("WHATSAPP_PHONE_NUMBER_ID")
            and os.getenv("WHATSAPP_ACCESS_TOKEN")
        ),
        "signature_enforced": bool(os.getenv("WHATSAPP_APP_SECRET")),
        "last_inbound_at": last.isoformat() if last else None,
    }


# ═══════════════════════════════════════════════════════════════
# ENDPOINT: POST /api/v1/admin/whatsapp/mock-inbound  (DEV ONLY)
# ═══════════════════════════════════════════════════════════════
# Lets us drive the FSM end-to-end without a real Meta webhook —
# essential for testing the ONBOARDING flow before our Meta Developer
# account is provisioned. Disabled in production via the
# WA_MOCK_INBOUND_ENABLED env flag (default: enabled when WHATSAPP
# isn't configured for real Meta calls; explicitly disabled otherwise).
#
# REQUEST SHAPE (a friendly subset of the parsed-inbound dict):
#   {
#     "from":      "+972501234567",              required
#     "type":      "text" | "interactive" | ...  default "text"
#     "text":      "hello",                       optional
#     "button_id": "onb:legal:osek_morshe",       optional
#     "list_id":   "onb:btype:contractor",        optional
#     "wamid":     "wamid.MOCK-xxxxx",            auto-generated if missing
#   }
#
# Outbound messages are written to WhatsAppOutboundLog (kind, payload_json,
# status='failed' with reason='not_configured' when Meta creds absent) so
# the caller can read them back through GET /api/v1/admin/whatsapp/mock-outbox.
#
# ACCESS:
#   - Allowed when Meta is NOT configured (dev / pre-Meta).
#   - Allowed when WA_MOCK_INBOUND_ENABLED=1 explicitly.
#   - Otherwise returns 403 (production safety).
# ═══════════════════════════════════════════════════════════════
def _mock_inbound_allowed() -> bool:
    """Mock endpoint is open while Meta isn't wired, or when explicitly enabled."""
    if (os.getenv("WA_MOCK_INBOUND_ENABLED") or "").strip() == "1":
        return True
    # Allow by default if Meta is unconfigured (dev mode)
    if not (os.getenv("WHATSAPP_PHONE_NUMBER_ID") and os.getenv("WHATSAPP_ACCESS_TOKEN")):
        return True
    # Permit if the access token is still the placeholder string
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    if "YOUR_META_ACCESS_TOKEN" in token or token.endswith("_HERE"):
        return True
    return False


@router.post("/api/v1/admin/whatsapp/mock-inbound")
async def mock_inbound(request: Request, db: Session = Depends(get_db)):
    """
    Synthesize a parsed-inbound payload and route it through the FSM.
    Lets `tests/mock_whatsapp_inbound.py` (and curl/Postman) drive the
    bot locally without Meta. The response includes everything the bot
    sent in reply (from WhatsAppOutboundLog).
    """
    if not _mock_inbound_allowed():
        raise HTTPException(
            status_code=403,
            detail=(
                "Mock inbound is disabled. Set WA_MOCK_INBOUND_ENABLED=1 to enable, "
                "or only-allow when WHATSAPP_ACCESS_TOKEN is unset."
            ),
        )

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be JSON")

    from_phone = (body.get("from") or "").strip()
    if not from_phone:
        raise HTTPException(status_code=400, detail="'from' is required (E.164)")

    import uuid as _u
    parsed = {
        "wamid": body.get("wamid") or f"wamid.MOCK-{_u.uuid4().hex[:12]}",
        "from": from_phone,
        "type": body.get("type") or "text",
        "text": body.get("text"),
        "button_id": body.get("button_id"),
        "list_id": body.get("list_id"),
        "media_id": body.get("media_id"),
    }

    # Capture outbound messages issued during this turn so the test
    # client can assert against them. We do this by snapshotting
    # WhatsAppOutboundLog ids before/after.
    before_max = (
        db.query(WhatsAppOutboundLog.id)
        .order_by(WhatsAppOutboundLog.id.desc())
        .first()
    )
    before_id = before_max[0] if before_max else 0

    try:
        await whatsapp_engine.handle_inbound(parsed, db)
    except Exception as e:
        # Mirror real-webhook behavior: never surface a 500 — but in dev
        # we want to see the error, so include it in the response.
        return {
            "ok": False,
            "error": str(e),
            "echo_in": parsed,
            "outbound_messages": [],
        }

    new_logs = (
        db.query(WhatsAppOutboundLog)
        .filter(WhatsAppOutboundLog.id > before_id)
        .order_by(WhatsAppOutboundLog.id.asc())
        .all()
    )
    return {
        "ok": True,
        "echo_in": parsed,
        "outbound_messages": [
            {
                "id":           log.id,
                "to":           log.whatsapp_phone_e164,
                "kind":         log.message_kind,
                "status":       log.status,
                "last_error":   log.last_error,
                "payload":      json.loads(log.payload_json) if log.payload_json else None,
            }
            for log in new_logs
        ],
    }


# ═══════════════════════════════════════════════════════════════
# ENDPOINT: GET /api/v1/admin/whatsapp/mock-outbox
# ═══════════════════════════════════════════════════════════════
@router.get("/api/v1/admin/whatsapp/mock-outbox")
def mock_outbox(
    phone: str = "",
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """
    Read the WhatsAppOutboundLog tail. Useful for the CLI harness to
    inspect what the bot tried to send. Filters by phone if provided.
    """
    if not _mock_inbound_allowed():
        raise HTTPException(status_code=403, detail="Mock endpoints disabled")

    q = db.query(WhatsAppOutboundLog).order_by(WhatsAppOutboundLog.id.desc())
    if phone:
        q = q.filter(WhatsAppOutboundLog.whatsapp_phone_e164 == phone)
    logs = q.limit(min(max(limit, 1), 200)).all()
    return {
        "count": len(logs),
        "messages": [
            {
                "id":          log.id,
                "to":          log.whatsapp_phone_e164,
                "kind":        log.message_kind,
                "status":      log.status,
                "last_error":  log.last_error,
                "payload":     json.loads(log.payload_json) if log.payload_json else None,
                "created_at":  log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }
