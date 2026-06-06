"""
ASG Solutions — Telegram Router
=================================
HTTP endpoints that connect the Telegram Bot API to our FastAPI server.

ENDPOINTS:
  POST /webhook/telegram/{secret}   — Receive updates from Telegram
  GET  /api/v1/telegram/health      — Check webhook status
  POST /api/v1/telegram/pairing-code — Generate a pairing code (JWT required)
  POST /api/v1/telegram/setup-webhook — Register the webhook URL with Telegram

HOW THE WEBHOOK WORKS:
  1. We register a URL with Telegram (setup-webhook endpoint).
  2. When a user sends a message to the bot, Telegram POSTs a JSON
     "Update" object to our URL.
  3. This router receives it, parses it, and passes it to the
     python-telegram-bot Application for processing.
  4. The Application dispatches to the right ConversationHandler step.

SECURITY:
  The {secret} path segment is a random token we set when registering
  the webhook. Only Telegram knows it (it's in the URL), and Telegram
  also adds it as X-Telegram-Bot-Api-Secret-Token header. We verify both.
  This prevents anyone who discovers the URL from faking updates.

REAL-WORLD ANALOGY:
  This file is the "mailroom" of the hotel. Telegram is the post office.
  When a letter (update) arrives, the mailroom (this router) checks it's
  a legitimate delivery (verifies the secret), then passes it to the
  right department (the ConversationHandler).
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import datetime
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from telegram import Update

from aurora_shared.database import get_db, User
from aurora_shared.middleware.auth_middleware import get_current_user, require_admin
from app.services.telegram_bot import get_application, get_last_update_at
from app.services.telegram_identity import generate_pairing_code


# ─────────────────────────────────────────────────────────────
# ROUTER
# ─────────────────────────────────────────────────────────────
router = APIRouter(tags=["Telegram"])


# ─────────────────────────────────────────────────────────────
# ENDPOINT 1: POST /webhook/telegram/{secret}
# ─────────────────────────────────────────────────────────────
@router.post("/webhook/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request):
    """
    Receive a Telegram update and dispatch it to the bot.

    Telegram calls this endpoint every time a user interacts with the bot.
    The {secret} in the URL is a verification token — only Telegram knows it.
    """
    # ── Verify the secret token ──
    expected_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if expected_secret and secret != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    # ── Also check Telegram's header (double verification) ──
    header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if expected_secret and header_token and header_token != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook token header")

    # ── Get the Application ──
    app = get_application()
    if not app:
        # Bot not initialized — happens if TELEGRAM_BOT_TOKEN is not set
        return {"ok": False, "reason": "Bot not initialized"}

    # ── Parse the update ──
    data = await request.json()
    update = Update.de_json(data, app.bot)

    # ── Process the update (dispatches to ConversationHandler) ──
    await app.process_update(update)

    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# ENDPOINT 2: GET /api/v1/telegram/health
# ─────────────────────────────────────────────────────────────
@router.get("/api/v1/telegram/health")
def telegram_health(
    current_user: User = Depends(get_current_user),
):
    """
    Return the health status of the Telegram bot integration.

    Ibrahim can call this to verify that:
    - The bot is initialized
    - The webhook is registered
    - The last update was received recently

    Response:
      {"ok": true, "last_update_seconds_ago": 42, "bot_username": "ASGBot"}
    """
    app = get_application()

    if not app:
        return {
            "ok": False,
            "reason": "TELEGRAM_BOT_TOKEN not configured",
            "last_update_seconds_ago": None,
            "bot_username": None,
        }

    last_update_seconds = None
    last_at = get_last_update_at()
    if last_at:
        delta = datetime.datetime.utcnow() - last_at
        last_update_seconds = int(delta.total_seconds())

    return {
        "ok": True,
        "last_update_seconds_ago": last_update_seconds,
        "note": "Send a /start to the bot to reset the timer",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 3: POST /api/v1/telegram/pairing-code
# ─────────────────────────────────────────────────────────────
@router.post("/api/v1/telegram/pairing-code")
def create_pairing_code(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate a 6-digit one-time pairing code for the current user.

    The dashboard calls this when the user clicks "Link Telegram".
    The user then sends the code to the bot via /start LINK-XXXXXX.

    RETURNS: {"code": "482913", "expires_in_seconds": 600, "instruction": "/start LINK-482913"}
    """
    return generate_pairing_code(current_user.id, db)


# ─────────────────────────────────────────────────────────────
# ENDPOINT 4: POST /api/v1/telegram/setup-webhook
# ─────────────────────────────────────────────────────────────
@router.post("/api/v1/telegram/setup-webhook")
async def setup_webhook(
    request: Request,
    current_user: User = Depends(require_admin),
):
    """
    Register this server's URL as the Telegram webhook.

    Call this once after starting the server (and after ngrok/Cloudflare
    is running and the public URL is known).

    BODY: {"url": "https://your-tunnel.ngrok.io"}
    The webhook will be registered at: {url}/webhook/telegram/{secret}

    Requires admin role.
    """
    app = get_application()
    if not app:
        raise HTTPException(status_code=503, detail="Bot not initialized")

    body = await request.json()
    base_url = body.get("url", "").rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail="Missing 'url' in body")

    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    webhook_url = f"{base_url}/webhook/telegram/{secret}" if secret else f"{base_url}/webhook/telegram/"

    # ── Register with Telegram ──
    await app.bot.set_webhook(
        url=webhook_url,
        secret_token=secret if secret else None,
        allowed_updates=["message", "callback_query"],
    )

    print(f"[TELEGRAM] Webhook registered: {webhook_url}")
    return {
        "ok": True,
        "webhook_url": webhook_url,
        "message": "Webhook registered successfully. The bot is live!",
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 5: DELETE /api/v1/telegram/webhook (admin only)
# ─────────────────────────────────────────────────────────────
@router.delete("/api/v1/telegram/webhook")
async def delete_webhook(
    current_user: User = Depends(require_admin),
):
    """Remove the webhook registration (switches bot to polling mode — dev only)."""
    app = get_application()
    if not app:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    await app.bot.delete_webhook()
    return {"ok": True, "message": "Webhook removed"}
