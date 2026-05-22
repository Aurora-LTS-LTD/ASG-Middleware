"""
ASG Solutions -- Reminder Service
================================
Detects overdue invoices and prepares WhatsApp reminders.

REAL-WORLD ANALOGY:
Think of this as the collections department:
  1. Check the calendar — any invoices past their due date?
  2. Check the logbook — did we already remind them this week?
  3. If not, prepare a reminder message and send it via WhatsApp.
  4. Record in the logbook that we sent a reminder (don't spam).

REMINDER RULES:
  - Only invoices that are overdue (past due_date) and not fully paid
  - Maximum ONE reminder per invoice per 7 days (anti-spam)
  - Only invoices with a beneficiary_contact (phone/email) get reminders
  - The reminder includes: invoice number, amount owed, days overdue

FUTURE INTEGRATION (Phase 4):
  Once the language_service is built, reminders will be sent in the
  customer's preferred language (Arabic, Hebrew, or English).
"""

# -----------------------------------------------------------------
# IMPORTS
# -----------------------------------------------------------------
import datetime

from sqlalchemy.orm import Session

from app.database.models import ActionLog
from app.services.payment_service import get_overdue_invoices
from app.services.whatsapp_sender import send_invoice_via_whatsapp


# -----------------------------------------------------------------
# FUNCTION: format_reminder_message
# -----------------------------------------------------------------
# PURPOSE:
#   Create a human-readable reminder message for an overdue invoice.
#   Currently in English — Phase 4 will add Arabic/Hebrew templates.
#
# PARAMETERS:
#   overdue_info (dict) -- one item from get_overdue_invoices()
#
# RETURNS:
#   str -- formatted reminder text
# -----------------------------------------------------------------
def format_reminder_message(overdue_info: dict) -> str:
    """Format a payment reminder message for an overdue invoice."""

    return (
        f"{'=' * 35}\n"
        f"  ASG Solutions — Payment Reminder\n"
        f"{'=' * 35}\n"
        f"\n"
        f"Dear {overdue_info['beneficiary_name']},\n"
        f"\n"
        f"This is a friendly reminder that invoice "
        f"{overdue_info['invoice_number']} is overdue.\n"
        f"\n"
        f"{'─' * 35}\n"
        f"  Invoice Total:  {overdue_info['amount_total']:,.2f} ILS\n"
        f"  Amount Paid:    {overdue_info['amount_paid']:,.2f} ILS\n"
        f"  Remaining:      {overdue_info['remaining']:,.2f} ILS\n"
        f"  Days Overdue:   {overdue_info['days_overdue']}\n"
        f"{'─' * 35}\n"
        f"\n"
        f"Please arrange payment at your earliest convenience.\n"
        f"\n"
        f"{'=' * 35}\n"
        f"  Powered by ASG Solutions\n"
        f"{'=' * 35}"
    )


# -----------------------------------------------------------------
# FUNCTION: send_overdue_reminders
# -----------------------------------------------------------------
# PURPOSE:
#   Scan all overdue invoices, check if a reminder was already sent
#   within the last 7 days, and send reminders for those that haven't
#   been reminded recently.
#
# REAL-WORLD ANALOGY:
#   The boss says "follow up on late payments." You go through the
#   stack, skip the ones you already called this week, and call
#   the rest. Then you write down who you called.
#
# PARAMETERS:
#   db (Session)             -- database session
#   business_id (int|None)   -- filter by business (None = all)
#
# RETURNS:
#   dict -- {reminders_sent, skipped_no_contact, skipped_recent_reminder}
# -----------------------------------------------------------------
async def send_overdue_reminders(
    db: Session,
    business_id: int | None = None,
) -> dict:
    """Send WhatsApp reminders for all overdue invoices."""

    overdue_list = get_overdue_invoices(db, business_id)

    if not overdue_list:
        print("[REMINDERS] No overdue invoices found")
        return {"reminders_sent": 0, "skipped_no_contact": 0, "skipped_recent_reminder": 0}

    now = datetime.datetime.utcnow()
    seven_days_ago = now - datetime.timedelta(days=7)

    sent_count = 0
    skipped_no_contact = 0
    skipped_recent = 0

    for item in overdue_list:
        # ── Skip if no contact info ──
        if not item.get("beneficiary_contact"):
            skipped_no_contact += 1
            continue

        # ── Check if reminder was already sent in the last 7 days ──
        recent_reminder = (
            db.query(ActionLog)
            .filter(
                ActionLog.status == "reminder_sent",
                ActionLog.detail.contains(item["invoice_number"]),
                ActionLog.triggered_at >= seven_days_ago,
            )
            .first()
        )

        if recent_reminder:
            skipped_recent += 1
            continue

        # ── Format and send the reminder ──
        message = format_reminder_message(item)

        # Build a minimal invoice_data dict for the whatsapp_sender
        invoice_data = {
            "invoice_number": item["invoice_number"],
            "business_id": item["business_id"],
        }

        print(
            f"[REMINDERS] Sending reminder for {item['invoice_number']} "
            f"to {item['beneficiary_contact']} "
            f"({item['days_overdue']} days overdue, "
            f"{item['remaining']:.2f} ILS remaining)"
        )

        # Send reminder as the message text (the sender formats it)
        from app.services.make_service import send_to_make
        await send_to_make(
            sender=item["beneficiary_contact"],
            message=message,
            business_id=str(item["business_id"]),
        )

        # ── Log the reminder ──
        log = ActionLog(
            business_id=item["business_id"],
            status="reminder_sent",
            detail=(
                f"Payment reminder sent for {item['invoice_number']} "
                f"to {item['beneficiary_contact']} — "
                f"{item['remaining']:.2f} ILS remaining, "
                f"{item['days_overdue']} days overdue"
            ),
        )
        db.add(log)
        db.commit()

        sent_count += 1

    result = {
        "reminders_sent": sent_count,
        "skipped_no_contact": skipped_no_contact,
        "skipped_recent_reminder": skipped_recent,
    }

    print(f"[REMINDERS] Done: {result}")
    return result
