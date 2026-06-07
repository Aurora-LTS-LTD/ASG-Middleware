"""
ASG Solutions — Allocation Retry Queue
=======================================
Background worker that retries ITA allocation requests for invoices
that failed on the first attempt (5% mock failure rate, and the real
ITA endpoint will have outages too).

HOW IT WORKS:
  1. Every 30 seconds, query for invoices with:
       status = "pending_allocation"
       allocation_next_retry_at <= now
       allocation_retry_count < MAX_RETRIES (10)
  2. For each, call request_allocation_number() again.
  3. On success: finalize the invoice, generate PDF, notify user via Telegram.
  4. On failure: increment retry_count, schedule next retry using
     exponential backoff (30s → 2m → 10m → 1h → 1h → ...).

REAL-WORLD ANALOGY:
  Like a postal worker who re-delivers a package when the recipient
  wasn't home. They don't give up after one try — they try again
  tomorrow, and the day after, until the package is delivered.

LIFECYCLE OF A PENDING-ALLOCATION INVOICE:
  draft → [user confirms in Telegram] → pending_allocation
        → [worker retries, ITA finally responds] → finalized
        → [worker sends PDF to user via Telegram]

  If MAX_RETRIES is reached, the invoice stays as pending_allocation
  and the user gets a final "please contact support" message.
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import asyncio
import datetime
import os

from aurora_shared.database import SessionLocal, Invoice, ActionLog
# Sprint 3 — dispatcher chooses mock vs production via ITA_BACKEND env.
# The function signature is preserved exactly; allocation_queue's caller
# code below is unchanged.
from app.services.ita import request_allocation_number
from app.services.invoice_service import finalize_invoice, AllocationFailedError

# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────
MAX_RETRIES = 10

# Delay between retries in seconds (exponential backoff pattern).
# Index = retry_count at the time of failure (0 = first retry, etc.)
RETRY_DELAYS = [30, 120, 600, 3600, 3600, 3600, 3600, 3600, 3600, 3600]
#               30s  2m   10m   1h    1h    1h    1h    1h    1h    1h


# ─────────────────────────────────────────────────────────────
# FUNCTION: process_pending_allocations
# ─────────────────────────────────────────────────────────────
async def process_pending_allocations(bot=None) -> int:
    """
    Process all invoices currently waiting for an allocation retry.

    PARAMETER:
      bot — a python-telegram-bot Bot instance (optional).
             If provided, sends Telegram notifications on success/failure.

    RETURNS: number of invoices successfully finalized in this run
    """
    db = SessionLocal()
    now = datetime.datetime.utcnow()
    finalized_count = 0

    try:
        # ── Find invoices ready for a retry attempt ──
        pending = (
            db.query(Invoice)
            .filter(
                Invoice.status == "pending_allocation",
                Invoice.allocation_next_retry_at <= now,
                Invoice.allocation_retry_count < MAX_RETRIES,
            )
            .all()
        )

        if pending:
            print(f"[ALLOC_QUEUE] Found {len(pending)} invoice(s) ready for retry")

        for invoice in pending:
            print(f"[ALLOC_QUEUE] Retrying allocation for {invoice.invoice_number} "
                  f"(attempt #{invoice.allocation_retry_count + 1})")

            # P1-05: resolve real seller tax_id; if the Business profile
            # is incomplete, surface as a non-retriable error so the row
            # doesn't churn through MAX_RETRIES with a placeholder.
            from app.services.tax_id_resolver import (
                resolve_seller_tax_id,
                SellerTaxIdMissing,
            )
            try:
                seller_tax_id = resolve_seller_tax_id(invoice, db)
            except SellerTaxIdMissing as exc:
                print(f"[ALLOC_QUEUE] {exc} — marking failed (non-retriable)")
                invoice.allocation_status = "rejected"
                invoice.allocation_retry_count = MAX_RETRIES  # halt retries
                db.commit()
                continue

            buyer_tax_id = invoice.beneficiary_tax_id or "000000000"

            try:
                # Sprint 3 — pass idempotency context. Mock backend
                # ignores the kwargs; production backend uses them.
                ita_response = await request_allocation_number(
                    seller_tax_id=seller_tax_id,
                    buyer_tax_id=buyer_tax_id,
                    amount=invoice.amount_total,
                    invoice_id=invoice.id,
                    retry_count=invoice.allocation_retry_count or 0,
                    organization_id=getattr(invoice, "organization_id", None),
                )
            except Exception as e:
                ita_response = {"success": False, "error": str(e)}

            if ita_response.get("success"):
                # ── Success: record allocation, then finalize ──
                # finalize_invoice() now accepts pending_allocation directly
                # (with allocation_status already "approved" it skips the ITA
                # call), so no status hack is needed.
                invoice.allocation_number = ita_response["allocation_number"]
                invoice.allocation_status = "approved"
                db.commit()

                try:
                    result = await finalize_invoice(
                        db=db,
                        invoice_id=invoice.id,
                        lang="he",              # Telegram pilot is Hebrew-only
                        actor_label="allocation_queue",
                    )
                    finalized_count += 1
                    print(f"[ALLOC_QUEUE] ✅ {invoice.invoice_number} finalized — "
                          f"allocation #{ita_response['allocation_number']}")

                    # ── Notify via Telegram if bot is available ──
                    await _notify_allocation_success(bot, invoice, result, db)

                    # ── Also notify via WhatsApp (no bot arg needed) ──
                    await _notify_whatsapp_allocation_success(invoice, result, db)

                except Exception as e:
                    print(f"[ALLOC_QUEUE] ⚠️ Finalize failed after allocation for "
                          f"{invoice.invoice_number}: {e}")

            else:
                # ── Failure: schedule next retry ──
                invoice.allocation_retry_count += 1
                delay_idx = min(invoice.allocation_retry_count - 1, len(RETRY_DELAYS) - 1)
                delay_seconds = RETRY_DELAYS[delay_idx]
                invoice.allocation_next_retry_at = now + datetime.timedelta(seconds=delay_seconds)
                db.commit()

                print(f"[ALLOC_QUEUE] ❌ {invoice.invoice_number} retry #{invoice.allocation_retry_count} "
                      f"failed — next attempt in {delay_seconds}s")

                # ── If max retries exhausted, mark terminal + notify ──
                if invoice.allocation_retry_count >= MAX_RETRIES:
                    # Terminal: was silently stuck in pending_allocation forever.
                    invoice.allocation_status = "rejected"
                    db.commit()
                    print(f"[ALLOC_QUEUE] 🛑 {invoice.invoice_number} exceeded max retries — marked rejected")
                    db.add(ActionLog(
                        business_id=invoice.business_id,
                        status="error",
                        detail=f"Invoice {invoice.invoice_number}: allocation failed after "
                               f"{MAX_RETRIES} attempts — manual intervention required",
                    ))
                    db.commit()
                    await _notify_allocation_exhausted(bot, invoice, db)
                    await _notify_whatsapp_allocation_exhausted(invoice, db)

    finally:
        db.close()

    return finalized_count


# ─────────────────────────────────────────────────────────────
# HELPER: send Telegram success notification
# ─────────────────────────────────────────────────────────────
async def _notify_allocation_success(bot, invoice: Invoice, result: dict, db) -> None:
    """
    Edit the pending-allocation placeholder message with the success result
    and send the PDF as a Telegram document.
    """
    if not bot:
        return

    from aurora_shared.database import TelegramSession, User
    from app.services.telegram_identity import get_user_by_telegram_id

    # ── Find the TelegramSession that started this invoice ──
    session = db.query(TelegramSession).filter(
        TelegramSession.pending_invoice_id == invoice.id
    ).first()
    if not session or not session.telegram_user_id:
        return

    chat_id = session.telegram_user_id
    alloc_num = invoice.allocation_number or "—"
    total = f"{invoice.amount_total:,.2f}"

    # ── Edit the "⏳ ממתין..." message in-place ──
    if session.pending_message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=session.pending_message_id,
                text=(
                    f"✅ <b>החשבונית אושרה!</b>\n\n"
                    f"📄 <b>{invoice.invoice_number}</b>\n"
                    f"💰 סכום כולל: <b>{total} ₪</b>\n"
                    f"🔢 מספר הקצאה: <b>{alloc_num}</b>"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"[ALLOC_QUEUE] Could not edit message: {e}")

    # ── Send the PDF ──
    pdf_url = result.get("pdf_url")
    if pdf_url:
        disk_path = "app" + pdf_url  # "/static/pdfs/..." → "app/static/pdfs/..."
        import os
        if os.path.exists(disk_path):
            try:
                with open(disk_path, "rb") as pdf_file:
                    await bot.send_document(
                        chat_id=chat_id,
                        document=pdf_file,
                        filename=f"{invoice.invoice_number}.pdf",
                        caption=(
                            f"📄 {invoice.invoice_number}\n"
                            f"💰 {total} ₪ | 🔢 הקצאה: {alloc_num}"
                        ),
                    )
            except Exception as e:
                print(f"[ALLOC_QUEUE] Could not send PDF via Telegram: {e}")

    # ── Clear the pending fields from the session ──
    session.pending_message_id = None
    session.pending_invoice_id = None
    db.commit()


# ─────────────────────────────────────────────────────────────
# HELPER: send Telegram "max retries exhausted" notification
# ─────────────────────────────────────────────────────────────
async def _notify_allocation_exhausted(bot, invoice: Invoice, db) -> None:
    """Tell the user that allocation failed permanently — manual action needed."""
    if not bot:
        return

    from aurora_shared.database import TelegramSession

    session = db.query(TelegramSession).filter(
        TelegramSession.pending_invoice_id == invoice.id
    ).first()
    if not session or not session.telegram_user_id:
        return

    try:
        if session.pending_message_id:
            await bot.edit_message_text(
                chat_id=session.telegram_user_id,
                message_id=session.pending_message_id,
                text=(
                    f"❌ <b>לא ניתן לקבל מספר הקצאה</b>\n\n"
                    f"שירות רשות המסים אינו זמין לאחר {MAX_RETRIES} ניסיונות.\n"
                    f"חשבונית: <b>{invoice.invoice_number}</b>\n\n"
                    f"אנא פנה לתמיכה או נסה שוב מהדשבורד."
                ),
                parse_mode="HTML",
            )
    except Exception as e:
        print(f"[ALLOC_QUEUE] Could not send exhausted notice: {e}")

    session.pending_message_id = None
    session.pending_invoice_id = None
    db.commit()


# ─────────────────────────────────────────────────────────────
# HELPER: send WhatsApp success notification
# ─────────────────────────────────────────────────────────────
async def _notify_whatsapp_allocation_success(invoice: Invoice, result: dict, db) -> None:
    """
    If this invoice was created through the WhatsApp bot, tell the
    WhatsApp user their allocation came through and deliver the PDF.

    The link between "invoice X was started on WhatsApp" is the
    WhatsAppSession row whose `pending_invoice_id == invoice.id`.
    """
    from aurora_shared.database import WhatsAppSession
    from app.services import whatsapp_meta_client as wa
    from app.services.whatsapp_strings import normalize_lang, t

    session = (
        db.query(WhatsAppSession)
        .filter(WhatsAppSession.pending_invoice_id == invoice.id)
        .first()
    )
    if not session or not session.whatsapp_phone_e164:
        return

    lang = normalize_lang(session.locale)
    phone = session.whatsapp_phone_e164

    # ── Announce the success ──
    try:
        await wa.send_text(
            phone,
            t(
                "allocation_arrived",
                lang,
                allocation=invoice.allocation_number or "—",
                invoice_number=invoice.invoice_number,
            ),
            db=db,
            user_id=session.user_id,
        )
    except Exception as e:
        print(f"[ALLOC_QUEUE] WA notify failed (non-fatal): {e}")

    # ── Send the PDF if we have a public URL ──
    pdf_url = result.get("pdf_url")
    if pdf_url and pdf_url.startswith("http"):
        try:
            caption = t(
                "invoice_caption",
                lang,
                invoice_number=invoice.invoice_number,
                total=invoice.amount_total or 0,
            )
            await wa.send_document(
                phone,
                document_link=pdf_url,
                filename=f"{invoice.invoice_number}.pdf",
                caption=caption,
                db=db,
                user_id=session.user_id,
            )
        except Exception as e:
            print(f"[ALLOC_QUEUE] WA PDF send failed (non-fatal): {e}")

    # ── Reset session back to idle ──
    session.pending_invoice_id = None
    session.pending_message_id = None
    session.state = None
    session.draft_payload_json = None
    db.commit()


# ─────────────────────────────────────────────────────────────
# HELPER: send WhatsApp "max retries exhausted" notification
# ─────────────────────────────────────────────────────────────
async def _notify_whatsapp_allocation_exhausted(invoice: Invoice, db) -> None:
    """Tell the WhatsApp user the allocation failed permanently."""
    from aurora_shared.database import WhatsAppSession
    from app.services import whatsapp_meta_client as wa

    session = (
        db.query(WhatsAppSession)
        .filter(WhatsAppSession.pending_invoice_id == invoice.id)
        .first()
    )
    if not session or not session.whatsapp_phone_e164:
        return

    # Short, transport-agnostic text. No template_name → relies on the
    # 24-hour window. If the user hasn't messaged in 24h this will fail;
    # in production we'd gate on can_send_freeform() and fall back to a
    # pre-approved template.
    try:
        await wa.send_text(
            session.whatsapp_phone_e164,
            (
                f"❌ לא ניתן לקבל מספר הקצאה לחשבונית "
                f"{invoice.invoice_number} לאחר {MAX_RETRIES} ניסיונות. "
                f"פנה לתמיכה או נסה מחדש מהדשבורד."
            ),
            db=db,
            user_id=session.user_id,
        )
    except Exception as e:
        print(f"[ALLOC_QUEUE] WA exhaust notify failed (non-fatal): {e}")

    session.pending_invoice_id = None
    session.pending_message_id = None
    session.state = None
    session.draft_payload_json = None
    db.commit()


# ─────────────────────────────────────────────────────────────
# BACKGROUND TASK: allocation_retry_loop
# ─────────────────────────────────────────────────────────────
async def allocation_retry_loop(bot=None) -> None:
    """
    Long-running asyncio task. Runs every 30 seconds forever.
    Pass as a background task to FastAPI startup.

    USAGE (in main.py):
        asyncio.create_task(allocation_retry_loop(bot=application.bot))
    """
    print("[ALLOC_QUEUE] Allocation retry worker started (polling every 30s)")
    while True:
        try:
            count = await process_pending_allocations(bot=bot)
            if count > 0:
                print(f"[ALLOC_QUEUE] Finalized {count} invoice(s) this cycle")
        except Exception as e:
            # Worker must never crash — log and continue
            print(f"[ALLOC_QUEUE] ⚠️ Worker error (continuing): {e}")
        await asyncio.sleep(30)
