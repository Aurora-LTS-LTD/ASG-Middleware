"""
ASG Solutions — Telegram Bot Service
======================================
The full Hebrew-only Telegram bot implementation.

DESIGN PHILOSOPHY:
  - Buttons for everything except: amount, beneficiary name, tax ID.
  - LLM is not in the critical path. Zero ambiguity.
  - Every step persists its draft to the DB (TelegramSession) so
    the user can resume if their phone dies mid-flow.
  - All tax logic is delegated to tax_compliance.py — never duplicated here.
  - PDF generation is delegated to pdf_service.py.

BOT FLOWS:
  /start              → check identity → resume draft OR show main menu
  Main Menu           → 6 options via inline keyboard
  🧾 חשבונית חדשה    → 5-step guided invoice creation flow
  📊 מאזן             → show outstanding balance
  ⚠️ חשבוניות באיחור → list overdue invoices
  📄 חשבוניות שלי    → list recent invoices
  ⚙️ הגדרות          → toggle morning digest
  🆘 עזרה             → help message

INTEGRATION:
  This module exposes:
    build_application(token)  — creates the Application with all handlers
    get_application()         — returns the global app instance
    morning_digest_loop()     — background asyncio task (run at startup)

REAL-WORLD ANALOGY:
  This is the "phone script" for a very organized bank teller.
  Every button press is a pre-written question. The teller (bot)
  never improvises — it always follows the script. If the customer
  hangs up (closes Telegram), the script is saved. When they call
  back, the teller picks up exactly where they left off.
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import asyncio
import datetime
import json
import os
import re
import warnings

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.database import SessionLocal, Invoice, User
from app.services.tax_compliance import calculate_vat, check_tax_compliance
from app.services.invoice_service import (
    create_draft_invoice,
    finalize_invoice,
    AllocationFailedError,
)
from app.services.telegram_identity import (
    verify_pairing_code,
    get_user_by_telegram_id,
    get_or_create_session,
    save_draft_to_session,
    clear_session_draft,
)
from app.services.payment_service import get_business_balance, get_overdue_invoices


# ─────────────────────────────────────────────────────────────
# CONVERSATION STATE CONSTANTS
# ─────────────────────────────────────────────────────────────
# Each integer is a "room" in the conversation flow.
# The ConversationHandler sends the user to the right "room"
# based on what button they pressed or what they typed.
(
    INVOICE_AMOUNT,
    INVOICE_BENEFICIARY,
    INVOICE_TAX_ID,
    INVOICE_DESCRIPTION,
    INVOICE_CONFIRM,
) = range(5)

# The "end of conversation" sentinel used by ConversationHandler
CONV_END = ConversationHandler.END


# ─────────────────────────────────────────────────────────────
# GLOBAL APPLICATION INSTANCE
# ─────────────────────────────────────────────────────────────
# Stored here so main.py and the Telegram webhook router can
# both access it. Set by init_application() at startup.
_application: Application | None = None

# Timestamp of the last update processed (for the /health endpoint)
_last_update_at: datetime.datetime | None = None


def get_application() -> Application | None:
    """Return the global Application instance, or None if not initialized."""
    return _application


def get_bot() -> Bot | None:
    """Return the Bot instance for sending proactive messages."""
    return _application.bot if _application else None


def get_last_update_at() -> datetime.datetime | None:
    """Return the timestamp of the last processed Telegram update."""
    return _last_update_at


# ─────────────────────────────────────────────────────────────
# HELPERS: UI building blocks
# ─────────────────────────────────────────────────────────────

def _main_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the root menu inline keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧾 חשבונית חדשה", callback_data="new_invoice")],
        [
            InlineKeyboardButton("💰 רישום תשלום", callback_data="record_payment"),
            InlineKeyboardButton("📊 מאזן", callback_data="check_balance"),
        ],
        [
            InlineKeyboardButton("⚠️ חשבוניות באיחור", callback_data="overdue"),
            InlineKeyboardButton("📄 חשבוניות שלי", callback_data="my_invoices"),
        ],
        [
            InlineKeyboardButton("⚙️ הגדרות", callback_data="settings"),
            InlineKeyboardButton("🆘 עזרה", callback_data="help"),
        ],
    ])


def _back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """Simple single-button keyboard for returning to main menu."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_menu")]
    ])


