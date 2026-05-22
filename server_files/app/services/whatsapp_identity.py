"""
ASG Solutions — WhatsApp Identity Service
==========================================
Handles the pairing flow that links a WhatsApp phone number to an
ASG dashboard user (and therefore to a business_id).

HOW THE PAIRING WORKS (Path A — owner self-bind):
  1. Ibrahim opens the dashboard → clicks "Link WhatsApp"
  2. Dashboard calls POST /api/v1/whatsapp/pairing-code
     → gets back {"code": "482913", "expires_in_seconds": 600,
                   "wa_me_url": "https://wa.me/9725XXXXXXX?text=LINK-482913"}
  3. Ibrahim taps the wa.me link → WhatsApp opens with the code pre-filled
     → sends "LINK-482913" to the bot.
  4. Bot calls verify_pairing_code("+9725...", "482913", db)
     → finds User, binds user.whatsapp_phone_e164, returns User.
  5. From now on, get_user_by_whatsapp_phone(phone, db) returns the User.

SECURITY:
  - 6-digit code (1M possibilities), 10-minute TTL, single-use.
  - Abuse is bounded by message-rate limiting at the engine layer.
  - Unbound phones see a "please link via dashboard" message — no data.

REAL-WORLD ANALOGY:
  Same "gym locker code" metaphor as Telegram. The locker now learns
  your phone number instead of your Telegram ID.
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import datetime
import json
import os
import random

from sqlalchemy.orm import Session

from app.database import User, WhatsAppSession

# Pairing code is valid for this many minutes
PAIRING_CODE_TTL_MINUTES = 10


# ─────────────────────────────────────────────────────────────
# HELPER: normalize_phone
# ─────────────────────────────────────────────────────────────
def normalize_phone(raw: str) -> str:
    """
    Meta delivers phones in E.164 WITHOUT the leading '+' (e.g.
    '972501234567'). We always store with the '+' prefix so we can
    compare reliably. This helper adds it if missing.
    """
    if not raw:
        return raw
    s = str(raw).strip()
    if not s.startswith("+"):
        s = "+" + s
    return s


# ─────────────────────────────────────────────────────────────
# FUNCTION: generate_pairing_code
# ─────────────────────────────────────────────────────────────
def generate_pairing_code(user_id: int, db: Session) -> dict:
    """
    Generate a 6-digit pairing code for a user and return a payload
    suitable for showing on the dashboard (includes a tap-to-WhatsApp
    wa.me deep link pre-filled with the LINK-XXXXXX message).

    Overwrites any existing code — the newer one wins.

    RETURNS:
      {
        "code": "482913",
        "expires_in_seconds": 600,
        "instruction": "LINK-482913",
        "wa_me_url": "https://wa.me/9725XXXXXXX?text=LINK-482913" or None,
      }

    RAISES:
      ValueError if user_id not found
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")

    code = f"{random.randint(100000, 999999)}"
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(
        minutes=PAIRING_CODE_TTL_MINUTES
    )

    user.whatsapp_pairing_code = code
    user.whatsapp_pairing_expires = expires_at
    db.commit()

    # ── Build the wa.me deep link if the bot phone is known ──
    bot_phone = os.getenv("WHATSAPP_BOT_PHONE_E164", "").lstrip("+")
    wa_me_url = None
    if bot_phone:
        wa_me_url = f"https://wa.me/{bot_phone}?text=LINK-{code}"

    print(
        f"[WA_ID] Pairing code {code} generated for user {user.email}, "
        f"expires {expires_at}"
    )
    return {
        "code": code,
        "expires_in_seconds": PAIRING_CODE_TTL_MINUTES * 60,
        "instruction": f"LINK-{code}",
        "wa_me_url": wa_me_url,
    }


# ─────────────────────────────────────────────────────────────
# FUNCTION: verify_pairing_code
# ─────────────────────────────────────────────────────────────
def verify_pairing_code(phone_e164: str, code: str, db: Session) -> User | None:
    """
    Verify a pairing code and permanently link the WhatsApp phone to
    the User record that owns the code.

    PARAMETERS:
      phone_e164 — WhatsApp phone (will be normalized to '+...')
      code       — 6-digit code from the user's LINK-XXXXXX message
      db         — database session

    RETURNS:
      User on success, None if code is invalid/expired.

    SIDE EFFECTS on success:
      - user.whatsapp_phone_e164 = phone_e164
      - user.whatsapp_pairing_code = None (one-time use)
      - user.whatsapp_pairing_expires = None
      - WhatsAppSession is created/refreshed for this phone.

    GUARD: If another user already owns this phone, the bind is
    refused (returns None) and a warning is logged. This prevents
    silent identity takeover.
    """
    now = datetime.datetime.utcnow()
    phone = normalize_phone(phone_e164)

    # ── Find the user who holds this code ──
    user = (
        db.query(User)
        .filter(
            User.whatsapp_pairing_code == code,
            User.whatsapp_pairing_expires > now,
        )
        .first()
    )

    if not user:
        print(f"[WA_ID] Invalid or expired pairing code: {code}")
        return None

    # ── Ensure the phone isn't already owned by someone else ──
    conflict = (
        db.query(User)
        .filter(
            User.whatsapp_phone_e164 == phone,
            User.id != user.id,
        )
        .first()
    )
    if conflict:
        print(
            f"[WA_ID] ⚠️ Phone {phone} already bound to user "
            f"{conflict.email}; refusing to rebind to {user.email}"
        )
        return None

    # ── Bind ──
    user.whatsapp_phone_e164 = phone
    user.whatsapp_pairing_code = None
    user.whatsapp_pairing_expires = None
    db.commit()

    # ── Create or refresh the WhatsApp session ──
    session = (
        db.query(WhatsAppSession)
        .filter(WhatsAppSession.whatsapp_phone_e164 == phone)
        .first()
    )
    if not session:
        session = WhatsAppSession(
            whatsapp_phone_e164=phone,
            user_id=user.id,
            business_id=user.business_id,
            locale=user.language_pref or "he",
        )
        db.add(session)
    else:
        session.user_id = user.id
        session.business_id = user.business_id
        if not session.locale:
            session.locale = user.language_pref or "he"
        session.updated_at = now
    db.commit()

    print(f"[WA_ID] ✅ Linked phone={phone} to user={user.email}")
    return user


