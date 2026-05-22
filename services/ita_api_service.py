"""
ASG Solutions — Israel Tax Authority (ITA / שא"מ) API Service (MOCK)
=====================================================================
This file SIMULATES communication with the Israel Tax Authority's
allocation number system. In production, this would connect to the
real government API. For now, it returns fake (but realistic) responses
so we can build and test the rest of the system.

IMPORTANT: THIS IS A MOCK (SIMULATION) FOR DEVELOPMENT ONLY.
When we go to production, we will replace the fake logic inside
these functions with real HTTP calls to the government servers.

═══════════════════════════════════════════════════════════════
BACKGROUND — WHAT IS THE ITA / שא"מ?
═══════════════════════════════════════════════════════════════

ITA = Israel Tax Authority (רשות המסים בישראל)
שא"מ = שירות עיבודים ממוחשבים (Computerized Processing Service)
      This is the technical arm of the Tax Authority that runs the
      digital systems, APIs, and databases.

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
  approval number to put on the receipt. This way we KNOW the
  sale really happened and nobody is faking invoices to cheat
  on taxes."

  That approval number = the allocation number (מספר הקצאה).

WHY DOES THIS EXIST?
  Israel has a problem with fake invoices ("חשבוניות פיקטיביות").
  Some businesses create invoices for transactions that never
  happened, to reduce their tax payments. The allocation number
  system forces businesses to register large invoices with the
  government IN REAL TIME, making it much harder to fake them.

═══════════════════════════════════════════════════════════════
HOW THE REAL API WOULD WORK (in production)
═══════════════════════════════════════════════════════════════

1. AUTHENTICATION:
   - The business needs a digital certificate (תעודה דיגיטלית)
     issued by the Tax Authority. This is like a digital ID card
     that proves who you are.
   - The certificate is used to sign every API request (like
     signing a document with your personal signature).

2. THE REQUEST:
   - Format: XML or JSON (structured data formats)
   - Sent via HTTPS (encrypted internet connection)
   - Contains: seller tax ID (ע.מ/ח.פ), buyer tax ID, invoice
     amount, invoice date, invoice type, and other details.

3. THE RESPONSE:
   - If approved: returns a 9-digit allocation number + timestamp
   - If rejected: returns an error code + reason
     (e.g., seller's tax file is blocked, amount mismatch, etc.)

4. TIMING:
   - The real API typically responds within 1-3 seconds.
   - The allocation number must appear on the printed/digital invoice.
   - The number is valid only for the specific transaction it was
     issued for — you cannot reuse it.

5. ENDPOINT (real):
   - The actual government API endpoint is provided by שא"מ
     after registration and certification.
   - URL pattern: https://ita-api.taxes.gov.il/... (approximate)

═══════════════════════════════════════════════════════════════
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
# "asyncio" — Python's tool for doing things asynchronously (not blocking).
#   We use asyncio.sleep() to simulate the 1-3 second delay that the
#   real government API would have.
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
# FUNCTION 1: request_allocation_number (MOCK)
# ─────────────────────────────────────────────────────────────
# PURPOSE:
#   Simulate requesting an allocation number from the ITA.
#   In production, this would send a real HTTPS request to the
#   government API. For now, it waits 1 second (simulating network
#   delay) and returns a fake response.
#
# REAL-WORLD ANALOGY:
#   Imagine calling a government office to get approval for a
#   large invoice. You dial the number (the API call), wait on
#   hold for a few seconds (the 1-second delay), and then the
#   clerk either gives you an approval number (success) or says
#   "Sorry, our system is down, try again later" (failure).
#   This function simulates that entire phone call.
#
# PARAMETERS:
#   seller_tax_id (str) — the seller's tax ID number (ע.מ / ח.פ)
#                         e.g., "515123456"
#   buyer_tax_id (str)  — the buyer's tax ID number
#                         e.g., "514987654"
#   amount (float)      — the invoice total in NIS (e.g., 15000.0)
#   invoice_date (str)  — the invoice date as a string (ISO format:
#                         "2026-04-06"). If not provided, uses today.
#
# RETURNS:
#   A dictionary with the API response:
#   On SUCCESS (95% of the time):
#   {
#     "success": True,
#     "allocation_number": "123456789",   ← random 9-digit number
#     "message": "Allocation approved",
#     "timestamp": "2026-04-06T14:30:00"  ← ISO format datetime
#   }
#
#   On FAILURE (5% of the time — simulating server issues):
#   {
#     "success": False,
#     "allocation_number": None,
#     "message": "ITA service temporarily unavailable",
#     "timestamp": "2026-04-06T14:30:00"
#   }
#
# WHY IS THIS FUNCTION "async"?
#   "async" means "this function can pause and let other things
#   happen while it waits." In our case, while we wait 1 second
#   for the fake government response, the server can handle other
#   incoming requests instead of freezing. It's like a waiter who
#   takes another table's order while your food is being cooked,
#   instead of standing next to your table doing nothing.
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

    In production, this function will be replaced with a real HTTPS
    call to the government API, authenticated with a digital certificate.

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
    # "date.today().isoformat()" gives us today's date as a string
    # like "2026-04-06". This is the standard format for dates in APIs.
    if invoice_date is None:
        invoice_date = date.today().isoformat()

    # ── Step 2: Log the request ──
    # "print()" writes a message to the terminal/console.
    # The "[ITA-API]" prefix makes it easy to spot these logs among
    # all the other messages the server prints.
    # In production, we'd use a proper logging system instead of print().
    print(f"[ITA-API] Requesting allocation for amount {amount} NIS")
    print(f"[ITA-API] Seller: {seller_tax_id} | Buyer: {buyer_tax_id} | Date: {invoice_date}")

    # ── Step 3: Simulate network delay ──
    # The real government API takes 1-3 seconds to respond.
    # "await asyncio.sleep(1)" pauses this function for 1 second
    # WITHOUT blocking the entire server.
    #
    # "await" = "pause here and come back when the sleep is done"
    # This is what makes "async" useful — while we sleep, the server
    # can handle other requests.
    await asyncio.sleep(1)

    # ── Step 4: Record the current timestamp ──
    # "datetime.utcnow()" = the current date and time in UTC
    #   (the universal time zone used by servers worldwide).
    # ".isoformat()" converts it to a string like "2026-04-06T14:30:00"
    #   (ISO 8601 format — the international standard for date strings).
    timestamp = datetime.utcnow().isoformat()

    # ── Step 5: Simulate occasional failure (5% chance) ──
    # "random.random()" returns a random decimal between 0.0 and 1.0.
    # If it's less than 0.05 (5%), we pretend the government server
    # is down. This helps us test our error handling code.
    #
    # Why 5%? In the real world, government APIs do occasionally fail
    # (maintenance windows, overloaded servers, network issues).
    # We want our system to handle this gracefully.
    if random.random() < 0.05:
        print(f"[ITA-API] *** SIMULATED FAILURE *** Service temporarily unavailable")
        return {
            "success":           False,
            "allocation_number": None,
            "message":           "ITA service temporarily unavailable",
            "timestamp":         timestamp,
        }

    # ── Step 6: Generate a fake allocation number ──
    # "random.randint(100_000_000, 999_999_999)" generates a random
    # 9-digit integer (between 100,000,000 and 999,999,999).
    # "str(...)" converts it to a string like "537291846".
    #
    # In the real API, this number would come from the government
    # server and would be tied to the specific transaction in their
    # database.
    allocation_number = str(random.randint(100_000_000, 999_999_999))

    # ── Step 7: Log success and return ──
    print(f"[ITA-API] Allocation APPROVED: {allocation_number}")

    return {
        "success":           True,
        "allocation_number": allocation_number,
        "message":           "Allocation approved",
        "timestamp":         timestamp,
    }