def _cancel_keyboard() -> InlineKeyboardMarkup:
    """Shown during multi-step flows — let user bail out at any point."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✖ ביטול", callback_data="cancel_invoice")]
    ])


def _vat_badge(amount_net: float) -> str:
    """
    Return a one-line Hebrew VAT + threshold status string.
    This is the "Real-Time VAT Coach" (Feature 1).

    Examples:
      📌 סכום כולל מע״מ: 4,720 ₪  🟢 מתחת לסף
      📌 סכום כולל מע״מ: 13,570 ₪  🟡 מעל הסף — יידרש מספר הקצאה
    """
    vat_info = calculate_vat(amount_net)
    compliance = check_tax_compliance(amount_net)
    total = f"{vat_info['amount_total']:,.2f}"
    if compliance["requires_allocation"]:
        threshold_str = f"{compliance['threshold']:,.0f}"
        badge = f"🟡 מעל הסף ({threshold_str} ₪) — יידרש מספר הקצאה מרשות המסים"
    else:
        badge = "🟢 מתחת לסף — לא נדרש מספר הקצאה"
    return f"📌 סכום כולל מע״מ (18%): <b>{total} ₪</b>\n{badge}"


def _format_main_menu_header(user: User, db) -> str:
    """
    Build the main menu greeting with live balance data.
    Example: "בוקר טוב, איבראהים 👋\nיתרה פתוחה: 12,400 ₪ · 3 חשבוניות באיחור"
    """
    name = user.full_name.split()[0] if user.full_name else "שלום"
    greeting = name

    # Get live balance if business is linked
    try:
        if user.business_id:
            balance = get_business_balance(db, user.business_id)
            overdue = get_overdue_invoices(db, user.business_id)
            outstanding = f"{balance.get('total_outstanding', 0):,.2f}"
            overdue_count = len(overdue)
            stats = f"\n💰 יתרה פתוחה: <b>{outstanding} ₪</b>"
            if overdue_count > 0:
                stats += f"  ·  ⚠️ {overdue_count} חשבוניות באיחור"
        else:
            stats = ""
    except Exception:
        stats = ""

    return f"👋 שלום, <b>{greeting}</b>!{stats}\n\nבחר פעולה:"


# ─────────────────────────────────────────────────────────────
# IDENTITY CHECK HELPER
# ─────────────────────────────────────────────────────────────

def _get_linked_user(telegram_user_id: str, db) -> User | None:
    """Return the linked User or None. Pure lookup, no side effects."""
    return get_user_by_telegram_id(telegram_user_id, db)


# ─────────────────────────────────────────────────────────────
# /start HANDLER
# ─────────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point. Called on /start (with or without a pairing code).

    Three branches:
    1. /start LINK-482913 → pairing flow
    2. Not linked         → show "please link" message
    3. Linked + has draft → offer to resume
    4. Linked             → show main menu
    """
    global _last_update_at
    _last_update_at = datetime.datetime.utcnow()

    tg_user_id = str(update.effective_user.id)
    args = context.args or []

    db = SessionLocal()
    try:
        # ── Branch 1: Pairing code ──
        if args and args[0].startswith("LINK-"):
            code = args[0].replace("LINK-", "")
            user = verify_pairing_code(tg_user_id, code, db)
            if user:
                await update.message.reply_text(
                    f"✅ <b>החשבון שלך קושר בהצלחה!</b>\n\n"
                    f"שלום, <b>{user.full_name}</b>!\n"
                    f"אני המנהל החכם של ASG Solutions.\n\n"
                    f"הקש על הכפתור למטה כדי להתחיל:",
                    parse_mode="HTML",
                    reply_markup=_main_menu_keyboard(),
                )
            else:
                await update.message.reply_text(
                    "❌ <b>קוד לא תקין או פג תוקפו.</b>\n\n"
                    "חזור לדשבורד וצור קוד חדש (תקף 10 דקות).",
                    parse_mode="HTML",
                )
            return CONV_END

        # ── Check identity for branches 2-4 ──
        user = _get_linked_user(tg_user_id, db)

        if not user:
            # ── Branch 2: Not linked ──
            await update.message.reply_text(
                "🔒 <b>החשבון שלך טרם קושר.</b>\n\n"
                "כדי להשתמש בבוט:\n"
                "1. פתח את הדשבורד (http://10.0.0.2:8000/dashboard)\n"
                "2. לחץ על <b>Link Telegram</b>\n"
                "3. שלח את הקוד שתקבל לבוט",
                parse_mode="HTML",
            )
            return CONV_END

        # ── Check for active draft (Feature 2: Save & Resume) ──
        session = get_or_create_session(tg_user_id, db)
        draft_json = session.draft_payload_json
        has_draft = bool(draft_json)

        if has_draft:
            draft = json.loads(draft_json)
            minutes_ago = ""
            if session.updated_at:
                delta = datetime.datetime.utcnow() - session.updated_at
                mins = int(delta.total_seconds() / 60)
                if mins < 60:
                    minutes_ago = f"לפני {mins} דקות"
                elif delta.total_seconds() < 86400:  # < 24h — offer resume
                    hours = int(delta.total_seconds() / 3600)
                    minutes_ago = f"לפני {hours} שעות"
                else:
                    # Draft is older than 24h — auto-discard
                    clear_session_draft(tg_user_id, db)
                    has_draft = False

            if has_draft:
                amount = draft.get("amount_net", "?")
                name = draft.get("beneficiary_name", "?")
                resume_text = (
                    f"📋 <b>יש לך טיוטה שמורה</b> ({minutes_ago}):\n\n"
                    f"💰 סכום: <b>{amount} ₪</b>\n"
                    f"👤 שם: <b>{name}</b>\n\n"
                    f"מה תרצה לעשות?"
                )
                await update.message.reply_text(
                    resume_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("▶ המשך מהמקום שעצרת", callback_data="resume_draft")],
                        [InlineKeyboardButton("🗑 מחק טיוטה", callback_data="discard_draft")],
                    ]),
                )
                return CONV_END

        # ── Branch 4: Normal start — show main menu ──
        header = _format_main_menu_header(user, db)
        await update.message.reply_text(
            header,
            parse_mode="HTML",
            reply_markup=_main_menu_keyboard(),
        )
        return CONV_END

    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# MENU NAVIGATION HANDLERS (non-invoice flows)
# ─────────────────────────────────────────────────────────────

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the 'back to main menu' button from any sub-screen."""
    query = update.callback_query
    await query.answer()
    tg_user_id = str(update.effective_user.id)
    db = SessionLocal()
    try:
        user = _get_linked_user(tg_user_id, db)
        if not user:
            await query.edit_message_text("🔒 חשבון לא מקושר.")
            return
        header = _format_main_menu_header(user, db)
        await query.edit_message_text(
            header,
            parse_mode="HTML",
            reply_markup=_main_menu_keyboard(),
        )
    finally:
        db.close()


async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the outstanding balance and payment summary."""
    query = update.callback_query
    await query.answer()
    tg_user_id = str(update.effective_user.id)
    db = SessionLocal()
    try:
        user = _get_linked_user(tg_user_id, db)
        if not user or not user.business_id:
            await query.edit_message_text("❌ לא נמצא עסק מקושר לחשבון שלך.")
            return

        balance = get_business_balance(db, user.business_id)
        outstanding = f"{balance.get('total_outstanding', 0):,.2f}"
        count = balance.get('invoice_count', 0)
        oldest = balance.get('oldest_due_date', None)
        oldest_str = ""
        if oldest:
            oldest_str = f"\n📅 ותיקה ביותר: <b>{oldest[:10]}</b>"

        text = (
            f"📊 <b>מאזן חשבוניות</b>\n\n"
            f"💰 יתרה פתוחה: <b>{outstanding} ₪</b>\n"
            f"📄 חשבוניות פתוחות: <b>{count}</b>"
            f"{oldest_str}"
        )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=_back_to_menu_keyboard(),
        )
    finally:
        db.close()


