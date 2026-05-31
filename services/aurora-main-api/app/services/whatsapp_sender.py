"""
ASG Solutions — WhatsApp Invoice Sender
========================================
This file formats invoice data into a human-readable message
and sends it to the customer via WhatsApp (through Make.com).

REAL-WORLD ANALOGY:
Think of this as the "printer + delivery person" for invoices:
  1. The printer formats the invoice into a nice text message
  2. The delivery person sends it to the customer's WhatsApp

The formatting happens here, the actual sending goes through
Make.com (via make_service.py).
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
from app.services.make_service import send_to_make


# ─────────────────────────────────────────────────────────────
# FUNCTION: format_invoice_message
# ─────────────────────────────────────────────────────────────
# PURPOSE:
#   Take invoice data (a dictionary) and turn it into a clean,
#   readable text message suitable for WhatsApp.
#
# REAL-WORLD ANALOGY:
#   Imagine you have a spreadsheet with all the invoice numbers.
#   This function turns that dry data into a nicely formatted
#   message that a person can actually read on their phone.
#
# PARAMETERS:
#   invoice (dict) — the invoice data (from the API response)
#
# RETURNS:
#   str — a formatted text message
# ─────────────────────────────────────────────────────────────
def format_invoice_message(invoice: dict) -> str:
    """Format invoice data into a WhatsApp-friendly text message."""

    # ── Build the allocation status line ──
    # Different messages depending on the allocation status
    alloc_status = invoice.get("allocation_status", "pending")
    if alloc_status == "approved":
        alloc_line = f"Allocation Number: {invoice.get('allocation_number', 'N/A')}"
    elif alloc_status == "not_required":
        alloc_line = "Allocation: Not required (below threshold)"
    elif alloc_status == "failed":
        alloc_line = "Allocation: FAILED — please retry"
    else:
        alloc_line = "Allocation: Pending"

    # ── Build the full message ──
    # Using triple quotes for a multi-line string.
    # The backslash at the end of each line prevents extra blank lines.
    message = (
        f"{'=' * 35}\n"
        f"  ASG Solutions — Invoice\n"
        f"{'=' * 35}\n"
        f"\n"
        f"Invoice #: {invoice.get('invoice_number', 'N/A')}\n"
        f"Date: {invoice.get('created_at', 'N/A')}\n"
        f"\n"
        f"To: {invoice.get('beneficiary_name', 'N/A')}\n"
        f"Tax ID: {invoice.get('beneficiary_tax_id', 'N/A')}\n"
        f"\n"
        f"{'─' * 35}\n"
        f"  Amount (before tax): {invoice.get('amount_net', 0):,.2f} {invoice.get('currency', 'ILS')}\n"
        f"  VAT ({int(invoice.get('vat_rate', 0.18) * 100)}%): {invoice.get('vat_amount', 0):,.2f} {invoice.get('currency', 'ILS')}\n"
        f"  TOTAL: {invoice.get('amount_total', 0):,.2f} {invoice.get('currency', 'ILS')}\n"
        f"{'─' * 35}\n"
        f"\n"
        f"Status: {invoice.get('status', 'draft').upper()}\n"
        f"{alloc_line}\n"
        f"\n"
        f"{'=' * 35}\n"
        f"  Powered by ASG Solutions\n"
        f"{'=' * 35}"
    )

    return message


# ─────────────────────────────────────────────────────────────
# FUNCTION: send_invoice_via_whatsapp
# ─────────────────────────────────────────────────────────────
# PURPOSE:
#   Format an invoice and send it to a customer via WhatsApp.
#   This is the "one-stop" function — give it invoice data and
#   a phone number, and it handles everything.
#
# PARAMETERS:
#   invoice_data (dict)    — the invoice details
#   recipient_phone (str)  — the customer's WhatsApp number
#
# RETURNS:
#   dict — the Make.com response, or None if failed
# ─────────────────────────────────────────────────────────────
async def send_invoice_via_whatsapp(
    invoice_data: dict,
    recipient_phone: str,
) -> dict | None:
    """
    Format and send an invoice to a customer via WhatsApp.

    Args:
        invoice_data:    Invoice dict from the API.
        recipient_phone: Customer's WhatsApp phone number.

    Returns:
        Make.com response dict, or None on failure.
    """

    # ── Step 1: Format the invoice into a readable message ──
    formatted_message = format_invoice_message(invoice_data)

    print(f"[WHATSAPP] Sending invoice {invoice_data.get('invoice_number')} to {recipient_phone}")

    # ── Step 2: Send via Make.com ──
    # "sender" is the system (not a human), so we mark it as such.
    # "message" is the formatted invoice text.
    # "business_id" helps Make.com route the message correctly.
    result = await send_to_make(
        sender=recipient_phone,
        message=formatted_message,
        business_id=str(invoice_data.get("business_id", "")),
    )

    if result:
        print(f"[WHATSAPP] ✅ Invoice sent successfully!")
    else:
        print(f"[WHATSAPP] ❌ Failed to send invoice")

    return result
