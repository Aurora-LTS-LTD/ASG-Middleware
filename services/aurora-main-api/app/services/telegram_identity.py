"""
ASG Solutions — Telegram Identity Service
==========================================
Handles the pairing flow that links a Telegram user to their
ASG dashboard account.

HOW THE PAIRING WORKS:
  1. Ibrahim opens the dashboard → clicks "Link Telegram"
  2. The dashboard calls POST /api/v1/telegram/pairing-code
     → gets back: {"code": "482913", "expires_in_seconds": 600}
  3. Ibrahim opens the ASG Telegram bot and sends:
       /start LINK-482913
  4. The bot calls verify_pairing_code("482913", telegram_user_id)
     → finds the User with that code, links the IDs, returns the User
  5. From now on, get_user_by_telegram_id(telegram_user_id) returns
     that User instantly — bot knows who's talking and which business
     they belong to.

SECURITY:
  - Code is 6 digits (1,000,000 possibilities) — good enough for
    a private admin tool with rate limiting.
  - Code expires after 10 minutes.
  - Code is one-time use: cleared on successful bind.
  - Unbound users see NO business data, ever.

REAL-WORLD ANALOGY:
  Like a gym locker code. The receptionist (dashboard) gives you a
  temporary code (pairing code). You type it into the locker (bot)
  to claim your locker. After that, the locker remembers your face
  (telegram_user_id) so you never need a code again.
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import datetime
import random

from sqlalchemy.orm import Session

from aurora_shared.database import User, TelegramSession

# Pairing code is valid for this many minutes
PAIRING_CODE_TTL_MINUTES = 10


# ─────────────────────────────────────────────────────────────
# FUNCTION: generate_pairing_code
# ─────────────────────────────────────────────────────────────
def generate_pairing_code(user_id: int, db: Session) -> dict:
    """
    Generate a new 6-digit pairing code for a user.

    Overwrites any existing code (if the user clicks "Generate" again
    before the old one expires, the old one is gone — no confusion).

    RETURNS: {"code": "482913", "expires_in_seconds": 600}
    RAISES:  ValueError if user_id not found
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")

    # Generate a 6-digit numeric code — padded with leading zeros if needed
    code = f"{random.randint(100000, 999999)}"
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=PAIRING_CODE_TTL_MINUTES)

    user.telegram_pairing_code = code
    user.telegram_pairing_expires = expires_at
    db.commit()

    print(f"[TELEGRAM_ID] Pairing code {code} generated for user {user.email}, expires {expires_at}")
    return {
        "code": code,
        "expires_in_seconds": PAIRING_CODE_TTL_MINUTES * 60,
        "instruction": f"/start LINK-{code}",
    }


# ─────────────────────────────────────────────────────────────
# FUNCTION: verify_pairing_code
# ─────────────────────────────────────────────────────────────
def verify_pairing_code(telegram_user_id: str, code: str, db: Session) -> User | None:
    """
    Verify a pairing code and permanently link the Telegram user ID
    to the corresponding User record.

    PARAMETERS:
      telegram_user_id — Telegram's numeric user ID as a string
      code             — the 6-digit code the user typed in the bot
      db               — database session

    RETURNS:
      The User object on success, None if code is invalid or expired

    SIDE EFFECTS:
      On success:
        - Sets user.telegram_user_id = telegram_user_id
        - Clears user.telegram_pairing_code and telegram_pairing_expires
        - Creates or updates a TelegramSession for this user
    """
    now = datetime.datetime.utcnow()

    # ── Find the user who holds this code ──
    user = db.query(User).filter(
        User.telegram_pairing_code == code,
        User.telegram_pairing_expires > now,
    ).first()

    if not user:
        print(f"[TELEGRAM_ID] Invalid or expired pairing code: {code}")
        return None

    # ── Link the Telegram ID ──
    user.telegram_user_id = str(telegram_user_id)
    user.telegram_pairing_code = None       # One-time use: clear immediately
    user.telegram_pairing_expires = None
    db.commit()

    # ── Create or refresh TelegramSession ──
    session = db.query(TelegramSession).filter(
        TelegramSession.telegram_user_id == str(telegram_user_id)
    ).first()
    if not session:
        session = TelegramSession(
            telegram_user_id=str(telegram_user_id),
            user_id=user.id,
            business_id=user.business_id,
        )
        db.add(session)
    else:
        session.user_id = user.id
        session.business_id = user.business_id
        session.updated_at = now
    db.commit()

    print(f"[TELEGRAM_ID] ✅ Linked telegram_user_id={telegram_user_id} to user={user.email}")
    return user


# ─────────────────────────────────────────────────────────────
# FUNCTION: get_user_by_telegram_id
# ─────────────────────────────────────────────────────────────
def get_user_by_telegram_id(telegram_user_id: str, db: Session) -> User | None:
    """
    Look up the User linked to a given Telegram user ID.

    Called by the bot on every incoming message to identify who
    is talking and which business they belong to.

    RETURNS: User object or None (if not yet paired)
    """
    return db.query(User).filter(
        User.telegram_user_id == str(telegram_user_id),
        User.is_active == True,
    ).first()


# ─────────────────────────────────────────────────────────────
# FUNCTION: get_or_create_session
# ─────────────────────────────────────────────────────────────
def get_or_create_session(telegram_user_id: str, db: Session) -> TelegramSession:
    """
    Return the TelegramSession for this user, creating a blank one
    if none exists. Used by the bot to read/write draft state.
    """
    session = db.query(TelegramSession).filter(
        TelegramSession.telegram_user_id == str(telegram_user_id)
    ).first()
    if not session:
        session = TelegramSession(telegram_user_id=str(telegram_user_id))
        db.add(session)
        db.commit()
        db.refresh(session)
    return session


# ─────────────────────────────────────────────────────────────
# FUNCTION: save_draft_to_session
# ─────────────────────────────────────────────────────────────
def save_draft_to_session(
    telegram_user_id: str,
    state: str,
    draft: dict,
    db: Session,
    pending_message_id: int | None = None,
    pending_invoice_id: int | None = None,
) -> None:
    """
    Persist the current in-progress invoice draft to the DB.
    Called after EVERY step of the invoice flow so that if the
    user's phone dies, they can resume via /start.

    PARAMETERS:
      state               — current ConversationHandler state name
      draft               — dict of fields collected so far
      pending_message_id  — Telegram message ID of a "⏳ ממתין..." message
      pending_invoice_id  — Invoice.id that's pending allocation
    """
    import json

    session = get_or_create_session(telegram_user_id, db)
    session.state = state
    session.draft_payload_json = json.dumps(draft, ensure_ascii=False)
    session.updated_at = datetime.datetime.utcnow()
    if pending_message_id is not None:
        session.pending_message_id = pending_message_id
    if pending_invoice_id is not None:
        session.pending_invoice_id = pending_invoice_id
    db.commit()


# ─────────────────────────────────────────────────────────────
# FUNCTION: clear_session_draft
# ─────────────────────────────────────────────────────────────
def clear_session_draft(telegram_user_id: str, db: Session) -> None:
    """
    Clear the draft and state from a session (after completion or cancel).
    Keeps the session record itself (for the Telegram → User link).
    """
    session = get_or_create_session(telegram_user_id, db)
    session.state = None
    session.draft_payload_json = None
    session.pending_message_id = None
    session.pending_invoice_id = None
    session.updated_at = datetime.datetime.utcnow()
    db.commit()