async def show_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all overdue invoices with days_overdue."""
    query = update.callback_query
    await query.answer()
    tg_user_id = str(update.effective_user.id)
    db = SessionLocal()
    try:
        user = _get_linked_user(tg_user_id, db)
        if not user or not user.business_id:
            await query.edit_message_text("❌ לא נמצא עסק מקושר.")
            return

        overdue = get_overdue_invoices(db, user.business_id)
        if not overdue:
            await query.edit_message_text(
                "✅ <b>אין חשבוניות באיחור!</b>\nכל החשבוניות שולמו בזמן.",
                parse_mode="HTML",
                reply_markup=_back_to_menu_keyboard(),
            )
            return

        lines = [f"⚠️ <b>חשבוניות באיחור ({len(overdue)})</b>\n"]
        for inv in overdue[:8]:  # Limit to 8 to avoid huge messages
            days = inv.get("days_overdue", 0)
            name = inv.get("beneficiary_name", "?")[:20]
            amount = f"{inv.get('amount_total', 0):,.0f}"
            inv_num = inv.get("invoice_number", "?")
            lines.append(f"• <b>{inv_num}</b> — {name}\n  💰 {amount} ₪  |  🕐 {days} ימים איחור")

        if len(overdue) > 8:
            lines.append(f"\n...ועוד {len(overdue) - 8} חשבוניות")

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=_back_to_menu_keyboard(),
        )
    finally:
        db.close()


async def show_my_invoices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List the 10 most recent invoices for this business."""
    query = update.callback_query
    await query.answer()
    tg_user_id = str(update.effective_user.id)
    db = SessionLocal()
    try:
        user = _get_linked_user(tg_user_id, db)
        if not user or not user.business_id:
            await query.edit_message_text("❌ לא נמצא עסק מקושר.")
            return

        invoices = (
            db.query(Invoice)
            .filter(Invoice.business_id == user.business_id)
            .order_by(Invoice.created_at.desc())
            .limit(10)
            .all()
        )
        if not invoices:
            await query.edit_message_text(
                "📄 <b>אין חשבוניות עדיין.</b>\nלחץ על 🧾 חשבונית חדשה כדי להתחיל!",
                parse_mode="HTML",
                reply_markup=_back_to_menu_keyboard(),
            )
            return

        STATUS_EMOJI = {
            "draft": "📝",
            "finalized": "✅",
            "sent": "📬",
            "pending_allocation": "⏳",
            "cancelled": "❌",
        }
        PAY_EMOJI = {"unpaid": "🔴", "partial": "🟡", "paid": "🟢"}

        lines = [f"📄 <b>10 חשבוניות אחרונות</b>\n"]
        for inv in invoices:
            s_emoji = STATUS_EMOJI.get(inv.status, "•")
            p_emoji = PAY_EMOJI.get(inv.payment_status or "unpaid", "•")
            amount = f"{inv.amount_total:,.0f}"
            name = (inv.beneficiary_name or "?")[:18]
            lines.append(
                f"{s_emoji} <b>{inv.invoice_number}</b> — {name}\n"
                f"   💰 {amount} ₪  {p_emoji}"
            )

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=_back_to_menu_keyboard(),
        )
    finally:
        db.close()


async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the settings screen with morning digest toggle."""
    query = update.callback_query
    await query.answer()
    tg_user_id = str(update.effective_user.id)
    db = SessionLocal()
    try:
        user = _get_linked_user(tg_user_id, db)
        if not user:
            await query.edit_message_text("❌ חשבון לא מקושר.")
            return

        enabled = user.morning_digest_enabled
        toggle_label = "🔔 כבה הודעת בוקר" if enabled else "🔕 הפעל הודעת בוקר"
        status = "פעיל ✅" if enabled else "כבוי ❌"

        await query.edit_message_text(
            f"⚙️ <b>הגדרות</b>\n\n"
            f"🌅 הודעת בוקר (08:30): <b>{status}</b>\n"
            f"📱 Telegram: מקושר\n"
            f"👤 {user.full_name} ({user.email})",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(toggle_label, callback_data="toggle_digest")],
                [InlineKeyboardButton("🏠 תפריט ראשי", callback_data="back_to_menu")],
            ]),
        )
    finally:
        db.close()


async def toggle_morning_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle the morning digest on/off."""
    query = update.callback_query
    await query.answer()
    tg_user_id = str(update.effective_user.id)
    db = SessionLocal()
    try:
        user = _get_linked_user(tg_user_id, db)
        if not user:
            await query.edit_message_text("❌ חשבון לא מקושר.")
            return
        user.morning_digest_enabled = not user.morning_digest_enabled
        db.commit()
        status = "הופעל ✅" if user.morning_digest_enabled else "כובה ❌"
        await query.answer(f"הודעת בוקר {status}", show_alert=True)
        # Refresh the settings screen
        await show_settings(update, context)
    finally:
        db.close()


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the help/about screen."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🆘 <b>עזרה</b>\n\n"
        "<b>פקודות זמינות:</b>\n"
        "• /start — פתח את התפריט הראשי\n"
        "• /cancel — בטל את הפעולה הנוכחית\n\n"
        "<b>זרימת יצירת חשבונית:</b>\n"
        "🧾 חדשה → 💰 סכום → 👤 שם → 🔢 ח.פ → 📝 תיאור → ✅ אישור → PDF\n\n"
        "<b>שאלות?</b>\n"
        "פנה לאיבראהים מצארוה | ASG Solutions v2.0.0",
        parse_mode="HTML",
        reply_markup=_back_to_menu_keyboard(),
    )


