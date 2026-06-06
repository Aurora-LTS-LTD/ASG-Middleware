"""
ASG Solutions — WhatsApp FSM Engine
=====================================
The brain of the WhatsApp bot. Takes a parsed inbound message
(from webhook router) and produces outbound messages by running
through a small finite state machine.

HOW IT FITS IN:
  webhook_router.py → parses Meta's JSON, calls handle_inbound()
  whatsapp_engine.py (this file) → decides what to say next
  whatsapp_meta_client.py → actually sends the messages to Meta

REAL-WORLD ANALOGY:
  Imagine a very disciplined phone operator at a tax firm.
  Every caller hears the same script, every branch is pre-written,
  every answer is a button. The operator never improvises, and
  their script-book (this file) is the only thing keeping the
  whole thing consistent across languages and flows.

STATES (stored in WhatsAppSession.state as a string):
  None / ""                     → show main menu
  NEW_INVOICE:AMOUNT            → waiting for amount
  NEW_INVOICE:CLIENT            → waiting for client pick (list)
  NEW_INVOICE:NEW_CLIENT_NAME   → waiting for typed name
  NEW_INVOICE:CONFIRM           → waiting for confirm/edit/cancel tap
  PENDING_ALLOCATION            → passive; waits for retry queue
  SETTINGS:ROOT                 → settings menu
  SETTINGS:LANG                 → choosing language
  AWAIT_PAIRING                 → unbound; waiting for LINK-code

DRAFT PAYLOAD (stored in WhatsAppSession.draft_payload_json):
  {
    "amount_net": float,
    "client_name": str,
    "invoice_number": str | None,   # set after create_draft_invoice
    "invoice_id": int | None,
  }
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import datetime
import re

from sqlalchemy.orm import Session

from aurora_shared.database import Invoice, User
from app.services import whatsapp_meta_client as wa
from app.services.invoice_service import (
    create_draft_invoice,
    finalize_invoice,
    AllocationFailedError,
)
from app.services.payment_service import get_business_balance, get_overdue_invoices
from app.services.tax_compliance import calculate_vat, check_tax_compliance
from aurora_shared.services.whatsapp_identity import (
    clear_session_draft,
    get_or_create_session,
    get_user_by_whatsapp_phone,
    load_draft,
    normalize_phone,
    save_draft_to_session,
    touch_last_inbound,
    verify_pairing_code,
)
from app.services.whatsapp_strings import (
    detect_lang_switch,
    normalize_lang,
    t,
)

# Sprint 1 ONBOARDING FSM dependencies
from aurora_shared.services.identity import (
    create_organization,
    create_invitation,
    validate_tax_id_israel,
    normalize_tax_id,
    infer_legal_structure_from_tax_id,
)
from aurora_shared.services.auth_service import hash_password


# ─────────────────────────────────────────────────────────────
# ONBOARDING FSM CONSTANTS (Sprint 1 follow-up)
# ─────────────────────────────────────────────────────────────
# Trigger keywords (any language): typing one of these from the unbound
# state launches the in-WhatsApp signup flow.
_ONBOARDING_TRIGGERS = (
    "register", "signup", "sign up", "/start", "start",
    "הרשמה", "הירשם", "התחל",
    "تسجيل", "ابدأ",
)

# Industry codes shown in the BUSINESS_TYPE list step.
_BUSINESS_TYPES = (
    "contractor", "electrician", "plumber", "hvac",
    "retail", "services", "other",
)

# Email format check (matches the same simple pattern used by
# routers/onboarding.py — kept local to avoid an import dance).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# E.164-ish phone (international format) check
_PHONE_RE = re.compile(r"^\+\d{8,15}$")


# ─────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT — called by the webhook router
# ─────────────────────────────────────────────────────────────
async def handle_inbound(parsed: dict, db: Session) -> None:
    """
    Process one inbound WhatsApp event.

    `parsed` is the normalized payload from webhook_router.parse_inbound():
      {
        "wamid":   "wamid.XXXX",
        "from":    "+9725XXXXXXX",   # E.164, with '+'
        "type":    "text" | "interactive" | "image" | "document" | "audio" | ...,
        "text":    str | None,
        "button_id": str | None,     # id of the tapped button (interactive)
        "list_id":   str | None,     # id of the tapped list row (interactive)
        "media_id":  str | None,     # Meta media id (images/documents)
      }

    This function is called AFTER the router has already:
      - verified the HMAC signature
      - dedup'd on wamid (so we know this is a fresh event)
    """
    phone = parsed.get("from")
    if not phone:
        print("[WA_ENGINE] Dropping inbound: no 'from' field")
        return
    phone = normalize_phone(phone)

    # ── 1. Touch the session (resets 24-hour window) ──
    session = get_or_create_session(phone, db)
    touch_last_inbound(session, db)

    # ── 2. Mark the inbound as read (blue-tick, non-blocking) ──
    wamid = parsed.get("wamid")
    if wamid:
        await wa.mark_message_read(wamid)

    # ── 3. Identify the user ──
    user = get_user_by_whatsapp_phone(phone, db)
    lang = normalize_lang(session.locale or (user.language_pref if user else "he"))

    # ── 4. Unbound users get either the pairing flow OR the
    #       in-WhatsApp ONBOARDING FSM (Sprint 1 follow-up).      ──
    if not user:
        # Are they mid-ONBOARDING? (session.state populated, no user yet)
        state_now = (session.state or "").upper()
        if state_now.startswith("ONBOARDING:"):
            await _route_onboarding(parsed, phone, session, state_now, lang, db)
            return
        await _handle_unbound(parsed, phone, session, lang, db)
        return

    # ── 5. Universal "menu" shortcut — type a language code to switch ──
    text = (parsed.get("text") or "").strip()
    switch = detect_lang_switch(text)
    if switch:
        session.locale = switch
        db.commit()
        await _send_menu(phone, user, switch, db)
        return

    # ── 5b. Media intercept (Sprint 2 — Receipt Box) ──────────
    # If the inbound is an image / document / PDF, send it through
    # the OCR pipeline regardless of the current FSM state. This
    # mirrors the "snap → file" UX target. Receipt review state is
    # set by the handler if user input is required (light/heavy review).
    msg_type = (parsed.get("type") or "").lower()
    has_media = bool(parsed.get("media_id")) or msg_type in ("image", "document")
    if has_media:
        try:
            await _handle_receipt_image(parsed, phone, user, lang, db)
        except Exception as e:
            print(f"[WA_ENGINE] ⚠️ Receipt handler error: {e}")
            clear_session_draft(phone, db)
            await wa.send_text(phone, t("receipt_ocr_failed", lang), db, user_id=user.id)
        return

    # ── 6. Route on session state ──
    state = (session.state or "").upper()
    try:
        if state == "NEW_INVOICE:AMOUNT":
            await _flow_invoice_amount(parsed, phone, user, lang, db)
        elif state == "NEW_INVOICE:CLIENT":
            await _flow_invoice_client(parsed, phone, user, lang, db)
        elif state == "NEW_INVOICE:NEW_CLIENT_NAME":
            await _flow_invoice_new_client_name(parsed, phone, user, lang, db)
        elif state == "NEW_INVOICE:CONFIRM":
            await _flow_invoice_confirm(parsed, phone, user, lang, db)
        elif state == "SETTINGS:ROOT":
            await _flow_settings_root(parsed, phone, user, lang, db)
        elif state == "SETTINGS:LANG":
            await _flow_settings_lang(parsed, phone, user, lang, db)
        elif state == "PENDING_ALLOCATION":
            # Passive state — the allocation_queue worker will edit back in.
            # Any inbound during this window bounces to the main menu.
            await _send_menu(phone, user, lang, db)
        # ── Sprint 2 — Receipt Review states ──
        elif state == "RECEIPT_REVIEW:LIGHT":
            await _flow_receipt_review_light(parsed, phone, user, lang, db)
        elif state == "RECEIPT_REVIEW:HEAVY":
            await _flow_receipt_review_heavy(parsed, phone, user, lang, db)
        else:
            # No active flow → interpret as a menu request.
            await _handle_menu_input(parsed, phone, user, lang, db)
    except Exception as e:
        # Never let an exception kill the webhook — log + reset to menu.
        print(f"[WA_ENGINE] ⚠️ Flow error: {e}")
        clear_session_draft(phone, db)
        await wa.send_text(phone, t("unknown_message", lang), db, user_id=user.id)
        await _send_menu(phone, user, lang, db)


# ─────────────────────────────────────────────────────────────
# UNBOUND PHONE — PAIRING FLOW
# ─────────────────────────────────────────────────────────────
async def _handle_unbound(
    parsed: dict, phone: str, session, lang: str, db: Session
) -> None:
    """
    Phone is not linked to any user. Branches:
      a) Text matches a 6-digit pairing code (LINK-XXXXXX) → bind to
         an existing dashboard user.
      b) Text matches a signup trigger keyword (Hebrew / Arabic / English) →
         enter the in-WhatsApp ONBOARDING FSM.
      c) Tapped one of the 3 quick-reply buttons we offered earlier:
         - btn:wa_signup     → enter ONBOARDING FSM
         - btn:open_web      → resend the web wizard deep-link
         - btn:link_existing → prompt for the pairing code
      d) Anything else → show the trilingual welcome + 3-button choice.
    """
    import os

    text = (parsed.get("text") or "").strip()
    button_id = parsed.get("button_id") or parsed.get("list_id") or ""

    # ── (a) Pairing code path ──
    match = re.search(r"(?i)(?:LINK[-\s]*)?(\d{6})", text)
    if match:
        code = match.group(1)
        user = verify_pairing_code(phone, code, db)
        if user:
            new_lang = normalize_lang(user.language_pref or lang)
            session.locale = new_lang
            db.commit()
            await wa.send_text(
                phone,
                t("pair_success", new_lang, email=user.email),
                db,
                user_id=user.id,
            )
            await _send_menu(phone, user, new_lang, db)
            return
        await wa.send_text(phone, t("pair_invalid", lang), db)
        return

    # ── (b) Signup trigger keyword ──
    if text.lower() in _ONBOARDING_TRIGGERS:
        await _start_wa_onboarding(phone, session, lang, db)
        return

    # ── (c) Tapped one of our prior buttons ──
    if button_id == "btn:wa_signup":
        await _start_wa_onboarding(phone, session, lang, db)
        return
    if button_id == "btn:open_web":
        await _send_web_onboarding_link(phone, lang, db)
        return
    if button_id == "btn:link_existing":
        await wa.send_text(phone, t("unbound_welcome", lang), db)
        return

    # ── (d) Default: trilingual welcome + 3-button choice ──
    await wa.send_text(phone, t("unbound_welcome", lang), db)
    await wa.send_buttons(
        phone,
        body_text=t("unbound_choice_prompt", lang),
        buttons=[
            {"id": "btn:wa_signup",      "title": t("btn_wa_signup", lang)},
            {"id": "btn:link_existing",  "title": t("btn_link_existing", lang)},
            {"id": "btn:open_web",       "title": t("btn_open_web", lang)},
        ],
        db=db,
    )


async def _send_web_onboarding_link(phone: str, lang: str, db) -> None:
    """Send the Aurora web wizard deep-link as a follow-up message."""
    import os
    onboarding_url = os.getenv("ONBOARDING_PUBLIC_URL", "https://aurora-ltd.co.il/onboarding")
    msg = {
        "he": (
            "✨ הצטרפות מלאה (כולל אימות טלפון, דוא\"ל ומסמכים):\n"
            f"{onboarding_url}\n"
            "🎁 14 ימי ניסיון חינם — ללא חיוב עד יום 15."
        ),
        "ar": (
            "✨ التسجيل الكامل (مع التحقق والوثائق):\n"
            f"{onboarding_url}\n"
            "🎁 14 يوماً تجربة مجانية — لا خصم حتى اليوم 15."
        ),
        "en": (
            "✨ Full signup (with verification & documents):\n"
            f"{onboarding_url}\n"
            "🎁 14-day free trial — no charge until day 15."
        ),
    }.get(lang, "")
    await wa.send_text(phone, msg, db)


# ─────────────────────────────────────────────────────────────
# ONBOARDING FSM — start  (Sprint 1 follow-up)
# ─────────────────────────────────────────────────────────────
async def _start_wa_onboarding(phone: str, session, lang: str, db: Session) -> None:
    """
    Bootstrap a fresh WhatsApp-native onboarding journey.
    Persists state='ONBOARDING:FIRST_NAME' and a clean draft.
    No User row yet — we create it only at CONFIRM, in one transaction
    with the Organization + Membership.
    """
    save_draft_to_session(
        phone,
        state="ONBOARDING:FIRST_NAME",
        draft={"locale": lang},
        db=db,
    )
    await wa.send_text(phone, t("onb_intro", lang), db)
    await wa.send_text(phone, t("onb_ask_first_name", lang), db)


# ─────────────────────────────────────────────────────────────
# ONBOARDING FSM — dispatcher  (no user yet, route by state)
# ─────────────────────────────────────────────────────────────
async def _route_onboarding(
    parsed: dict, phone: str, session, state: str, lang: str, db: Session
) -> None:
    """
    Dispatcher for the in-WhatsApp ONBOARDING flow. Reached when the
    session has an ONBOARDING:* state but no User is bound yet.

    Universal cancel: typing 'cancel' / 'ביטול' / 'إلغاء' at any step
    aborts the flow and resets the session.
    """
    text = (parsed.get("text") or "").strip()
    if text.lower() in ("cancel", "ביטול", "إلغاء", "stop"):
        clear_session_draft(phone, db)
        await wa.send_text(phone, t("onb_cancelled", lang), db)
        return

    try:
        if state == "ONBOARDING:FIRST_NAME":
            await _onb_first_name(parsed, phone, session, lang, db)
        elif state == "ONBOARDING:LAST_NAME":
            await _onb_last_name(parsed, phone, session, lang, db)
        elif state == "ONBOARDING:LEGAL_STRUCTURE":
            await _onb_legal_structure(parsed, phone, session, lang, db)
        elif state == "ONBOARDING:TAX_ID":
            await _onb_tax_id(parsed, phone, session, lang, db)
        elif state == "ONBOARDING:BUSINESS_NAME":
            await _onb_business_name(parsed, phone, session, lang, db)
        elif state == "ONBOARDING:BUSINESS_TYPE":
            await _onb_business_type(parsed, phone, session, lang, db)
        elif state == "ONBOARDING:INVITE_ACCOUNTANT":
            await _onb_invite_accountant(parsed, phone, session, lang, db)
        elif state == "ONBOARDING:ACCOUNTANT_CONTACT":
            await _onb_accountant_contact(parsed, phone, session, lang, db)
        elif state == "ONBOARDING:CONFIRM":
            await _onb_confirm(parsed, phone, session, lang, db)
        else:
            # Unknown ONBOARDING state — reset
            clear_session_draft(phone, db)
            await wa.send_text(phone, t("unknown_message", lang), db)
    except Exception as e:
        # Never crash the webhook — log and reset gracefully
        print(f"[WA_ONB] ⚠️ Step error in {state}: {e}")
        clear_session_draft(phone, db)
        await wa.send_text(phone, t("onb_failed", lang, error=str(e)[:120]), db)


# ─────────────────────────────────────────────────────────────
# ONBOARDING FSM — individual steps
# ─────────────────────────────────────────────────────────────
async def _onb_first_name(parsed, phone, session, lang, db):
    text = (parsed.get("text") or "").strip()
    if len(text) < 2:
        await wa.send_text(phone, t("onb_bad_first_name", lang), db)
        return
    draft = load_draft(session)
    draft["first_name"] = text[:80]
    save_draft_to_session(phone, state="ONBOARDING:LAST_NAME", draft=draft, db=db)
    await wa.send_text(phone, t("onb_ask_last_name", lang), db)


async def _onb_last_name(parsed, phone, session, lang, db):
    text = (parsed.get("text") or "").strip()
    if len(text) < 2:
        await wa.send_text(phone, t("onb_bad_last_name", lang), db)
        return
    draft = load_draft(session)
    draft["last_name"] = text[:80]
    save_draft_to_session(phone, state="ONBOARDING:LEGAL_STRUCTURE", draft=draft, db=db)
    await wa.send_buttons(
        phone,
        body_text=t("onb_ask_legal_structure", lang),
        buttons=[
            {"id": "onb:legal:osek_morshe", "title": t("btn_legal_osek_morshe", lang)},
            {"id": "onb:legal:osek_patur",  "title": t("btn_legal_osek_patur", lang)},
            {"id": "onb:legal:chevra_baam", "title": t("btn_legal_chevra_baam", lang)},
        ],
        db=db,
    )


async def _onb_legal_structure(parsed, phone, session, lang, db):
    """Accept the legal_structure tap. Falls back to text matching if buttons
    fail to render in some clients."""
    bid = (parsed.get("button_id") or "").strip()
    text = (parsed.get("text") or "").strip().lower()

    legal_structure = None
    if bid.startswith("onb:legal:"):
        legal_structure = bid.split("onb:legal:", 1)[1]
    elif "מורשה" in text or "morshe" in text or "authorized" in text:
        legal_structure = "osek_morshe"
    elif "פטור" in text or "patur" in text or "exempt" in text:
        legal_structure = "osek_patur"
    elif "חברה" in text or "ltd" in text or "baam" in text or "ש.م" in text:
        legal_structure = "chevra_baam"

    if legal_structure not in ("osek_morshe", "osek_patur", "chevra_baam"):
        # Re-prompt with buttons
        await wa.send_buttons(
            phone,
            body_text=t("onb_ask_legal_structure", lang),
            buttons=[
                {"id": "onb:legal:osek_morshe", "title": t("btn_legal_osek_morshe", lang)},
                {"id": "onb:legal:osek_patur",  "title": t("btn_legal_osek_patur", lang)},
                {"id": "onb:legal:chevra_baam", "title": t("btn_legal_chevra_baam", lang)},
            ],
            db=db,
        )
        return

    draft = load_draft(session)
    draft["legal_structure"] = legal_structure
    save_draft_to_session(phone, state="ONBOARDING:TAX_ID", draft=draft, db=db)
    await wa.send_text(phone, t("onb_ask_tax_id", lang), db)


async def _onb_tax_id(parsed, phone, session, lang, db):
    text = (parsed.get("text") or "").strip()
    normalized = normalize_tax_id(text)
    if not validate_tax_id_israel(normalized):
        await wa.send_text(phone, t("onb_bad_tax_id", lang), db)
        return
    draft = load_draft(session)
    draft["tax_id"] = normalized

    # Sanity check: warn if the user-chosen legal structure disagrees
    # with the tax-id-inferred one. We accept anyway (user knows best),
    # but log it for the audit trail.
    inferred = infer_legal_structure_from_tax_id(normalized)
    if inferred and inferred != draft.get("legal_structure"):
        print(
            f"[WA_ONB] ℹ️ tax_id hint={inferred} differs from chosen "
            f"{draft.get('legal_structure')} — accepting"
        )

    save_draft_to_session(phone, state="ONBOARDING:BUSINESS_NAME", draft=draft, db=db)
    await wa.send_text(phone, t("onb_ask_business_name", lang), db)


async def _onb_business_name(parsed, phone, session, lang, db):
    text = (parsed.get("text") or "").strip()
    if len(text) < 3:
        await wa.send_text(phone, t("onb_bad_business_name", lang), db)
        return
    draft = load_draft(session)
    draft["display_name"] = text[:120]
    save_draft_to_session(phone, state="ONBOARDING:BUSINESS_TYPE", draft=draft, db=db)

    # List Message: 7 industry rows
    rows = [
        {"id": f"onb:btype:{code}", "title": t(f"btn_btype_{code}", lang)[:24]}
        for code in _BUSINESS_TYPES
    ]
    await wa.send_list(
        phone,
        body_text=t("onb_ask_business_type", lang),
        button_label=t("btn_btype_list", lang),
        sections=[{"title": t("btn_btype_list", lang), "rows": rows}],
        db=db,
    )


async def _onb_business_type(parsed, phone, session, lang, db):
    """Accept the business_type list-row tap; fall back to keyword text."""
    list_id = (parsed.get("list_id") or parsed.get("button_id") or "").strip()
    text = (parsed.get("text") or "").strip().lower()

    btype = None
    if list_id.startswith("onb:btype:"):
        btype = list_id.split("onb:btype:", 1)[1]
    else:
        for code in _BUSINESS_TYPES:
            if code in text:
                btype = code
                break

    if btype not in _BUSINESS_TYPES:
        # Re-send the list
        rows = [
            {"id": f"onb:btype:{code}", "title": t(f"btn_btype_{code}", lang)[:24]}
            for code in _BUSINESS_TYPES
        ]
        await wa.send_list(
            phone,
            body_text=t("onb_ask_business_type", lang),
            button_label=t("btn_btype_list", lang),
            sections=[{"title": t("btn_btype_list", lang), "rows": rows}],
            db=db,
        )
        return

    draft = load_draft(session)
    draft["business_type"] = btype
    save_draft_to_session(phone, state="ONBOARDING:INVITE_ACCOUNTANT", draft=draft, db=db)
    await wa.send_buttons(
        phone,
        body_text=t("onb_ask_invite_accountant", lang),
        buttons=[
            {"id": "onb:invite:yes",   "title": t("btn_invite_yes", lang)},
            {"id": "onb:invite:later", "title": t("btn_invite_later", lang)},
        ],
        db=db,
    )


async def _onb_invite_accountant(parsed, phone, session, lang, db):
    bid = (parsed.get("button_id") or "").strip()
    text = (parsed.get("text") or "").strip().lower()

    if bid == "onb:invite:yes" or text in ("yes", "כן", "نعم"):
        draft = load_draft(session)
        draft["wants_accountant"] = True
        save_draft_to_session(phone, state="ONBOARDING:ACCOUNTANT_CONTACT", draft=draft, db=db)
        await wa.send_text(phone, t("onb_ask_accountant_contact", lang), db)
        return

    if bid == "onb:invite:later" or text in ("no", "לא", "later", "بعدا", "لا"):
        draft = load_draft(session)
        draft["wants_accountant"] = False
        await _onb_send_confirm(phone, draft, lang, db)
        save_draft_to_session(phone, state="ONBOARDING:CONFIRM", draft=draft, db=db)
        return

    # Re-prompt
    await wa.send_buttons(
        phone,
        body_text=t("onb_ask_invite_accountant", lang),
        buttons=[
            {"id": "onb:invite:yes",   "title": t("btn_invite_yes", lang)},
            {"id": "onb:invite:later", "title": t("btn_invite_later", lang)},
        ],
        db=db,
    )


async def _onb_accountant_contact(parsed, phone, session, lang, db):
    text = (parsed.get("text") or "").strip()
    is_email = bool(_EMAIL_RE.match(text))
    is_phone = bool(_PHONE_RE.match(text))
    if not (is_email or is_phone):
        await wa.send_text(phone, t("onb_bad_contact", lang), db)
        return

    draft = load_draft(session)
    draft["accountant_contact"] = text
    draft["accountant_contact_is_email"] = is_email
    save_draft_to_session(phone, state="ONBOARDING:CONFIRM", draft=draft, db=db)
    await wa.send_text(phone, t("onb_accountant_invited", lang), db)
    await _onb_send_confirm(phone, draft, lang, db)


async def _onb_send_confirm(phone, draft, lang, db):
    """Render the final summary card and confirm/edit/cancel buttons."""
    legal_label = {
        "osek_morshe": t("btn_legal_osek_morshe", lang),
        "osek_patur":  t("btn_legal_osek_patur", lang),
        "chevra_baam": t("btn_legal_chevra_baam", lang),
    }.get(draft.get("legal_structure", ""), draft.get("legal_structure", ""))

    btype_label = t(f"btn_btype_{draft.get('business_type', 'other')}", lang)

    if draft.get("wants_accountant") and draft.get("accountant_contact"):
        accountant_line = t(
            "onb_accountant_line_yes",
            lang,
            contact=draft["accountant_contact"],
        )
    else:
        accountant_line = t("onb_accountant_line_none", lang)

    summary = t(
        "onb_confirm_card",
        lang,
        first_name=draft.get("first_name", ""),
        last_name=draft.get("last_name", ""),
        display_name=draft.get("display_name", ""),
        legal_structure_label=legal_label,
        tax_id=draft.get("tax_id", ""),
        business_type_label=btype_label,
        accountant_line=accountant_line,
    )
    await wa.send_buttons(
        phone,
        body_text=summary,
        buttons=[
            {"id": "onb:confirm",  "title": t("btn_onb_confirm", lang)},
            {"id": "onb:edit",     "title": t("btn_onb_edit", lang)},
            {"id": "onb:cancel",   "title": t("btn_onb_cancel", lang)},
        ],
        db=db,
    )


async def _onb_confirm(parsed, phone, session, lang, db):
    bid = (parsed.get("button_id") or "").strip()
    text = (parsed.get("text") or "").strip().lower()

    if bid == "onb:cancel" or text in ("cancel", "ביטול", "إلغاء"):
        clear_session_draft(phone, db)
        await wa.send_text(phone, t("onb_cancelled", lang), db)
        return

    if bid == "onb:edit" or text in ("edit", "ערוך", "تعديل"):
        # Restart from FIRST_NAME but keep draft so the user re-types
        # only what they want to change.
        draft = load_draft(session)
        save_draft_to_session(phone, state="ONBOARDING:FIRST_NAME", draft=draft, db=db)
        await wa.send_text(phone, t("onb_ask_first_name", lang), db)
        return

    if bid == "onb:confirm" or text in ("confirm", "yes", "כן", "אישור", "نعم"):
        await _finalize_wa_onboarding(phone, session, lang, db)
        return

    # Unrecognized — re-render the confirm card
    draft = load_draft(session)
    await _onb_send_confirm(phone, draft, lang, db)


# ─────────────────────────────────────────────────────────────
# ONBOARDING FSM — finalize (atomic User + Org + Membership)
# ─────────────────────────────────────────────────────────────
async def _finalize_wa_onboarding(phone, session, lang, db):
    """
    Final commit. One DB transaction:
      1. Create the User (synthetic email derived from phone, random
         password, whatsapp_phone_e164=phone, role='business_owner').
      2. Call create_organization() — also creates legacy Business +
         Membership(role='owner', is_primary=True) and dual-writes
         User.business_id (expand/contract migration).
      3. If the user opted to invite an accountant, queue an Invitation
         row (the WhatsApp-template send happens later, post Meta-approval).
      4. Bind the WhatsApp session: session.user_id = user.id.
      5. Mark user.onboarding_status='active'.
      6. Send success message + main menu.
    """
    import uuid as _u

    draft = load_draft(session)

    # ── Derive the synthetic email (one-to-one with phone) ──
    # Format: wa{phone-with-+stripped}@aurora-ltd.co.il
    # The user can later set a real email via web-onboarding's profile
    # page (when shipped) without changing their phone-based identity.
    phone_clean = phone.lstrip("+")
    synthetic_email = f"wa{phone_clean}@aurora-ltd.co.il"

    # If somehow this email is taken (very unlikely — same phone re-running
    # post-cancel), append a uuid suffix so we never collide.
    from aurora_shared.database import User as _UserModel
    if db.query(_UserModel).filter(_UserModel.email == synthetic_email).first():
        synthetic_email = f"wa{phone_clean}-{_u.uuid4().hex[:6]}@aurora-ltd.co.il"

    await wa.send_text(phone, t("onb_creating", lang), db)

    # 1. User
    user = User(
        email=synthetic_email,
        password_hash=hash_password(_u.uuid4().hex),  # random unguessable
        full_name=f"{draft.get('first_name', '')} {draft.get('last_name', '')}".strip(),
        first_name=draft.get("first_name"),
        last_name=draft.get("last_name"),
        role="business_owner",
        is_active=True,
        language_pref=normalize_lang(draft.get("locale") or lang),
        whatsapp_phone_e164=phone,
        onboarding_status="active",
        # Phone is implicitly verified by the fact they messaged us from it
        phone_verified_at=datetime.datetime.utcnow(),
    )
    db.add(user)
    db.flush()

    # 2. Organization (+ legacy Business + Membership via dual-write)
    org = create_organization(
        display_name=draft["display_name"],
        legal_structure=draft["legal_structure"],
        tax_id=draft["tax_id"],
        owner_user_id=user.id,
        db=db,
        business_phone=phone,
        industry_code=draft.get("business_type"),
    )

    # 3. Optional accountant invitation
    if draft.get("wants_accountant") and draft.get("accountant_contact"):
        contact = draft["accountant_contact"]
        is_email = draft.get("accountant_contact_is_email", False)
        try:
            create_invitation(
                organization_id=org.id,
                invited_by_user_id=user.id,
                role="accountant",
                target_email=contact if is_email else None,
                target_phone_e164=contact if not is_email else None,
                db=db,
            )
        except ValueError as e:
            # Non-fatal — the org is created either way; log and continue.
            print(f"[WA_ONB] ⚠️ Failed to queue accountant invitation: {e}")

    # 4 + 5. Bind the WhatsApp session and clear the FSM
    session.user_id = user.id
    session.business_id = user.business_id
    session.state = None
    session.draft_payload_json = None
    db.commit()

    # 6. Welcome + main menu
    await wa.send_text(
        phone,
        t("onb_success", lang, display_name=org.display_name),
        db,
        user_id=user.id,
    )
    await _send_menu(phone, user, lang, db)


# ─────────────────────────────────────────────────────────────
# MAIN MENU
# ─────────────────────────────────────────────────────────────
async def _send_menu(phone: str, user: User, lang: str, db: Session) -> None:
    """
    Render the main menu. Meta allows max 3 buttons per interactive
    message, so we send TWO interactive-button messages back-to-back:
      Row 1: 🧾 Invoice · 📊 Balance · ⚠️ Overdue
      Row 2: 💰 Payment · 📷 Receipt · ⚙️ Settings
    """
    # Clear any stale state — pressing the menu always resets the flow.
    clear_session_draft(phone, db)

    # ── Header: live stats line ──
    try:
        balance = (
            get_business_balance(db, user.business_id) if user.business_id else {}
        )
        outstanding = balance.get("total_outstanding", 0) or 0
        count = balance.get("invoice_count", 0) or 0
    except Exception:
        outstanding = 0
        count = 0

    first_name = (user.full_name or "").split()[0] if user.full_name else ""
    header = t("menu_header", lang, name=first_name, count=count, outstanding=outstanding)
    prompt = t("menu_prompt", lang)

    # ── Row 1 — three primary actions ──
    await wa.send_buttons(
        phone,
        body_text=f"{header}\n\n{prompt}",
        buttons=[
            {"id": "menu:new_invoice", "title": t("btn_new_invoice", lang)},
            {"id": "menu:balance",     "title": t("btn_balance", lang)},
            {"id": "menu:overdue",     "title": t("btn_overdue", lang)},
        ],
        db=db,
        user_id=user.id,
    )

    # ── Row 2 — three secondary actions ──
    await wa.send_buttons(
        phone,
        body_text="…",
        buttons=[
            {"id": "menu:record_payment", "title": t("btn_record_pay", lang)},
            {"id": "menu:receipt_box",    "title": t("btn_receipt_box", lang)},
            {"id": "menu:settings",       "title": t("btn_settings", lang)},
        ],
        db=db,
        user_id=user.id,
    )


# ─────────────────────────────────────────────────────────────
# MENU INPUT ROUTING
# ─────────────────────────────────────────────────────────────
async def _handle_menu_input(
    parsed: dict, phone: str, user: User, lang: str, db: Session
) -> None:
    """
    User is at the top level (no active flow). Figure out what they
    tapped or typed, and hand off to the right sub-flow.
    """
    btn = parsed.get("button_id") or parsed.get("list_id")
    msg_type = parsed.get("type")

    # ── Image or document → Receipt Box ──
    if msg_type in ("image", "document"):
        await _flow_receipt_box(parsed, phone, user, lang, db)
        return

    # ── Interactive button/list routes ──
    if btn == "menu:new_invoice":
        await _start_new_invoice(phone, user, lang, db)
    elif btn == "menu:balance":
        await _show_balance(phone, user, lang, db)
    elif btn == "menu:overdue":
        await _show_overdue(phone, user, lang, db)
    elif btn == "menu:record_payment":
        await wa.send_text(
            phone, t("record_payment_soon", lang), db, user_id=user.id
        )
        await _send_menu(phone, user, lang, db)
    elif btn == "menu:receipt_box":
        # Prompt user to just send a photo.
        await wa.send_text(phone, t("receipt_received", lang), db, user_id=user.id)
    elif btn == "menu:settings":
        await _open_settings(phone, user, lang, db)
    else:
        # Unrecognized text at the menu — show the menu again.
        await _send_menu(phone, user, lang, db)


# ─────────────────────────────────────────────────────────────
# NEW INVOICE FLOW — 3 taps + 1 amount
# ─────────────────────────────────────────────────────────────
async def _start_new_invoice(
    phone: str, user: User, lang: str, db: Session
) -> None:
    """Kick off the invoice flow — ask for the amount."""
    if not user.business_id:
        await wa.send_text(
            phone,
            "❌ No business linked to your account.",
            db,
            user_id=user.id,
        )
        return

    save_draft_to_session(phone, "NEW_INVOICE:AMOUNT", {}, db)
    await wa.send_text(phone, t("ask_amount", lang), db, user_id=user.id)


async def _flow_invoice_amount(
    parsed: dict, phone: str, user: User, lang: str, db: Session
) -> None:
    """Validate the amount; if good, advance to client picker."""
    raw = (parsed.get("text") or "").strip().replace(",", "")

    match = re.fullmatch(r"(\d{1,10})(\.\d{1,2})?", raw)
    if not match:
        await wa.send_text(phone, t("bad_amount", lang), db, user_id=user.id)
        return

    amount_net = float(raw)
    if amount_net > 10_000_000:
        await wa.send_text(phone, t("too_big", lang), db, user_id=user.id)
        return

    # Save amount to draft and advance
    draft = {"amount_net": amount_net}
    save_draft_to_session(phone, "NEW_INVOICE:CLIENT", draft, db)

    # ── Show VAT coach line ──
    vat = calculate_vat(amount_net)
    compliance = check_tax_compliance(amount_net)
    rate_pct = int(vat["vat_rate"] * 100)
    vat_line = t(
        "vat_line",
        lang,
        net=vat["amount_net"],
        total=vat["amount_total"],
        rate=rate_pct,
    )
    badge = t(
        "vat_yellow" if compliance["requires_allocation"] else "vat_green",
        lang,
    )
    await wa.send_text(phone, f"{vat_line}\n{badge}", db, user_id=user.id)

    # ── Show client picker: last 5 unique clients + "new client" row ──
    await _send_client_picker(phone, user, lang, db)


def _recent_clients(db: Session, business_id: int, limit: int = 5) -> list[str]:
    """Up to `limit` most recent unique beneficiary names for this business."""
    if not business_id:
        return []
    rows = (
        db.query(Invoice.beneficiary_name)
        .filter(
            Invoice.business_id == business_id,
            Invoice.beneficiary_name.isnot(None),
            Invoice.beneficiary_name != "",
        )
        .order_by(Invoice.created_at.desc())
        .limit(30)
        .all()
    )
    seen: set[str] = set()
    out: list[str] = []
    for (name,) in rows:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
        if len(out) >= limit:
            break
    return out


async def _send_client_picker(
    phone: str, user: User, lang: str, db: Session
) -> None:
    """Send a List Message with recent clients + a '+ new client' row."""
    recent = _recent_clients(db, user.business_id, limit=5)

    rows = []
    for i, name in enumerate(recent):
        rows.append(
            {
                "id": f"inv:client:{i}",
                "title": name[:24],   # Meta hard limit
            }
        )
    # Always include "new client" as the last row
    rows.append(
        {
            "id": "inv:client:new",
            "title": t("btn_new_client", lang)[:24],
        }
    )

    # Stash the recent list so the picker callback can look up by index
    draft = load_draft(
        get_or_create_session(phone, db)
    )
    draft["recent_clients"] = recent
    save_draft_to_session(phone, "NEW_INVOICE:CLIENT", draft, db)

    await wa.send_list(
        phone,
        body_text=t("ask_client", lang),
        button_label=t("btn_new_client", lang)[:20],
        sections=[{"title": t("ask_client", lang), "rows": rows}],
        db=db,
        user_id=user.id,
    )


async def _flow_invoice_client(
    parsed: dict, phone: str, user: User, lang: str, db: Session
) -> None:
    """Handle the list-row tap from the client picker."""
    choice = parsed.get("list_id") or parsed.get("button_id") or ""

    session = get_or_create_session(phone, db)
    draft = load_draft(session)
    recent = draft.get("recent_clients", [])

    if choice == "inv:client:new":
        save_draft_to_session(phone, "NEW_INVOICE:NEW_CLIENT_NAME", draft, db)
        await wa.send_text(
            phone, t("ask_new_client_name", lang), db, user_id=user.id
        )
        return

    m = re.fullmatch(r"inv:client:(\d+)", choice)
    if m:
        idx = int(m.group(1))
        if 0 <= idx < len(recent):
            draft["client_name"] = recent[idx]
            await _advance_to_confirm(phone, user, lang, draft, db)
            return

    # Unrecognized — re-send picker
    await _send_client_picker(phone, user, lang, db)


async def _flow_invoice_new_client_name(
    parsed: dict, phone: str, user: User, lang: str, db: Session
) -> None:
    """User typed a new client name."""
    name = (parsed.get("text") or "").strip()
    if len(name) < 2 or len(name) > 80:
        await wa.send_text(phone, t("bad_name", lang), db, user_id=user.id)
        return

    session = get_or_create_session(phone, db)
    draft = load_draft(session)
    draft["client_name"] = name
    await _advance_to_confirm(phone, user, lang, draft, db)


async def _advance_to_confirm(
    phone: str, user: User, lang: str, draft: dict, db: Session
) -> None:
    """Show the confirmation card with VAT Coach + confirm/edit/cancel."""
    amount_net = float(draft.get("amount_net") or 0)
    vat = calculate_vat(amount_net)
    compliance = check_tax_compliance(amount_net)

    threshold_badge = t(
        "vat_yellow" if compliance["requires_allocation"] else "vat_green",
        lang,
    )
    card = t(
        "confirm_card",
        lang,
        net=vat["amount_net"],
        vat=vat["vat_amount"],
        total=vat["amount_total"],
        client=draft.get("client_name", "?"),
        threshold_badge=threshold_badge,
    )

    save_draft_to_session(phone, "NEW_INVOICE:CONFIRM", draft, db)

    await wa.send_buttons(
        phone,
        body_text=card,
        buttons=[
            {"id": "inv:confirm", "title": t("btn_confirm_send", lang)},
            {"id": "inv:edit",    "title": t("btn_edit", lang)},
            {"id": "inv:cancel",  "title": t("btn_cancel", lang)},
        ],
        db=db,
        user_id=user.id,
    )


async def _flow_invoice_confirm(
    parsed: dict, phone: str, user: User, lang: str, db: Session
) -> None:
    """User tapped confirm / edit / cancel on the confirmation card."""
    btn = parsed.get("button_id") or ""

    if btn == "inv:cancel":
        clear_session_draft(phone, db)
        await wa.send_text(phone, t("cancelled", lang), db, user_id=user.id)
        await _send_menu(phone, user, lang, db)
        return

    if btn == "inv:edit":
        # Drop back to amount entry; keep client name if user had one
        session = get_or_create_session(phone, db)
        draft = load_draft(session)
        draft.pop("amount_net", None)  # force re-entry
        save_draft_to_session(phone, "NEW_INVOICE:AMOUNT", draft, db)
        await wa.send_text(phone, t("ask_amount", lang), db, user_id=user.id)
        return

    if btn != "inv:confirm":
        # Unknown tap — just re-send the card
        session = get_or_create_session(phone, db)
        draft = load_draft(session)
        await _advance_to_confirm(phone, user, lang, draft, db)
        return

    # ── Confirm — actually create the invoice ──
    session = get_or_create_session(phone, db)
    draft = load_draft(session)
    amount_net = float(draft.get("amount_net") or 0)
    client_name = draft.get("client_name") or "Client"

    # Placeholder so the user sees immediate feedback
    await wa.send_text(phone, t("preparing_pdf", lang), db, user_id=user.id)

    # 1) create draft row
    invoice = create_draft_invoice(
        db=db,
        business_id=user.business_id,
        beneficiary_name=client_name,
        amount_net=amount_net,
    )
    invoice_id = invoice["id"]
    requires_alloc = bool(invoice.get("requires_allocation"))

    # 2) try to finalize (may raise AllocationFailedError)
    try:
        result = await finalize_invoice(
            db=db, invoice_id=invoice_id, lang=lang, actor_label="whatsapp_bot"
        )
    except AllocationFailedError:
        # Queue retry — tell user we're waiting.
        inv_row = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if inv_row:
            inv_row.status = "pending_allocation"
            inv_row.allocation_status = "retry_pending"
            inv_row.allocation_retry_count = 1
            inv_row.allocation_next_retry_at = (
                datetime.datetime.utcnow() + datetime.timedelta(seconds=30)
            )
            db.commit()

        # Park the session in PENDING_ALLOCATION so we know which invoice to
        # announce when the retry lands.
        save_draft_to_session(
            phone,
            state="PENDING_ALLOCATION",
            draft={"invoice_id": invoice_id},
            db=db,
            pending_invoice_id=invoice_id,
        )
        await wa.send_text(
            phone, t("pending_allocation", lang), db, user_id=user.id
        )
        return

    # 3) success — deliver the PDF caption + menu
    clear_session_draft(phone, db)
    caption = t(
        "invoice_caption",
        lang,
        invoice_number=result.get("invoice_number", "?"),
        total=result.get("amount_total", 0),
    )

    # Send the PDF link if the pdf_url is already a full URL; otherwise
    # fall back to a text confirmation. (In production the pdf_url would
    # be a publicly-reachable HTTPS link — GCP signed URL or CDN.)
    pdf_url = result.get("pdf_url")
    if pdf_url and pdf_url.startswith("http"):
        await wa.send_document(
            phone,
            document_link=pdf_url,
            filename=f"{result.get('invoice_number', 'invoice')}.pdf",
            caption=caption,
            db=db,
            user_id=user.id,
        )
    else:
        # Dev mode — no public URL. Still confirm success by text.
        await wa.send_text(phone, caption, db, user_id=user.id)

    await _send_menu(phone, user, lang, db)


# ─────────────────────────────────────────────────────────────
# BALANCE / OVERDUE
# ─────────────────────────────────────────────────────────────
async def _show_balance(
    phone: str, user: User, lang: str, db: Session
) -> None:
    if not user.business_id:
        await wa.send_text(phone, "❌ No business linked.", db, user_id=user.id)
        return
    balance = get_business_balance(db, user.business_id)
    overdue = get_overdue_invoices(db, user.business_id)
    msg = t(
        "balance_summary",
        lang,
        outstanding=balance.get("total_outstanding", 0) or 0,
        open_count=balance.get("invoice_count", 0) or 0,
        overdue_count=len(overdue),
    )
    await wa.send_text(phone, msg, db, user_id=user.id)


async def _show_overdue(
    phone: str, user: User, lang: str, db: Session
) -> None:
    if not user.business_id:
        await wa.send_text(phone, "❌ No business linked.", db, user_id=user.id)
        return
    overdue = get_overdue_invoices(db, user.business_id)
    if not overdue:
        await wa.send_text(phone, t("overdue_none", lang), db, user_id=user.id)
        return
    # Cap at 8 rows to avoid a wall of text
    lines = []
    for inv in overdue[:8]:
        lines.append(
            t(
                "overdue_row",
                lang,
                invoice_number=inv.get("invoice_number", "?"),
                total=inv.get("amount_total", 0) or 0,
                days=inv.get("days_overdue", 0) or 0,
                client=(inv.get("beneficiary_name") or "?")[:20],
            )
        )
    if len(overdue) > 8:
        lines.append(f"…+{len(overdue) - 8}")
    await wa.send_text(phone, "\n".join(lines), db, user_id=user.id)


# ─────────────────────────────────────────────────────────────
# SETTINGS (language + digest + unlink)
# ─────────────────────────────────────────────────────────────
async def _open_settings(
    phone: str, user: User, lang: str, db: Session
) -> None:
    save_draft_to_session(phone, "SETTINGS:ROOT", {}, db)

    digest_on = bool(getattr(user, "morning_digest_enabled", False))
    digest_label = t(
        "btn_digest_on" if digest_on else "btn_digest_off", lang
    )

    await wa.send_buttons(
        phone,
        body_text=t("settings_header", lang),
        buttons=[
            {"id": "set:lang",   "title": t("btn_lang", lang)},
            {"id": "set:digest", "title": digest_label},
            {"id": "set:unlink", "title": t("btn_unlink", lang)},
        ],
        db=db,
        user_id=user.id,
    )


async def _flow_settings_root(
    parsed: dict, phone: str, user: User, lang: str, db: Session
) -> None:
    btn = parsed.get("button_id") or ""

    if btn == "set:lang":
        save_draft_to_session(phone, "SETTINGS:LANG", {}, db)
        await wa.send_buttons(
            phone,
            body_text=t("ask_lang", lang),
            buttons=[
                {"id": "set:lang:he", "title": t("btn_lang_he", lang)},
                {"id": "set:lang:ar", "title": t("btn_lang_ar", lang)},
                {"id": "set:lang:en", "title": t("btn_lang_en", lang)},
            ],
            db=db,
            user_id=user.id,
        )
        return

    if btn == "set:digest":
        user.morning_digest_enabled = not bool(
            getattr(user, "morning_digest_enabled", False)
        )
        db.commit()
        await wa.send_text(
            phone, t("digest_toggled", lang), db, user_id=user.id
        )
        await _open_settings(phone, user, lang, db)
        return

    if btn == "set:unlink":
        user.whatsapp_phone_e164 = None
        session = get_or_create_session(phone, db)
        session.user_id = None
        session.business_id = None
        session.state = None
        session.draft_payload_json = None
        db.commit()
        await wa.send_text(phone, t("unlinked", lang), db)
        return

    # Anything else — reshow settings
    await _open_settings(phone, user, lang, db)


async def _flow_settings_lang(
    parsed: dict, phone: str, user: User, lang: str, db: Session
) -> None:
    btn = parsed.get("button_id") or ""
    m = re.fullmatch(r"set:lang:(he|ar|en)", btn)
    if not m:
        await _open_settings(phone, user, lang, db)
        return

    new_lang = m.group(1)
    session = get_or_create_session(phone, db)
    session.locale = new_lang
    user.language_pref = new_lang
    db.commit()

    await wa.send_text(
        phone, t("lang_set", new_lang), db, user_id=user.id
    )
    await _send_menu(phone, user, new_lang, db)


# ═══════════════════════════════════════════════════════════
# SPRINT 2 — RECEIPT BOX (Document AI OCR pipeline)
# ═══════════════════════════════════════════════════════════
# `_flow_receipt_box` (called from menu) and `_handle_receipt_image`
# (called when ANY inbound message carries an image/document) both
# route into the same OCR pipeline. The pipeline returns one of five
# outcomes; this function decides what to say next.
#
# State machine for review:
#   AUTO_APPROVE   → no FSM state set; user gets "✓ saved" card
#   REVIEW_LIGHT   → state=RECEIPT_REVIEW:LIGHT; user taps ✓/✏️/🗑
#   REVIEW_HEAVY   → state=RECEIPT_REVIEW:HEAVY; user types the amount
#   DLP_QUARANTINE → no state; user gets a friendly rejection
#   OCR_FAILURE    → no state; user gets "couldn't read it, try again"

# Lazy imports — receipts pipeline pulls in DB models. Keep at runtime.
def _import_receipts():
    from app.services.receipts import (
        process_receipt,
        ReceiptOutcomeStatus,
        ReceiptRoute,
        confirm_expense,
        reject_expense,
    )
    return process_receipt, ReceiptOutcomeStatus, ReceiptRoute, confirm_expense, reject_expense


async def _flow_receipt_box(parsed: dict, phone: str, user: User, lang: str, db: Session) -> None:
    """Menu-entry point: 'menu:receipt_box' tap → ask the user to send an image."""
    await wa.send_text(
        phone,
        {
            "he": "📷 שלח תמונה של הקבלה — אני אקרא את הסכום והתאריך אוטומטית.",
            "ar": "📷 أرسل صورة الفاتورة — سأقرأ المبلغ والتاريخ تلقائياً.",
            "en": "📷 Send a photo of the receipt — I'll read the amount and date.",
        }.get(lang, ""),
        db,
        user_id=user.id,
    )


async def _handle_receipt_image(parsed: dict, phone: str, user: User, lang: str, db: Session) -> None:
    """
    Inbound image/document handler. Drives the full OCR pipeline:
      1. Acknowledge ("got it, processing")
      2. Download media bytes from Meta
      3. process_receipt() — pipeline does dedup/DLP/upload/OCR/persist
      4. Branch on outcome.route → send the right reply
    """
    process_receipt, ReceiptOutcomeStatus, ReceiptRoute, *_ = _import_receipts()

    if not user.business_id:
        # Edge case: paired user with no Business yet (shouldn't happen
        # post-onboarding, but defensive). Tell them to complete signup.
        await wa.send_text(phone, t("unknown_message", lang), db, user_id=user.id)
        return

    # Resolve the user's primary organization. We use the legacy Business
    # paired Organization (post-S1.8 dual-write keeps these in sync).
    from aurora_shared.database import Organization
    org = db.query(Organization).filter(Organization.legacy_business_id == user.business_id).first()
    if not org:
        # Last-ditch backfill via the Sprint 1.8 helper
        from aurora_shared.services.identity import get_or_create_organization_for_business
        org = get_or_create_organization_for_business(user.business_id, db)
        db.commit()

    media_id = parsed.get("media_id")
    if not media_id:
        await wa.send_text(phone, t("receipt_unable_to_download", lang), db, user_id=user.id)
        return

    # 1. Ack
    await wa.send_text(phone, t("receipt_received", lang), db, user_id=user.id)

    # 2. Download from Meta (or fail gracefully)
    try:
        result = await wa.download_media(media_id)
    except Exception as e:
        print(f"[WA_ENGINE] receipt download error: {e}")
        result = None
    if not result:
        await wa.send_text(phone, t("receipt_unable_to_download", lang), db, user_id=user.id)
        return
    image_bytes, mime_type = result

    # 3. Pipeline
    outcome = process_receipt(
        organization_id=org.id,
        user_id=user.id,
        mime_type=mime_type or "image/jpeg",
        image_bytes=image_bytes,
        db=db,
        source="whatsapp",
        source_message_id=parsed.get("wamid"),
    )

    # 4. Branch
    await _send_receipt_reply(outcome, phone, user, lang, db)


async def _send_receipt_reply(outcome, phone: str, user: User, lang: str, db: Session) -> None:
    """Render and send the appropriate WhatsApp message for an outcome."""
    _, ReceiptOutcomeStatus, ReceiptRoute, *_ = _import_receipts()

    # ── Quarantine ──
    if outcome.status == ReceiptOutcomeStatus.QUARANTINED:
        await wa.send_text(phone, t("receipt_dlp_rejected", lang), db, user_id=user.id)
        return

    # ── OCR failure ──
    if outcome.status == ReceiptOutcomeStatus.OCR_FAILED:
        await wa.send_text(phone, t("receipt_ocr_failed", lang), db, user_id=user.id)
        return

    # ── Duplicate ──
    if outcome.status == ReceiptOutcomeStatus.DUPLICATE:
        await wa.send_text(phone, t("receipt_duplicate", lang), db, user_id=user.id)
        return

    # ── Auto-approve (high confidence) ──
    if outcome.route == ReceiptRoute.AUTO_APPROVE:
        await _render_receipt_card(outcome, phone, user, lang, db, auto_approve=True)
        # No FSM state; user can ask for a fix later but otherwise it's done
        clear_session_draft(phone, db)
        return

    # ── Review-light (mid confidence) ──
    if outcome.route == ReceiptRoute.REVIEW_LIGHT:
        await _render_receipt_card(outcome, phone, user, lang, db, auto_approve=False, conf_label="mid")
        save_draft_to_session(
            phone,
            state="RECEIPT_REVIEW:LIGHT",
            draft={"receipt_id": outcome.receipt.id, "expense_id": outcome.expense.id if outcome.expense else None},
            db=db,
        )
        return

    # ── Review-heavy (low confidence) ──
    if outcome.route == ReceiptRoute.REVIEW_HEAVY:
        await _render_receipt_card(outcome, phone, user, lang, db, auto_approve=False, conf_label="low")
        await wa.send_text(phone, t("receipt_amount_guess_prompt", lang), db, user_id=user.id)
        save_draft_to_session(
            phone,
            state="RECEIPT_REVIEW:HEAVY",
            draft={"receipt_id": outcome.receipt.id, "expense_id": outcome.expense.id if outcome.expense else None},
            db=db,
        )
        return


async def _render_receipt_card(
    outcome,
    phone: str,
    user: User,
    lang: str,
    db: Session,
    auto_approve: bool,
    conf_label: str = "high",
) -> None:
    """Render the receipt summary card. auto_approve → no buttons; else 3 buttons."""
    expense = outcome.expense
    if not expense:
        # Defensive — should never happen on AUTO_APPROVE / REVIEW_*
        await wa.send_text(phone, t("receipt_ocr_failed", lang), db, user_id=user.id)
        return

    supplier = expense.supplier_name or "—"
    total = (expense.total_amount_minor_units or 0) / 100.0
    date_str = expense.expense_date.isoformat() if expense.expense_date else "—"

    if auto_approve:
        body = t(
            "receipt_auto_approve_card", lang,
            supplier=supplier,
            total=f"{total:,.2f}",
            date=date_str,
            receipt_id=outcome.receipt.id[:8],
        )
        await wa.send_text(phone, body, db, user_id=user.id)
        return

    conf_line = t(
        {
            "high": "receipt_review_conf_high",
            "mid":  "receipt_review_conf_mid",
            "low":  "receipt_review_conf_low",
        }[conf_label],
        lang,
    )
    body = t(
        "receipt_review_card", lang,
        supplier=supplier,
        total=f"{total:,.2f}",
        date=date_str,
        conf_line=conf_line,
    )
    await wa.send_buttons(
        phone,
        body_text=body,
        buttons=[
            {"id": f"rcpt:confirm:{outcome.receipt.id}", "title": t("btn_receipt_confirm", lang)},
            {"id": f"rcpt:fix:{outcome.receipt.id}",     "title": t("btn_receipt_fix", lang)},
            {"id": f"rcpt:reject:{outcome.receipt.id}",  "title": t("btn_receipt_reject", lang)},
        ],
        db=db,
        user_id=user.id,
    )


# ─────────────────────────────────────────────────────────────
# RECEIPT_REVIEW:LIGHT  — user taps confirm / fix / reject
# ─────────────────────────────────────────────────────────────
async def _flow_receipt_review_light(parsed: dict, phone: str, user: User, lang: str, db: Session) -> None:
    process_receipt, _, _, confirm_expense, reject_expense = _import_receipts()

    bid = (parsed.get("button_id") or "").strip()
    text = (parsed.get("text") or "").strip().lower()

    # Resolve receipt_id from button id, else from session draft
    receipt_id = None
    action = None
    if bid.startswith("rcpt:confirm:"):
        receipt_id = bid.split("rcpt:confirm:", 1)[1]
        action = "confirm"
    elif bid.startswith("rcpt:reject:"):
        receipt_id = bid.split("rcpt:reject:", 1)[1]
        action = "reject"
    elif bid.startswith("rcpt:fix:"):
        receipt_id = bid.split("rcpt:fix:", 1)[1]
        action = "fix"
    elif text in ("yes", "כן", "نعم", "confirm", "ok", "אישור"):
        action = "confirm"
    elif text in ("no", "לא", "لا", "reject", "דחה"):
        action = "reject"
    elif text in ("fix", "ערוך", "תקן", "تصحيح"):
        action = "fix"

    # Fall back to the in-flight draft for receipt_id
    session = get_or_create_session(phone, db)
    draft = load_draft(session)
    if not receipt_id:
        receipt_id = draft.get("receipt_id")
    expense_id = draft.get("expense_id")

    if not receipt_id or not expense_id:
        # Lost context — bounce to menu
        clear_session_draft(phone, db)
        await wa.send_text(phone, t("unknown_message", lang), db, user_id=user.id)
        return

    if action == "confirm":
        try:
            confirm_expense(expense_id=expense_id, confirmed_by_user_id=user.id, db=db)
        except Exception as e:
            print(f"[WA_ENGINE] confirm error: {e}")
        await wa.send_text(phone, t("receipt_filed", lang), db, user_id=user.id)
        clear_session_draft(phone, db)
        return

    if action == "reject":
        try:
            reject_expense(expense_id=expense_id, rejected_by_user_id=user.id, reason="user-rejected via WhatsApp", db=db)
        except Exception as e:
            print(f"[WA_ENGINE] reject error: {e}")
        await wa.send_text(phone, t("receipt_rejected", lang), db, user_id=user.id)
        clear_session_draft(phone, db)
        return

    if action == "fix":
        # Send into the heavy-review path so the user types the amount.
        save_draft_to_session(
            phone,
            state="RECEIPT_REVIEW:HEAVY",
            draft={"receipt_id": receipt_id, "expense_id": expense_id},
            db=db,
        )
        await wa.send_text(phone, t("receipt_amount_guess_prompt", lang), db, user_id=user.id)
        return

    # Unrecognised input — re-prompt
    await wa.send_text(phone, t("unknown_message", lang), db, user_id=user.id)


# ─────────────────────────────────────────────────────────────
# RECEIPT_REVIEW:HEAVY  — user types the amount
# ─────────────────────────────────────────────────────────────
async def _flow_receipt_review_heavy(parsed: dict, phone: str, user: User, lang: str, db: Session) -> None:
    process_receipt, _, _, confirm_expense, _ = _import_receipts()

    text = (parsed.get("text") or "").strip()
    # Accept "287", "287.50", "287,50" — strip currency symbols
    cleaned = text.replace("₪", "").replace(",", ".").strip()
    amount_match = re.match(r"^\d+(\.\d{1,2})?$", cleaned)
    if not amount_match:
        await wa.send_text(phone, t("receipt_amount_invalid", lang), db, user_id=user.id)
        return

    amount_minor = int(round(float(cleaned) * 100))

    session = get_or_create_session(phone, db)
    draft = load_draft(session)
    expense_id = draft.get("expense_id")
    if not expense_id:
        clear_session_draft(phone, db)
        await wa.send_text(phone, t("unknown_message", lang), db, user_id=user.id)
        return

    # Update the Expense with the corrected amount + confirm it
    from aurora_shared.database import Expense
    expense = db.query(Expense).filter(Expense.id == expense_id).first()
    if not expense:
        clear_session_draft(phone, db)
        await wa.send_text(phone, t("unknown_message", lang), db, user_id=user.id)
        return

    expense.total_amount_minor_units = amount_minor
    # Re-derive a sensible VAT estimate from the corrected total (18% VAT
    # for current 2026 Israel rate; if the user types pre-VAT, the
    # accountant can correct it later in the dashboard / portal).
    expense.vat_amount_minor_units = int(round(amount_minor * 0.18 / 1.18))
    expense.notes = (expense.notes or "") + " [amount manually corrected via WhatsApp]"
    db.commit()

    confirm_expense(expense_id=expense_id, confirmed_by_user_id=user.id, db=db)
    await wa.send_text(phone, t("receipt_filed", lang), db, user_id=user.id)
    clear_session_draft(phone, db)
