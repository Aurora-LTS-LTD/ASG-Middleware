"""
WhatsApp Webhook Router
========================
This file handles ALL communication with WhatsApp (via Meta).
Two jobs:
  1. VERIFICATION — Meta "knocks on the door" to check we're real (GET)
  2. RECEIVING — Meta delivers customer messages to us (POST)

Think of it like a hotel receptionist:
  - First checks your reservation (verification)
  - Then takes your message and passes it to the right department
"""

from fastapi import APIRouter, Request, HTTPException, Query
import os
import json

from database import SessionLocal, ActionLog
from services.make_service import send_to_make

# ─────────────────────────────────────────────────────────────
# CREATE THE ROUTER
# APIRouter = a "mini server" that handles a group of related
# endpoints. We'll plug this into the main app later.
# prefix="/webhook/whatsapp" means all URLs here start with that path.
# ─────────────────────────────────────────────────────────────
router = APIRouter(
    prefix="/webhook/whatsapp",
    tags=["WhatsApp"],          # groups these endpoints in the API docs
)

# ─────────────────────────────────────────────────────────────
# VERIFY TOKEN
# This is a secret password that YOU choose. When Meta "knocks
# on the door" to verify your server, it sends this token.
# Your server checks: "Is this the password I set?" If yes → legit.
# We read it from the .env file so it's not hardcoded in the code.
# ─────────────────────────────────────────────────────────────
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "asg-verify-token-2026")


# ─────────────────────────────────────────────────────────────
# ENDPOINT 1: GET /webhook/whatsapp
# VERIFICATION — Meta sends a GET request to check if we're real.
#
# Meta sends 3 things:
#   hub.mode        → always "subscribe"
#   hub.verify_token → the secret password
#   hub.challenge   → a random number we must echo back
#
# If the password matches → we return the challenge number
# If not → we reject with 403 (Forbidden)
#
# This only happens ONCE when you first connect WhatsApp.
# ─────────────────────────────────────────────────────────────
@router.get("")
def verify_webhook(
    # These are "query parameters" — they come after the ? in the URL
    # Example: /webhook/whatsapp?hub.mode=subscribe&hub.verify_token=abc&hub.challenge=123
    mode:         str = Query(None, alias="hub.mode"),
    token:        str = Query(None, alias="hub.verify_token"),
    challenge:    str = Query(None, alias="hub.challenge"),
):
    print(f"[WHATSAPP] Verification request: mode={mode}, token={token}")

    # Check 1: Is the mode "subscribe"?
    # Check 2: Does the token match our secret password?
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print(f"[WHATSAPP] ✅ Verification successful!")
        # Return the challenge number as plain text (not JSON!)
        # Meta expects JUST the number, nothing else.
        return int(challenge)
    else:
        print(f"[WHATSAPP] ❌ Verification failed — wrong token")
        raise HTTPException(status_code=403, detail="Verification failed")


# ─────────────────────────────────────────────────────────────
# ENDPOINT 2: POST /webhook/whatsapp
# RECEIVING — Meta sends customer messages here.
#
# When a customer sends "Hello" on WhatsApp, Meta wraps it in
# a big JSON object and POSTs it to this endpoint.
#
# The JSON structure from Meta looks like:
# {
#   "entry": [{
#     "changes": [{
#       "value": {
#         "messages": [{
#           "from": "972501234567",    ← customer phone number
#           "text": { "body": "Hello" } ← the actual message
#         }]
#       }
#     }]
#   }]
# }
#
# Our job: dig into this structure, extract the phone + message,
# and print/log it. Later we'll send it to Make.com for AI.
# ─────────────────────────────────────────────────────────────
@router.post("")
async def receive_message(request: Request):
    # Read the raw JSON body that Meta sent us
    body = await request.json()

    # Print the FULL payload so we can see what Meta sends (for learning)
    print(f"\n[WHATSAPP] 📩 Incoming webhook:")
    print(json.dumps(body, indent=2, ensure_ascii=False))

    # ── Try to extract the actual message ──
    try:
        entry    = body.get("entry", [])           # outer wrapper
        changes  = entry[0].get("changes", [])     # list of changes
        value    = changes[0].get("value", {})      # the actual data
        messages = value.get("messages", [])        # list of messages

        if messages:
            # We have a real message from a customer!
            msg       = messages[0]                  # take the first message
            sender    = msg.get("from", "unknown")   # phone number
            msg_type  = msg.get("type", "unknown")   # text / image / audio / etc.

            # Extract the text content (if it's a text message)
            if msg_type == "text":
                text = msg["text"]["body"]
            else:
                text = f"[{msg_type} message — not text]"

            print(f"[WHATSAPP] 📱 From: {sender}")
            print(f"[WHATSAPP] 💬 Message: {text}")
            print(f"[WHATSAPP] 📦 Type: {msg_type}")

            # Forward to Make.com for AI processing
            make_result = await send_to_make(sender, text, business_id=None)
            make_status = "forwarded" if make_result is not None else "forward_failed"

            # ── Log this message to the action_logs database table ──
            db = SessionLocal()
            try:
                detail_text = f"WhatsApp from {sender}: {text}"[:200]

                log_entry = ActionLog(
                    template_id=None,
                    business_id=None,
                    status=make_status,
                    detail=detail_text,
                )
                db.add(log_entry)    # stage the new row (like putting it in the "to save" pile)
                db.commit()          # actually write it to the database file
                print(f"[WHATSAPP] Saved to action_logs (id={log_entry.id})")
            except Exception as db_err:
                # If the database save fails, log the error but don't crash the webhook
                print(f"[WHATSAPP] Failed to save to action_logs: {db_err}")
            finally:
                db.close()           # always close the session to free the connection

        else:
            # No messages — might be a status update (delivered, read, etc.)
            print(f"[WHATSAPP] ℹ️ Non-message event (status update)")

    except (IndexError, KeyError) as e:
        # If the JSON structure is unexpected, don't crash — just log it
        print(f"[WHATSAPP] ⚠️ Could not parse message: {e}")

    # IMPORTANT: Always return 200 OK to Meta!
    # If we return an error, Meta will keep retrying and eventually
    # disconnect our webhook. Always say "got it!" even if parsing failed.
    return {"status": "received"}