async def record_payment_placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Placeholder for payment recording — marked as coming soon."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💰 <b>רישום תשלום — בקרוב!</b>\n\n"
        "פיצ'ר זה יהיה זמין בגרסה הבאה.\n"
        "לרישום תשלום השתמש בדשבורד: http://10.0.0.2:8000/dashboard",
        parse_mode="HTML",
        reply_markup=_back_to_menu_keyboard(),
    )


async def resume_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-show the confirmation screen from a saved draft."""
    query = update.callback_query
    await query.answer()
    tg_user_id = str(update.effective_user.id)
    db = SessionLocal()
    try:
        session = get_or_create_session(tg_user_id, db)
        if not session.draft_payload_json:
            await query.edit_message_text(
                "🏠 אין טיוטה פעילה.",
                reply_markup=_main_menu_keyboard(),
            )
            return
        draft = json.loads(session.draft_payload_json)
        # Load draft back into context.user_data
        context.user_data["invoice_draft"] = draft
        # Show the confirmation screen
        text = _build_confirmation_text(draft)
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=_confirm_keyboard(),
        )
        return INVOICE_CONFIRM
    finally:
        db.close()


async def discard_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Discard the saved draft and go to main menu."""
    query = update.callback_query
    await query.answer()
    tg_user_id = str(update.effective_user.id)
    db = SessionLocal()
    try:
        clear_session_draft(tg_user_id, db)
        user = _get_linked_user(tg_user_id, db)
        if user:
            header = _format_main_menu_header(user, db)
            await query.edit_message_text(
                header,
                parse_mode="HTML",
                reply_markup=_main_menu_keyboard(),
            )
        else:
            await query.edit_message_text("🗑 הטיוטה נמחקה.")
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# INVOICE CREATION FLOW (ConversationHandler)
# ─────────────────────────────────────────────────────────────

async def start_new_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for the invoice creation flow."""
    query = update.callback_query
    await query.answer()

    tg_user_id = str(update.effective_user.id)
    db = SessionLocal()
    try:
        user = _get_linked_user(tg_user_id, db)
        if not user or not user.business_id:
            await query.edit_message_text(
                "❌ <b>לא ניתן ליצור חשבונית.</b>\n"
                "החשבון שלך אינו מקושר לעסק. פנה לאיבראהים.",
                parse_mode="HTML",
            )
            return CONV_END
    finally:
        db.close()

    # Clear any stale draft from context
    context.user_data.clear()

    await query.edit_message_text(
        "🧾 <b>חשבונית חדשה</b> — שלב 1 מתוך 5\n\n"
        "💰 <b>מה הסכום לפני מע״מ?</b>\n\n"
        "הכנס מספר בשקלים (מספרים בלבד, ניתן עם נקודה עשרונית):\n"
        "<i>לדוגמה: 4500 או 4500.50</i>",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )
    return INVOICE_AMOUNT


async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive and validate the invoice amount — Feature 1 (VAT Coach) fires here."""
    tg_user_id = str(update.effective_user.id)
    raw = update.message.text.strip().replace(",", "")

    # ── Parse ──
    match = re.fullmatch(r"(\d{1,10})(\.\d{1,2})?", raw)
    if not match:
        await update.message.reply_text(
            "❌ <b>סכום לא תקין.</b>\n"
            "הכנס מספר בשקלים בלבד, ללא סמלים.\n"
            "<i>לדוגמה: 4500 או 4500.50</i>",
            parse_mode="HTML",
            reply_markup=_cancel_keyboard(),
        )
        return INVOICE_AMOUNT

    amount_net = float(raw)

    # ── Hard cap to prevent layout breakage ──
    if amount_net > 10_000_000:
        await update.message.reply_text(
            "❌ <b>סכום גדול מדי.</b>\n"
            "הסכום חייב להיות פחות מ-10,000,000 ₪.\n"
            "אם זה נכון, צור את החשבונית מהדשבורד.",
            parse_mode="HTML",
            reply_markup=_cancel_keyboard(),
        )
        return INVOICE_AMOUNT

    # ── VAT Coach (Feature 1) ──
    vat_info = calculate_vat(amount_net)
    badge = _vat_badge(amount_net)

    # ── Save partial draft ──
    draft = {"amount_net": amount_net, "vat_info": vat_info}
    context.user_data["invoice_draft"] = draft
    db = SessionLocal()
    try:
        save_draft_to_session(tg_user_id, "INVOICE_BENEFICIARY", draft, db)
    finally:
        db.close()

    await update.message.reply_text(
        f"{badge}\n\n"
        f"🧾 <b>שלב 2 מתוך 5</b>\n\n"
        f"👤 <b>מה שם המקבל?</b>\n"
        f"הכנס את שם הלקוח או העסק:",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )
    return INVOICE_BENEFICIARY


