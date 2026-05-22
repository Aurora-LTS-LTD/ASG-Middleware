"""
ASG Solutions — WhatsApp Meta Graph API Client
================================================
Thin, well-commented wrapper over the Meta WhatsApp Business Cloud API.

WHAT THIS DOES:
  - Sends messages (text, quick-reply buttons, list messages, documents,
    images, templates) to WhatsApp users via Meta's Graph API.
  - Downloads inbound media (receipts, images) sent by users.
  - Logs every outbound to WhatsAppOutboundLog BEFORE the HTTP call
    (write-ahead) so we can resend if the process dies between the
    log write and the API call.

WHY IT EXISTS AS A SEPARATE FILE:
  Keeping the raw HTTP calls here (not inside the engine/flows) means
  the FSM layer is testable with a mock client, and Meta-specific
  quirks (pagination, ToS-mandated business reply category, etc.) live
  in ONE file.

ENV VARS IT READS:
  WHATSAPP_ACCESS_TOKEN         — Long-lived System User token from Meta
  WHATSAPP_PHONE_NUMBER_ID      — The ID of *our* WhatsApp business number
  WHATSAPP_API_VERSION          — e.g. "v20.0" (default: v20.0)

REAL-WORLD ANALOGY:
  This is the hotel's "front desk phone operator." When a department
  wants to call a guest, they pass the message here, and the operator
  dials out through the hotel PBX (Meta's API) and records the call
  in the phone log (WhatsAppOutboundLog).
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import datetime
import json
import os
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.database import WhatsAppOutboundLog


# ─────────────────────────────────────────────────────────────
# CONFIG (read lazily so .env changes at runtime are picked up)
# ─────────────────────────────────────────────────────────────
def _api_base() -> str:
    version = os.getenv("WHATSAPP_API_VERSION", "v20.0")
    return f"https://graph.facebook.com/{version}"


def _phone_number_id() -> str:
    return os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")


def _access_token() -> str:
    return os.getenv("WHATSAPP_ACCESS_TOKEN", "")


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {_access_token()}",
        "Content-Type": "application/json",
    }


def is_configured() -> bool:
    """True iff the Meta credentials are present in the environment."""
    return bool(_phone_number_id() and _access_token())


# ─────────────────────────────────────────────────────────────
# INTERNAL: log then send (write-ahead pattern)
# ─────────────────────────────────────────────────────────────
async def _log_and_send(
    to_phone: str,
    message_kind: str,
    body: dict,
    db: Session,
    user_id: int | None = None,
    template_name: str | None = None,
) -> dict:
    """
    Persist an outbound log row BEFORE hitting Meta's API.

    Why: if the network call is killed halfway, the log row acts as
    our "we intended to send this" record. A background resend worker
    can pick up any log row stuck in 'pending' status and retry.

    Returns: {"ok": bool, "wamid": str|None, "error": str|None}
    """
    # ── 1. Write-ahead log ──
    log = WhatsAppOutboundLog(
        whatsapp_phone_e164=to_phone,
        user_id=user_id,
        message_kind=message_kind,
        payload_json=json.dumps(body, ensure_ascii=False),
        template_name=template_name,
        status="pending",
        attempts=0,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    # ── 2. HTTP call ──
    if not is_configured():
        log.status = "failed"
        log.last_error = "Meta credentials not configured"
        log.attempts = 1
        log.updated_at = datetime.datetime.utcnow()
        db.commit()
        print(f"[WA_META] ❌ Not configured — would have sent {message_kind} to {to_phone}")
        return {"ok": False, "wamid": None, "error": "not_configured"}

    url = f"{_api_base()}/{_phone_number_id()}/messages"
    log.attempts = (log.attempts or 0) + 1
    db.commit()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=_auth_headers(), json=body)
    except Exception as e:
        log.status = "failed"
        log.last_error = f"HTTP exception: {e}"
        log.updated_at = datetime.datetime.utcnow()
        db.commit()
        print(f"[WA_META] ❌ HTTP error sending to {to_phone}: {e}")
        return {"ok": False, "wamid": None, "error": str(e)}

    # ── 3. Interpret response ──
    if resp.status_code >= 200 and resp.status_code < 300:
        try:
            data = resp.json()
            messages = data.get("messages") or []
            wamid = messages[0].get("id") if messages else None
        except Exception:
            wamid = None
        log.status = "sent"
        log.wamid = wamid
        log.updated_at = datetime.datetime.utcnow()
        db.commit()
        print(f"[WA_META] ✅ {message_kind} → {to_phone} (wamid={wamid})")
        return {"ok": True, "wamid": wamid, "error": None}

    # ── Failure ──
    err_text = resp.text[:500]
    log.status = "failed" if resp.status_code < 500 else "pending"
    log.last_error = f"HTTP {resp.status_code}: {err_text}"
    log.updated_at = datetime.datetime.utcnow()
    db.commit()
    print(
        f"[WA_META] ❌ {message_kind} → {to_phone} failed "
        f"(HTTP {resp.status_code}): {err_text}"
    )

    # ── ExecEvent on terminal failure only (4xx) — Tier 1 Alert Stream
    if log.status == "failed":
        try:
            from app.services.exec_events import publish_exec_event
            publish_exec_event(
                db,
                kind="wa_send_failed",
                severity="warning",
                title=f"WhatsApp send failed: {to_phone}",
                detail=f"kind={message_kind} http={resp.status_code} err={err_text[:140]}",
                related_entity_type="whatsapp_outbound_log",
                related_entity_id=log.id,
            )
        except Exception:
            pass

    return {"ok": False, "wamid": None, "error": log.last_error}


# ─────────────────────────────────────────────────────────────
# PUBLIC: send_text
# ─────────────────────────────────────────────────────────────
async def send_text(
    to_phone: str,
    text: str,
    db: Session,
    user_id: int | None = None,
    preview_url: bool = False,
) -> dict:
    """Send a plain text message (no interactivity)."""
    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone.lstrip("+"),
        "type": "text",
        "text": {
            "body": text,
            "preview_url": preview_url,
        },
    }
    return await _log_and_send(to_phone, "text", body, db, user_id)


# ─────────────────────────────────────────────────────────────
# PUBLIC: send_buttons (quick-reply)
# ─────────────────────────────────────────────────────────────
async def send_buttons(
    to_phone: str,
    body_text: str,
    buttons: list[dict],
    db: Session,
    user_id: int | None = None,
    header_text: str | None = None,
    footer_text: str | None = None,
) -> dict:
    """
    Send an interactive message with up to 3 quick-reply buttons.

    `buttons` is a list of {"id": "unique_id", "title": "Label ≤20 chars"}.
    Any extra buttons beyond the first 3 are dropped (Meta limit).
    """
    interactive: dict[str, Any] = {
        "type": "button",
        "body": {"text": body_text},
        "action": {
            "buttons": [
                {
                    "type": "reply",
                    "reply": {
                        "id": b["id"],
                        "title": b["title"][:20],  # Meta hard limit
                    },
                }
                for b in buttons[:3]
            ]
        },
    }
    if header_text:
        interactive["header"] = {"type": "text", "text": header_text[:60]}
    if footer_text:
        interactive["footer"] = {"text": footer_text[:60]}

    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone.lstrip("+"),
        "type": "interactive",
        "interactive": interactive,
    }
    return await _log_and_send(to_phone, "buttons", body, db, user_id)


# ─────────────────────────────────────────────────────────────
# PUBLIC: send_list
# ─────────────────────────────────────────────────────────────
async def send_list(
    to_phone: str,
    body_text: str,
    button_label: str,
    sections: list[dict],
    db: Session,
    user_id: int | None = None,
    header_text: str | None = None,
    footer_text: str | None = None,
) -> dict:
    """
    Send a List Message. User taps a button to expand a list of rows.

    `sections` is a list of:
      {
        "title": "Section title",   # optional, shown as a header within the list
        "rows": [
          {"id": "row_id", "title": "Row title ≤24 chars",
           "description": "Optional ≤72 chars"},
          ...
        ],
      }

    Limits: max 10 total rows across all sections. Button label ≤20 chars.
    """
    interactive: dict[str, Any] = {
        "type": "list",
        "body": {"text": body_text},
        "action": {
            "button": button_label[:20],
            "sections": sections,
        },
    }
    if header_text:
        interactive["header"] = {"type": "text", "text": header_text[:60]}
    if footer_text:
        interactive["footer"] = {"text": footer_text[:60]}

    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone.lstrip("+"),
        "type": "interactive",
        "interactive": interactive,
    }
    return await _log_and_send(to_phone, "list", body, db, user_id)


# ─────────────────────────────────────────────────────────────
# PUBLIC: send_document (for invoice PDFs)
# ─────────────────────────────────────────────────────────────
async def send_document(
    to_phone: str,
    document_link: str,
    filename: str,
    caption: str,
    db: Session,
    user_id: int | None = None,
) -> dict:
    """
    Send a PDF (or any document) to the user by HTTPS link.
    For our use case this will be the `pdf_url` from `invoice_service`.

    NOTE: Meta requires the link to be publicly reachable. On local
    dev (laptop + ngrok) this means the URL must be served over the
    ngrok tunnel. On GCP it's the load balancer URL.
    """
    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone.lstrip("+"),
        "type": "document",
        "document": {
            "link": document_link,
            "filename": filename,
            "caption": caption[:1024],
        },
    }
    return await _log_and_send(to_phone, "document", body, db, user_id)


# ─────────────────────────────────────────────────────────────
# PUBLIC: send_template (for Morning Pulse & re-engagement)
# ─────────────────────────────────────────────────────────────
async def send_template(
    to_phone: str,
    template_name: str,
    language_code: str,
    components: list[dict] | None,
    db: Session,
    user_id: int | None = None,
) -> dict:
    """
    Send a pre-approved template message. Required to re-open a
    conversation after the 24-hour window has lapsed.

    `language_code` examples: "he", "ar", "en_US".
    `components` follows Meta's template spec — see the platform docs.
    """
    template_body: dict[str, Any] = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if components:
        template_body["components"] = components

    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone.lstrip("+"),
        "type": "template",
        "template": template_body,
    }
    return await _log_and_send(
        to_phone, "template", body, db, user_id, template_name=template_name
    )


# ─────────────────────────────────────────────────────────────
# PUBLIC: mark_message_read
# ─────────────────────────────────────────────────────────────
async def mark_message_read(message_id: str) -> None:
    """
    Best-effort "blue-tick" for the user's last inbound message.
    Signals engagement without blocking the conversation.
    Failure is swallowed (cosmetic only).
    """
    if not is_configured() or not message_id:
        return
    url = f"{_api_base()}/{_phone_number_id()}/messages"
    body = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, headers=_auth_headers(), json=body)
    except Exception:
        # Non-fatal
        pass


# ─────────────────────────────────────────────────────────────
# PUBLIC: download_media  (for Receipt Box)
# ─────────────────────────────────────────────────────────────
async def download_media(media_id: str) -> tuple[bytes, str] | None:
    """
    Download inbound media (image/document) from Meta in two steps:
      1. GET /{media-id} → returns a temporary URL (expires ~5 min).
      2. GET that URL (still needs auth header) → returns the bytes.

    RETURNS: (bytes, mime_type) or None on any failure.
    """
    if not is_configured() or not media_id:
        return None

    # Step 1: get the temporary URL
    meta_url = f"{_api_base()}/{media_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r1 = await client.get(meta_url, headers=_auth_headers())
            if r1.status_code != 200:
                print(f"[WA_META] download_media: step 1 failed ({r1.status_code})")
                return None
            j = r1.json()
            media_url = j.get("url")
            mime_type = j.get("mime_type", "application/octet-stream")
            if not media_url:
                return None

            # Step 2: download bytes (needs the same bearer header)
            r2 = await client.get(media_url, headers={"Authorization": f"Bearer {_access_token()}"})
            if r2.status_code != 200:
                print(f"[WA_META] download_media: step 2 failed ({r2.status_code})")
                return None
            return r2.content, mime_type
    except Exception as e:
        print(f"[WA_META] download_media error: {e}")
        return None
