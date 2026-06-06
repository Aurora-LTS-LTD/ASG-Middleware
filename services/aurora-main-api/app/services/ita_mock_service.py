"""
ASG Solutions — Israel Tax Authority (ITA / שא"מ) Mock API Service
===================================================================
This file SIMULATES communication with the Israel Tax Authority's
allocation number system. In production, this would connect to the
real government API. For now, it returns fake (but realistic) responses
so we can build and test the rest of the system.

IMPORTANT: THIS IS A MOCK (SIMULATION) FOR DEVELOPMENT ONLY.
When we go to production, we will replace the fake logic inside
these functions with real HTTP calls to the government servers.

═══════════════════════════════════════════════════════════════
WHAT IS AN ALLOCATION NUMBER (מספר הקצאה)?
═══════════════════════════════════════════════════════════════

An allocation number is a unique 9-digit code that the Tax Authority
issues for large invoices. Think of it like a "stamp of approval"
from the government.

REAL-WORLD ANALOGY:
  Imagine you're selling a car. For small sales (like selling a
  used phone), you just write a receipt and you're done. But for
  expensive items (like a car), the government says: "Before you
  write the receipt, call us first. We'll give you a special
  approval number to put on the receipt."

  That approval number = the allocation number (מספר הקצאה).
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
# "asyncio" — Python's tool for doing things asynchronously.
#   We use asyncio.sleep() to simulate the 1-3 second delay that
#   the real government API would have.
#
# "random" — Python's random number generator.
#   We use it to generate fake 9-digit allocation numbers and to
#   simulate the occasional failure (5% chance).
#
# "datetime" — Python's date and time tools.
#   We use it to create timestamps (recording WHEN the allocation
#   was issued).
import asyncio
import random
from datetime import datetime, date


# ─────────────────────────────────────────────────────────────
# FUNCTION: request_allocation_number (MOCK)
# ─────────────────────────────────────────────────────────────
# PURPOSE:
#   Simulate requesting an allocation number from the ITA.
#   In production, this would send a real HTTPS request to the
#   government API. For now, it waits 1 second and returns a
#   fake response.
#
# REAL-WORLD ANALOGY:
#   Imagine calling a government office to get approval for a
#   large invoice. You dial the number (the API call), wait on
#   hold for a few seconds (the 1-second delay), and then the
#   clerk either gives you an approval number (success, 95%) or
#   says "Sorry, our system is down" (failure, 5%).
#
# PARAMETERS:
#   seller_tax_id (str) — the seller's tax ID (ע.מ / ח.פ)
#   buyer_tax_id (str)  — the buyer's tax ID
#   amount (float)      — the invoice total in NIS
#   invoice_date (str)  — the invoice date (ISO format). Defaults to today.
#
# RETURNS:
#   On SUCCESS (95%):
#   {
#     "success": True,
#     "allocation_number": "537291846",  ← random 9-digit number
#     "message": "Allocation approved",
#     "timestamp": "2026-04-13T14:30:00"
#   }
#
#   On FAILURE (5%):
#   {
#     "success": False,
#     "allocation_number": None,
#     "message": "ITA service temporarily unavailable",
#     "timestamp": "2026-04-13T14:30:00"
#   }
#
# WHY IS THIS FUNCTION "async"?
#   "async" means this function can pause and let other things
#   happen while it waits. While we wait 1 second for the fake
#   government response, the server can handle other requests.
#   It's like a waiter who takes another table's order while
#   your food is being cooked.
# ─────────────────────────────────────────────────────────────
async def request_allocation_number(
    seller_tax_id: str,
    buyer_tax_id: str,
    amount: float,
    invoice_date: str = None,
) -> dict:
    """
    MOCK — Simulate requesting an allocation number from the Israel
    Tax Authority (שא"מ) API.

    Args:
        seller_tax_id: The seller's tax ID (ע.מ / ח.פ).
        buyer_tax_id:  The buyer's tax ID.
        amount:        The invoice total in NIS.
        invoice_date:  The invoice date (ISO string). Defaults to today.

    Returns:
        A dict with success status, allocation number (or None), message,
        and timestamp.
    """

    # ── Step 1: Default the invoice date to today if not provided ──
    if invoice_date is None:
        invoice_date = date.today().isoformat()

    # ── Step 2: Log the request ──
    # "[ITA-MOCK]" prefix makes it easy to spot these logs in the terminal.
    print(f"[ITA-MOCK] Requesting allocation for amount {amount} NIS")
    print(f"[ITA-MOCK] Seller: {seller_tax_id} | Buyer: {buyer_tax_id} | Date: {invoice_date}")

    # ── Step 3: Simulate network delay (1 second) ──
    # The real government API takes 1-3 seconds to respond.
    await asyncio.sleep(1)

    # ── Step 4: Record the current timestamp ──
    timestamp = datetime.utcnow().isoformat()

    # ── Step 5: Simulate occasional failure (5% chance) ──
    # This helps us test our error handling code.
    if random.random() < 0.05:
        print(f"[ITA-MOCK] *** SIMULATED FAILURE *** Service temporarily unavailable")
        return {
            "success":           False,
            "allocation_number": None,
            "message":           "ITA service temporarily unavailable",
            "timestamp":         timestamp,
        }

    # ── Step 6: Generate a fake 9-digit allocation number ──
    # In the real API, this number would come from the government.
    allocation_number = str(random.randint(100_000_000, 999_999_999))

    # ── Step 7: Log success and return ──
    print(f"[ITA-MOCK] Allocation APPROVED: {allocation_number}")

    return {
        "success":           True,
        "allocation_number": allocation_number,
        "message":           "Allocation approved",
        "timestamp":         timestamp,
    }