async def handle_beneficiary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive beneficiary name."""
    tg_user_id = str(update.effective_user.id)
    name = update.message.text.strip()

    if len(name) < 2 or len(name) > 80:
        await update.message.reply_text(
            "❌ <b>שם לא תקין.</b>\nחייב להיות בין 2 ל-80 תווים.",
            parse_mode="HTML",
            reply_markup=_cancel_keyboard(),
        )
        return INVOICE_BENEFICIARY

    draft = context.user_data.get("invoice_draft", {})
    draft["beneficiary_name"] = name
    context.user_data["invoice_draft"] = draft

    db = SessionLocal()
    try:
        save_draft_to_session(tg_user_id, "INVOICE_TAX_ID", draft, db)
    finally:
        db.close()

    await update.message.reply_text(
        f"🧾 <b>שלב 3 מתוך 5</b>\n\n"
        f"🔢 <b>מה ח.פ. / ע.מ. של {name}?</b>\n\n"
        f"הכנס את מספר החברה (9 ספרות) או לחץ <b>דלג</b>:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("← דלג", callback_data="skip_tax_id")],
            [InlineKeyboardButton("✖ ביטול", callback_data="cancel_invoice")],
        ]),
    )
    return INVOICE_TAX_ID


async def handle_tax_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive and validate the Israeli tax ID."""
    tg_user_id = str(update.effective_user.id)
    raw = update.message.text.strip().replace("-", "").replace(" ", "")

    # Israeli tax ID (ח.פ / ע.מ) is 9 digits
    if not re.fullmatch(r"\d{9}", raw):
        await update.message.reply_text(
            "❌ <b>מספר לא תקין.</b>\n"
            "ח.פ / ע.מ חייב להיות 9 ספרות.\n"
            "<i>לדוגמה: 123456789</i>\n\n"
            "או לחץ <b>דלג</b> אם אין מספר:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("← דלג", callback_data="skip_tax_id")],
                [InlineKeyboardButton("✖ ביטול", callback_data="cancel_invoice")],
            ]),
        )
        return INVOICE_TAX_ID

    return await _proceed_to_description(update, context, tax_id=raw, via_message=True)


async def skip_tax_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skip the tax ID step (button press)."""
    query = update.callback_query
    await query.answer()
    return await _proceed_to_description(update, context, tax_id=None, via_message=False)


async def _proceed_to_description(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tax_id: str | None,
    via_message: bool,
) -> int:
    """Shared logic: save tax_id (or None) and advance to description step."""
    tg_user_id = str(update.effective_user.id)
    draft = context.user_data.get("invoice_draft", {})
    draft["beneficiary_tax_id"] = tax_id
    context.user_data["invoice_draft"] = draft

    # Build description step keyboard with 5 recent descriptions + manual + skip
    db = SessionLocal()
    try:
        user = _get_linked_user(tg_user_id, db)
        save_draft_to_session(tg_user_id, "INVOICE_DESCRIPTION", draft, db)
        recent_descs = _get_recent_descriptions(user.business_id if user else None, db)
    finally:
        db.close()

    buttons = []
    for i, desc in enumerate(recent_descs):
        short = desc[:28] + "…" if len(desc) > 30 else desc
        buttons.append([InlineKeyboardButton(f"📋 {short}", callback_data=f"desc_{i}")])
    buttons.append([InlineKeyboardButton("✍ הכנס תיאור ידנית", callback_data="desc_manual")])
    buttons.append([InlineKeyboardButton("← דלג", callback_data="skip_description")])
    buttons.append([InlineKeyboardButton("✖ ביטול", callback_data="cancel_invoice")])

    # Store recent descriptions list in context for later lookup
    context.user_data["recent_descs"] = recent_descs

    text = (
        "🧾 <b>שלב 4 מתוך 5</b>\n\n"
        "📝 <b>תיאור השירות?</b>\n\n"
        "בחר מרשימה, הכנס ידנית, או דלג:"
    )
    keyboard = InlineKeyboardMarkup(buttons)

    if via_message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.callback_query.edit_message_text(
            text, parse_mode="HTML", reply_markup=keyboard
        )
    return INVOICE_DESCRIPTION


def _get_recent_descriptions(business_id: int | None, db) -> list[str]:
    """Return up to 5 recent non-empty descriptions used by this business."""
    if not business_id:
        return []
    recent = (
        db.query(Invoice.description)
        .filter(
            Invoice.business_id == business_id,
            Invoice.description != None,
            Invoice.description != "",
        )
        .order_by(Invoice.created_at.desc())
        .limit(20)
        .all()
    )
    # Deduplicate while preserving order
    seen = set()
    result = []
    for (desc,) in recent:
        if desc and desc not in seen:
            seen.add(desc)
            result.append(desc)
        if len(result) >= 5:
            break
    return result


async def handle_description_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User picked a recent description from the inline buttons."""
    query = update.callback_query
    await query.answer()
    idx = int(query.data.replace("desc_", ""))
    recent = context.user_data.get("recent_descs", [])
    if idx < len(recent):
        return await _save_description_and_confirm(update, context, recent[idx], via_message=False)
    await query.edit_message_text("❌ תיאור לא נמצא. נסה שוב.", reply_markup=_cancel_keyboard())
    return INVOICE_DESCRIPTION


async def ask_for_manual_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User wants to type a custom description."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✍ <b>הכנס תיאור השירות:</b>\n\n"
        "<i>לדוגמה: עבודות אינסטלציה, ייעוץ עסקי, תיקון מזגן</i>",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )
    context.user_data["awaiting_manual_desc"] = True
    return INVOICE_DESCRIPTION


async def handle_description_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive a manually typed description."""
    if not context.user_data.get("awaiting_manual_desc"):
        # User typed something unexpected — treat it as a description anyway
        pass
    context.user_data.pop("awaiting_manual_desc", None)
    desc = update.message.text.strip()[:200]
    return await _save_description_and_confirm(update, context, desc, via_message=True)


async def skip_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skip the description step."""
    query = update.callback_query
    await query.answer()
    return await _save_description_and_confirm(update, context, None, via_message=False)


