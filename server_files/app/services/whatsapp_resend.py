"""
ASG Solutions — WhatsApp Outbound Resend Worker
================================================
Background task that retries WhatsApp messages that we TRIED to send
but never got a successful response from Meta.

WHY IT EXISTS:
  In whatsapp_meta_client._log_and_send() we persist a row to
  WhatsAppOutboundLog BEFORE the HTTP call (write-ahead logging).
  If Meta returns a 5xx, or the network times out, or the FastAPI
  process dies between the log row and the HTTP call, the row stays
  in status='pending'. This worker picks those rows up and retries.

WHAT IT DOES EVERY 30 SECONDS:
  1. Find outbound logs where status='pending' AND attempts < 3
     AND created_at is older than 5 minutes (avoid racing the initial
     send which might still be in flight).
  2. Re-POST the stored payload to Meta.
  3. On success: mark as 'sent' with the new wamid.
  4. On failure: bump attempts; after 3 attempts mark as 'failed'.

REAL-WORLD ANALOGY:
  Think of a mailbox where the mail carrier picks up unsent letters
  every 30 minutes. If a letter keeps coming back undelivered after
  three tries, it goes in the "dead letter" pile for manual review.
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import asyncio
import datetime
import json
import os

import httpx

from app.database import SessionLocal, WhatsAppOutboundLog
from app.services import whatsapp_meta_client as wa


# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
MAX_ATTEMPTS = 3              # Give up after this many tries
GRACE_PERIOD_SECONDS = 300    # Don't touch rows < 5 min old
POLL_INTERVAL_SECONDS = 30    # How often the loop runs


# ─────────────────────────────────────────────────────────────
# FUNCTION: process_pending_outbounds
# ─────────────────────────────────────────────────────────────
async def process_pending_outbounds() -> int:
    """
    Retry all pending WhatsApp outbound log rows. Returns the number
    of rows we re-attempted this cycle (success + failure).
    """
    if not wa.is_configured():
        # No Meta creds → no point trying; caller should keep polling
        # in case the env gets updated at runtime.
        return 0

    db = SessionLocal()
    now = datetime.datetime.utcnow()
    cutoff = now - datetime.timedelta(seconds=GRACE_PERIOD_SECONDS)
    retried = 0

    try:
        rows = (
            db.query(WhatsAppOutboundLog)
            .filter(
                WhatsAppOutboundLog.status == "pending",
                WhatsAppOutboundLog.attempts < MAX_ATTEMPTS,
                WhatsAppOutboundLog.created_at <= cutoff,
            )
            .order_by(WhatsAppOutboundLog.created_at.asc())
            .limit(50)
            .all()
        )

        if rows:
            print(f"[WA_RESEND] Retrying {len(rows)} pending outbound(s)")

        for row in rows:
            retried += 1
            row.attempts = (row.attempts or 0) + 1
            row.updated_at = now
            db.commit()

            # Re-POST the stored payload
            try:
                body = json.loads(row.payload_json or "{}")
            except Exception:
                row.status = "failed"
                row.last_error = "payload_json not decodable"
                db.commit()
                continue

            url = f"{_api_base()}/{_phone_number_id()}/messages"
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(url, headers=_auth_headers(), json=body)
            except Exception as e:
                row.last_error = f"HTTP exception: {e}"
                # Leave as pending for the next cycle
                if row.attempts >= MAX_ATTEMPTS:
                    row.status = "failed"
                db.commit()
                continue

            if 200 <= resp.status_code < 300:
                try:
                    data = resp.json()
                    messages = data.get("messages") or []
                    wamid = messages[0].get("id") if messages else None
                except Exception:
                    wamid = None
                row.status = "sent"
                row.wamid = wamid
                row.updated_at = datetime.datetime.utcnow()
                db.commit()
                print(f"[WA_RESEND] ✅ Resent → {row.whatsapp_phone_e164} (wamid={wamid})")
            else:
                err = resp.text[:500]
                row.last_error = f"HTTP {resp.status_code}: {err}"
                # 4xx is permanent; 5xx → try again next cycle
                if resp.status_code < 500 or row.attempts >= MAX_ATTEMPTS:
                    row.status = "failed"
                row.updated_at = datetime.datetime.utcnow()
                db.commit()
                print(
                    f"[WA_RESEND] ❌ {row.whatsapp_phone_e164} "
                    f"(HTTP {resp.status_code}): {err}"
                )

                # ── ExecEvent for the CEO Alert Stream (defensive)
                if row.status == "failed":
                    try:
                        from app.services.exec_events import publish_exec_event
                        publish_exec_event(
                            db,
                            kind="wa_send_failed",
                            severity="warning",
                            title=f"WhatsApp send failed: {row.whatsapp_phone_e164}",
                            detail=(
                                f"kind={row.message_kind} template={row.template_name or '-'} "
                                f"attempts={row.attempts} last_error={row.last_error[:140] if row.last_error else ''}"
                            ),
                            related_entity_type="whatsapp_outbound_log",
                            related_entity_id=row.id,
                        )
                    except Exception:
                        pass
    finally:
        db.close()

    return retried


# ─────────────────────────────────────────────────────────────
# PRIVATE: reuse meta_client's auth helpers (but local copies to
# avoid reaching into a private module)
# ─────────────────────────────────────────────────────────────
def _api_base() -> str:
    version = os.getenv("WHATSAPP_API_VERSION", "v20.0")
    return f"https://graph.facebook.com/{version}"


def _phone_number_id() -> str:
    return os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('WHATSAPP_ACCESS_TOKEN', '')}",
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────────────────────
# BACKGROUND TASK: whatsapp_resend_loop
# ─────────────────────────────────────────────────────────────
async def whatsapp_resend_loop() -> None:
    """
    Long-running asyncio task. Polls every POLL_INTERVAL_SECONDS
    seconds forever. Must never crash — any exception is logged and
    the loop continues.

    USAGE (in main.py startup):
        asyncio.create_task(whatsapp_resend_loop())
    """
    print(
        f"[WA_RESEND] Worker started "
        f"(poll every {POLL_INTERVAL_SECONDS}s, grace {GRACE_PERIOD_SECONDS}s)"
    )
    while True:
        try:
            await process_pending_outbounds()
        except Exception as e:
            print(f"[WA_RESEND] ⚠️ Worker error (continuing): {e}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