# ─────────────────────────────────────────────────────────────
# FUNCTION: get_user_by_whatsapp_phone
# ─────────────────────────────────────────────────────────────
def get_user_by_whatsapp_phone(phone_e164: str, db: Session) -> User | None:
    """
    Look up the User linked to a given WhatsApp phone number.
    Called on every inbound message to identify the tenant.

    RETURNS: User object or None (if not yet paired).
    """
    phone = normalize_phone(phone_e164)
    return (
        db.query(User)
        .filter(
            User.whatsapp_phone_e164 == phone,
            User.is_active == True,  # noqa: E712
        )
        .first()
    )


# ─────────────────────────────────────────────────────────────
# FUNCTION: get_or_create_session
# ─────────────────────────────────────────────────────────────
def get_or_create_session(phone_e164: str, db: Session) -> WhatsAppSession:
    """
    Fetch the WhatsAppSession for this phone. Create a blank one
    (no user_id) if none exists yet. Used on every inbound message.
    """
    phone = normalize_phone(phone_e164)
    session = (
        db.query(WhatsAppSession)
        .filter(WhatsAppSession.whatsapp_phone_e164 == phone)
        .first()
    )
    if not session:
        session = WhatsAppSession(
            whatsapp_phone_e164=phone,
            locale="he",
        )
        db.add(session)
        db.commit()
        db.refresh(session)
    return session


# ─────────────────────────────────────────────────────────────
# FUNCTION: touch_last_inbound
# ─────────────────────────────────────────────────────────────
def touch_last_inbound(session: WhatsAppSession, db: Session) -> None:
    """
    Record that the user just sent us a message. This resets the
    24-hour WhatsApp window during which we can send freeform replies.
    """
    session.last_client_message_at = datetime.datetime.utcnow()
    session.updated_at = session.last_client_message_at
    db.commit()


def can_send_freeform(session: WhatsAppSession) -> bool:
    """
    True iff we're inside the 24-hour WhatsApp customer-initiated
    window (i.e. we're allowed to send freeform content, not only
    pre-approved templates).
    """
    if not session.last_client_message_at:
        return False
    age = datetime.datetime.utcnow() - session.last_client_message_at
    return age.total_seconds() < 24 * 3600


# ─────────────────────────────────────────────────────────────
# FUNCTION: save_draft_to_session
# ─────────────────────────────────────────────────────────────
def save_draft_to_session(
    phone_e164: str,
    state: str | None,
    draft: dict | None,
    db: Session,
    pending_message_id: str | None = None,
    pending_invoice_id: int | None = None,
) -> None:
    """
    Persist in-progress flow state after every step.
    Called before any outbound message so that if the process dies,
    the next webhook replay will re-enter the same state.
    """
    session = get_or_create_session(phone_e164, db)
    session.state = state
    session.draft_payload_json = (
        json.dumps(draft, ensure_ascii=False) if draft is not None else None
    )
    session.updated_at = datetime.datetime.utcnow()
    if pending_message_id is not None:
        session.pending_message_id = pending_message_id
    if pending_invoice_id is not None:
        session.pending_invoice_id = pending_invoice_id
    db.commit()


def load_draft(session: WhatsAppSession) -> dict:
    """Decode the draft JSON back to a dict (empty if none)."""
    if not session.draft_payload_json:
        return {}
    try:
        return json.loads(session.draft_payload_json)
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
# FUNCTION: clear_session_draft
# ─────────────────────────────────────────────────────────────
def clear_session_draft(phone_e164: str, db: Session) -> None:
    """Clear the draft/state but keep the phone↔user binding."""
    session = get_or_create_session(phone_e164, db)
    session.state = None
    session.draft_payload_json = None
    session.pending_message_id = None
    session.pending_invoice_id = None
    session.updated_at = datetime.datetime.utcnow()
    db.commit()