async def _save_description_and_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    description: str | None,
    via_message: bool,
) -> int:
    """Save description and advance to the confirmation screen (step 5)."""
    tg_user_id = str(update.effective_user.id)
    draft = context.user_data.get("invoice_draft", {})
    draft["description"] = description
    context.user_data["invoice_draft"] = draft

    db = SessionLocal()
    try:
        save_draft_to_session(tg_user_id, "INVOICE_CONFIRM", draft, db)
    finally:
        db.close()

    text = _build_confirmation_text(draft)
    keyboard = _confirm_keyboard()

    if via_message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.callback_query.edit_message_text(
            text, parse_mode="HTML", reply_markup=keyboard
        )
    return INVOICE_CONFIRM


def _build_confirmation_text(draft: dict) -> str:
    """Build the full confirmation card text (Step 5)."""
    vat_info = draft.get("vat_info", {})
    net = f"{draft.get('amount_net', 0):,.2f}"
    vat_amt = f"{vat_info.get('vat_amount', 0):,.2f}"
    total = f"{vat_info.get('amount_total', 0):,.2f}"
    name = draft.get("beneficiary_name", "—")
    tax_id = draft.get("beneficiary_tax_id") or "לא הוזן"
    desc = draft.get("description") or "ללא תיאור"

    compliance = check_tax_compliance(draft.get("amount_net", 0))
    alloc_line = (
        "🟡 <b>יידרש מספר הקצאה</b> מרשות המסים"
        if compliance["requires_allocation"]
        else "🟢 לא נדרש מספר הקצאה"
    )

    return (
        f"🧾 <b>שלב 5 מתוך 5 — אישור פרטים</b>\n\n"
        f"👤 <b>מקבל:</b> {name}\n"
        f"🔢 <b>ח.פ.:</b> {tax_id}\n"
        f"📝 <b>תיאור:</b> {desc}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>סכום לפני מע״מ:</b> {net} ₪\n"
        f"📊 <b>מע״מ 18%:</b> {vat_amt} ₪\n"
        f"💵 <b>סה״כ לתשלום:</b> {total} ₪\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{alloc_line}\n\n"
        f"📄 <i>יופק PDF בעברית לאחר האישור</i>"
    )


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ אשר וסיים", callback_data="confirm_invoice")],
        [
            InlineKeyboardButton("✏ ערוך סכום", callback_data="edit_amount"),
            InlineKeyboardButton("✏ ערוך שם", callback_data="edit_name"),
        ],
        [InlineKeyboardButton("✖ בטל", callback_data="cancel_invoice")],
    ])


async def confirm_invoice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User confirmed — create invoice, finalize, deliver PDF."""
    query = update.callback_query
    await query.answer()
    tg_user_id = str(update.effective_user.id)

    # ── Show "working" placeholder ──
    await query.edit_message_text(
        "⏳ <b>יוצר חשבונית...</b>",
        parse_mode="HTML",
    )

    db = SessionLocal()
    try:
        user = _get_linked_user(tg_user_id, db)
        if not user or not user.business_id:
            await query.edit_message_text("❌ שגיאה — חשבון לא מקושר לעסק.")
            return CONV_END

        draft = context.user_data.get("invoice_draft", {})

        # ── Step A: Create draft invoice in DB ──
        invoice_dict = create_draft_invoice(
            db=db,
            business_id=user.business_id,
            beneficiary_name=draft["beneficiary_name"],
            amount_net=draft["amount_net"],
            beneficiary_tax_id=draft.get("beneficiary_tax_id"),
            description=draft.get("description"),
        )
        invoice_id = invoice_dict["id"]

        # ── Step B: Check if allocation needed ──
        requires_alloc = invoice_dict.get("requires_allocation") == 1

        if requires_alloc:
            # Show "contacting ITA" message — we'll edit it when done
            pending_msg = await query.edit_message_text(
                "⏳ <b>מקצה מספר מרשות המסים...</b>\n\n"
                f"חשבונית <b>{invoice_dict['invoice_number']}</b> נוצרה.\n"
                "ממתין לאישור הרשות — זה יכול לקחת כמה שניות.",
                parse_mode="HTML",
            )
            pending_msg_id = pending_msg.message_id
        else:
            pending_msg_id = None

        # ── Step C: Finalize (may raise AllocationFailedError) ──
        try:
            result = await finalize_invoice(
                db=db,
                invoice_id=invoice_id,
                lang="he",
                actor_label="telegram_bot",
            )

            # ── Success ──
            alloc_num = result.get("allocation_number") or "לא נדרש"
            total = f"{result.get('amount_total', 0):,.2f}"
            inv_number = result.get("invoice_number", "?")
            clear_session_draft(tg_user_id, db)

            if requires_alloc and pending_msg_id:
                await query.bot.edit_message_text(
                    chat_id=query.message.chat_id,
                    message_id=pending_msg_id,
                    text=(
                        f"✅ <b>החשבונית אושרה!</b>\n\n"
                        f"📄 <b>{inv_number}</b>\n"
                        f"💰 סכום כולל: <b>{total} ₪</b>\n"
                        f"🔢 מספר הקצאה: <b>{alloc_num}</b>"
                    ),
                    parse_mode="HTML",
                )
            else:
                await query.edit_message_text(
                    f"✅ <b>החשבונית נוצרה בהצלחה!</b>\n\n"
                    f"📄 <b>{inv_number}</b>\n"
                    f"💰 סכום כולל: <b>{total} ₪</b>",
                    parse_mode="HTML",
                )

            # ── Send PDF ──
            pdf_url = result.get("pdf_url")
            if pdf_url:
                disk_path = "app" + pdf_url
                import os as _os
                if _os.path.exists(disk_path):
                    with open(disk_path, "rb") as pdf_file:
                        await query.bot.send_document(
                            chat_id=query.message.chat_id,
                            document=pdf_file,
                            filename=f"{inv_number}.pdf",
                            caption=(
                                f"📄 {inv_number}\n"
                                f"💰 {total} ₪  |  🔢 הקצאה: {alloc_num}"
                            ),
                        )

            # ── Show main menu ──
            header = _format_main_menu_header(user, db)
            await query.bot.send_message(
                chat_id=query.message.chat_id,
                text=header,
                parse_mode="HTML",
                reply_markup=_main_menu_keyboard(),
            )

        except AllocationFailedError:
            # ── Queue retry (Feature 5) ──
            # Set invoice to pending_allocation with first retry scheduled in 30s
            invoice_obj = db.query(Invoice).filter(Invoice.id == invoice_id).first()
            if invoice_obj:
                invoice_obj.status = "pending_allocation"
                invoice_obj.allocation_status = "retry_pending"
                invoice_obj.allocation_retry_count = 1
                invoice_obj.allocation_next_retry_at = (
                    datetime.datetime.utcnow() + datetime.timedelta(seconds=30)
                )
                db.commit()

            # Store the pending message ID so the retry worker can edit it
            save_draft_to_session(
                tg_user_id,
                state="PENDING_ALLOCATION",
                draft={},
                db=db,
                pending_message_id=pending_msg_id,
                pending_invoice_id=invoice_id,
            )

            if pending_msg_id:
                await query.bot.edit_message_text(
                    chat_id=query.message.chat_id,
                    message_id=pending_msg_id,
                    text=(
                        f"🕐 <b>ממתין לאישור הרשות</b>\n\n"
                        f"חשבונית <b>{invoice_dict['invoice_number']}</b> נוצרה.\n"
                        "שרות רשות המסים תפוס כרגע.\n"
                        "אנסה שוב תוך 30 שניות ואעדכן אוטומטית. 🔄"
                    ),
                    parse_mode="HTML",
                )

    except Exception as e:
        print(f"[BOT] Unexpected error in confirm_invoice: {e}")
        await query.edit_message_text(
            f"❌ <b>שגיאה בלתי צפויה.</b>\n{str(e)[:100]}",
            parse_mode="HTML",
        )
    finally:
        db.close()
        context.user_data.clear()

    return CONV_END


async def cancel_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the current invoice flow and return to main menu."""
    query = update.callback_query
    await query.answer()
    tg_user_id = str(update.effective_user.id)
    context.user_data.clear()
    db = SessionLocal()
    try:
        clear_session_draft(tg_user_id, db)
        user = _get_linked_user(tg_user_id, db)
        header = _format_main_menu_header(user, db) if user else "🏠 בוא נתחיל מחדש."
        await query.edit_message_text(
            f"✖ <b>הפעולה בוטלה.</b>\n\n{header}",
            parse_mode="HTML",
            reply_markup=_main_menu_keyboard(),
        )
    finally:
        db.close()
    return CONV_END


async def edit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Let user re-enter the amount from the confirmation screen."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏ <b>עריכת סכום</b>\n\n"
        "💰 הכנס את הסכום החדש לפני מע״מ:",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )
    return INVOICE_AMOUNT


async def edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Let user re-enter the beneficiary name from the confirmation screen."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏ <b>עריכת שם המקבל</b>\n\n"
        "👤 הכנס את השם החדש:",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )
    return INVOICE_BENEFICIARY


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cancel command — always returns to main menu."""
    tg_user_id = str(update.effective_user.id)
    context.user_data.clear()
    db = SessionLocal()
    try:
        clear_session_draft(tg_user_id, db)
        user = _get_linked_user(tg_user_id, db)
        header = _format_main_menu_header(user, db) if user else "שלום!"
        await update.message.reply_text(
            f"✖ <b>פעולה בוטלה.</b>\n\n{header}",
            parse_mode="HTML",
            reply_markup=_main_menu_keyboard(),
        )
    finally:
        db.close()
    return CONV_END


# ─────────────────────────────────────────────────────────────
# MORNING DIGEST BACKGROUND TASK (Feature 3)
# ─────────────────────────────────────────────────────────────

async def _send_morning_digests(bot: Bot) -> None:
    """Send the daily briefing to all opted-in users."""
    db = SessionLocal()
    try:
        opted_in = db.query(User).filter(
            User.telegram_user_id != None,
            User.morning_digest_enabled == True,
            User.is_active == True,
        ).all()

        print(f"[MORNING_DIGEST] Sending to {len(opted_in)} users")

        for user in opted_in:
            try:
                name = user.full_name.split()[0] if user.full_name else "שלום"
                stats = ""
                if user.business_id:
                    balance = get_business_balance(db, user.business_id)
                    overdue = get_overdue_invoices(db, user.business_id)
                    outstanding = f"{balance.get('total_outstanding', 0):,.2f}"
                    overdue_count = len(overdue)
                    overdue_amount = sum(
                        i.get("amount_total", 0) - i.get("amount_paid", 0)
                        for i in overdue
                    )
                    stats = f"\n💰 יתרה פתוחה: <b>{outstanding} ₪</b>"
                    if overdue_count > 0:
                        oldest = sorted(overdue, key=lambda x: x.get("days_overdue", 0), reverse=True)
                        oldest_name = oldest[0].get("beneficiary_name", "?")[:20] if oldest else "?"
                        oldest_days = oldest[0].get("days_overdue", 0) if oldest else 0
                        stats += (
                            f"\n⚠️ {overdue_count} חשבוניות באיחור "
                            f"({f'{overdue_amount:,.0f}'} ₪)"
                            f"\n📌 הותיקה ביותר: {oldest_name} — {oldest_days} ימים"
                        )

                text = (
                    f"☀️ <b>בוקר טוב, {name}!</b>{stats}\n\n"
                    f"יום עבודה טוב 💼"
                )

                keyboard = None
                if user.business_id and stats:
                    overdue_list = get_overdue_invoices(db, user.business_id)
                    if overdue_list:
                        keyboard = InlineKeyboardMarkup([
                            [
                                InlineKeyboardButton("📨 שלח תזכורות", callback_data="trigger_reminders"),
                                InlineKeyboardButton("📊 מאזן מלא", callback_data="check_balance"),
                            ]
                        ])

                await bot.send_message(
                    chat_id=user.telegram_user_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            except Exception as e:
                print(f"[MORNING_DIGEST] Failed for user {user.email}: {e}")
    finally:
        db.close()


async def trigger_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'שלח תזכורות' button from the morning digest."""
    query = update.callback_query
    await query.answer()
    tg_user_id = str(update.effective_user.id)
    db = SessionLocal()
    try:
        user = _get_linked_user(tg_user_id, db)
        if not user:
            await query.edit_message_text("❌ חשבון לא מקושר.")
            return
        from app.services.reminder_service import send_overdue_reminders
        result = send_overdue_reminders(db)
        sent = result.get("reminders_sent", 0)
        skipped = result.get("skipped", 0)
        await query.answer(
            f"נשלחו {sent} תזכורות (דולגו {skipped})",
            show_alert=True,
        )
    finally:
        db.close()


async def morning_digest_loop(bot: Bot) -> None:
    """
    Long-running asyncio task that fires every day at 08:30 Israel time (UTC+3).
    Call as: asyncio.create_task(morning_digest_loop(application.bot))
    """
    print("[MORNING_DIGEST] Scheduled daily digest worker started")
    while True:
        try:
            # Israel time ≈ UTC+3 (summer) or UTC+2 (winter).
            # Using UTC+3 for simplicity — will be ≤30 min off in winter.
            israel_now = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
            target = israel_now.replace(hour=8, minute=30, second=0, microsecond=0)
            if israel_now >= target:
                target += datetime.timedelta(days=1)
            seconds_until = (target - israel_now).total_seconds()
            print(f"[MORNING_DIGEST] Next digest in {seconds_until/3600:.1f} hours")
            await asyncio.sleep(seconds_until)
            await _send_morning_digests(bot)
        except Exception as e:
            print(f"[MORNING_DIGEST] Error: {e} — retrying in 60s")
            await asyncio.sleep(60)


# ─────────────────────────────────────────────────────────────
# APPLICATION BUILDER
# ─────────────────────────────────────────────────────────────

def build_application(token: str) -> Application:
    """
    Create and configure the python-telegram-bot Application with all handlers.

    RETURNS: Application instance (not yet started — call initialize() separately)
    """
    # Suppress the per_message advisory — we intentionally use per_message=False
    # (track state per conversation/user, not per individual message click).
    warnings.filterwarnings("ignore", message=".*per_message.*", category=Warning)

    app = ApplicationBuilder().token(token).build()

    # ── Invoice creation ConversationHandler ──
    invoice_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_new_invoice, pattern="^new_invoice$"),
            CallbackQueryHandler(resume_draft, pattern="^resume_draft$"),
        ],
        states={
            INVOICE_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount),
                CallbackQueryHandler(edit_amount, pattern="^edit_amount$"),
            ],
            INVOICE_BENEFICIARY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_beneficiary),
                CallbackQueryHandler(edit_name, pattern="^edit_name$"),
            ],
            INVOICE_TAX_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tax_id),
                CallbackQueryHandler(skip_tax_id, pattern="^skip_tax_id$"),
            ],
            INVOICE_DESCRIPTION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    handle_description_text,
                ),
                CallbackQueryHandler(
                    handle_description_choice, pattern=r"^desc_\d+$"
                ),
                CallbackQueryHandler(
                    ask_for_manual_description, pattern="^desc_manual$"
                ),
                CallbackQueryHandler(skip_description, pattern="^skip_description$"),
            ],
            INVOICE_CONFIRM: [
                CallbackQueryHandler(confirm_invoice_handler, pattern="^confirm_invoice$"),
                CallbackQueryHandler(edit_amount, pattern="^edit_amount$"),
                CallbackQueryHandler(edit_name, pattern="^edit_name$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_invoice, pattern="^cancel_invoice$"),
            CommandHandler("cancel", cancel_command),
            CommandHandler("start", start_handler),
        ],
        per_message=False,
        allow_reentry=True,
    )

    # ── Register all handlers ──
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(invoice_conv)

    # Menu callbacks (handled outside the ConversationHandler)
    app.add_handler(CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"))
    app.add_handler(CallbackQueryHandler(check_balance, pattern="^check_balance$"))
    app.add_handler(CallbackQueryHandler(show_overdue, pattern="^overdue$"))
    app.add_handler(CallbackQueryHandler(show_my_invoices, pattern="^my_invoices$"))
    app.add_handler(CallbackQueryHandler(show_settings, pattern="^settings$"))
    app.add_handler(CallbackQueryHandler(toggle_morning_digest, pattern="^toggle_digest$"))
    app.add_handler(CallbackQueryHandler(show_help, pattern="^help$"))
    app.add_handler(CallbackQueryHandler(record_payment_placeholder, pattern="^record_payment$"))
    app.add_handler(CallbackQueryHandler(discard_draft, pattern="^discard_draft$"))
    app.add_handler(CallbackQueryHandler(trigger_reminders, pattern="^trigger_reminders$"))

    return app


async def init_application(token: str) -> Application:
    """
    Initialize the global Application at FastAPI startup.
    Must be awaited.

    USAGE (in main.py):
        from app.services.telegram_bot import init_application
        await init_application(os.getenv("TELEGRAM_BOT_TOKEN"))
    """
    global _application
    _application = build_application(token)
    await _application.initialize()
    await _application.start()
    print("[TELEGRAM_BOT] Application initialized and started")
    return _application


async def shutdown_application() -> None:
    """Gracefully stop the Application at FastAPI shutdown."""
    global _application
    if _application:
        await _application.stop()
        await _application.shutdown()
        print("[TELEGRAM_BOT] Application shut down")
