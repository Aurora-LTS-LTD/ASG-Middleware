"""
ASG Solutions — Israeli Tax Compliance Engine
================================================
This file contains the tax logic for Israeli invoicing rules.

REAL-WORLD ANALOGY:
Think of this file as the "accountant's rulebook." When a business
issues an invoice, the accountant checks the rulebook to answer:
  1. Is this invoice big enough to need government approval? (allocation number)
  2. How much VAT (tax) do we add on top?
  3. What number do we stamp on the invoice?

This file answers all three questions in code.

BACKGROUND — ISRAELI TAX RULES:
In Israel, the Tax Authority (רשות המסים / שא"מ) introduced a system
called "allocation numbers" (מספר הקצאה) to fight fake invoices.
The idea: for large invoices, the seller must ask the government
for a special number BEFORE issuing the invoice. This proves the
invoice is real and approved.

The thresholds (the amount above which you MUST get an allocation number):
  - Before June 1, 2026: invoices of 10,000 NIS or more
  - From June 1, 2026 onward: invoices of 5,000 NIS or more
  (The government is gradually lowering the bar to catch more fraud.)
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
# "date" — a Python type that represents a calendar date (year, month, day).
#   We use it to check WHEN the invoice is issued, because the
#   threshold changes over time.
from datetime import date


# ─────────────────────────────────────────────────────────────
# CONSTANTS — the fixed numbers that define the rules
# ─────────────────────────────────────────────────────────────
# These are the thresholds set by Israeli tax law.
# "Threshold" = the line in the sand. Above it → you need an allocation number.

THRESHOLD_BEFORE_JUNE_2026 = 10_000.0   # 10,000 NIS (valid until May 31, 2026)
THRESHOLD_FROM_JUNE_2026   = 5_000.0    # 5,000 NIS  (valid from June 1, 2026 onward)

# The date when the threshold drops from 10,000 to 5,000
THRESHOLD_CHANGE_DATE = date(2026, 6, 1)   # June 1, 2026

# Israel's standard VAT rate (Value Added Tax / מע"מ)
# As of 2025-2026, the standard rate is 17%.
DEFAULT_VAT_RATE = 0.17


# ─────────────────────────────────────────────────────────────
# FUNCTION 1: check_tax_compliance
# ─────────────────────────────────────────────────────────────
# PURPOSE:
#   Given an invoice amount and date, determine whether the
#   invoice requires an allocation number from the Tax Authority.
#
# REAL-WORLD ANALOGY:
#   Imagine you're at a bank. For small cash withdrawals, you just
#   swipe your card. But for large withdrawals (say, above 10,000),
#   the bank calls a manager for approval. This function checks:
#   "Is this withdrawal big enough to need manager approval?"
#
# PARAMETERS:
#   amount (float)       — the invoice total in NIS (e.g., 7500.0)
#   invoice_date (date)  — the date of the invoice; if not provided,
#                          we assume today's date
#
# RETURNS:
#   A dictionary (like a labeled box) with four pieces of info:
#   {
#     "requires_allocation": True or False,
#     "threshold":           the current threshold (10000 or 5000),
#     "amount":              the amount you passed in,
#     "above_threshold":     True if amount >= threshold, else False
#   }
# ─────────────────────────────────────────────────────────────
def check_tax_compliance(amount: float, invoice_date: date = None) -> dict:
    """
    Check whether an invoice amount requires an ITA allocation number.

    Args:
        amount:       The invoice total in NIS.
        invoice_date: The date of the invoice. Defaults to today if not given.

    Returns:
        A dict with compliance details: requires_allocation, threshold,
        amount, and above_threshold.
    """

    # ── Step 1: If no date was provided, use today ──
    # "date.today()" asks Python: "What is today's date?"
    # We need the date to know WHICH threshold applies.
    if invoice_date is None:
        invoice_date = date.today()

    # ── Step 2: Pick the correct threshold based on the date ──
    # Before June 1, 2026 → the threshold is 10,000 NIS
    # From June 1, 2026 onward → the threshold drops to 5,000 NIS
    #
    # Think of it like speed limits on a road:
    #   Before the new sign goes up → old speed limit applies.
    #   After the new sign → new (lower) speed limit applies.
    if invoice_date < THRESHOLD_CHANGE_DATE:
        threshold = THRESHOLD_BEFORE_JUNE_2026
    else:
        threshold = THRESHOLD_FROM_JUNE_2026

    # ── Step 3: Check if the amount is at or above the threshold ──
    # ">=" means "greater than or equal to"
    # If the amount is EXACTLY 10,000, it still requires allocation.
    above_threshold = amount >= threshold

    # ── Step 4: Build and return the result ──
    # "requires_allocation" is the same as "above_threshold" for now.
    # We keep both fields because in the future there might be
    # exemptions (e.g., government bodies, non-profits) where
    # the amount is above the threshold but allocation is NOT required.
    return {
        "requires_allocation": above_threshold,
        "threshold":           threshold,
        "amount":              amount,
        "above_threshold":     above_threshold,
    }


# ─────────────────────────────────────────────────────────────
# FUNCTION 2: calculate_vat
# ─────────────────────────────────────────────────────────────
# PURPOSE:
#   Given a net amount (before tax), calculate the VAT and total.
#
# REAL-WORLD ANALOGY:
#   You buy a sandwich for 20 NIS (that's the "net" price — what the
#   shop actually earns). The government says: "Add 17% tax on top."
#   So the VAT = 20 * 0.17 = 3.40 NIS.
#   The total you pay = 20 + 3.40 = 23.40 NIS.
#   This function does exactly that math.
#
# PARAMETERS:
#   amount_net (float) — the price BEFORE tax (e.g., 1000.0)
#   vat_rate (float)   — the VAT percentage as a decimal (default 0.17 = 17%)
#
# RETURNS:
#   A dictionary with:
#   {
#     "amount_net":   the original price before tax,
#     "vat_rate":     the rate used (e.g., 0.17),
#     "vat_amount":   the tax portion (rounded to 2 decimal places),
#     "amount_total": net + tax (rounded to 2 decimal places)
#   }
#
# WHY ROUND TO 2 DECIMALS?
#   Money always has exactly 2 decimal places (agorot in Israel,
#   cents in the US). Without rounding, Python might give you
#   something like 3.4000000000000004, which looks ugly and is wrong
#   on an invoice.
# ─────────────────────────────────────────────────────────────
def calculate_vat(amount_net: float, vat_rate: float = DEFAULT_VAT_RATE) -> dict:
    """
    Calculate VAT (מע"מ) for a given net amount.

    Args:
        amount_net: The price before tax in NIS.
        vat_rate:   The VAT rate as a decimal (default 0.17 = 17%).

    Returns:
        A dict with amount_net, vat_rate, vat_amount, and amount_total.
    """

    # ── Step 1: Calculate the VAT amount ──
    # Multiply the net price by the tax rate.
    # round(..., 2) keeps exactly 2 decimal places.
    vat_amount = round(amount_net * vat_rate, 2)

    # ── Step 2: Calculate the total (net + tax) ──
    amount_total = round(amount_net + vat_amount, 2)

    # ── Step 3: Return everything in a neat package ──
    return {
        "amount_net":   amount_net,
        "vat_rate":     vat_rate,
        "vat_amount":   vat_amount,
        "amount_total": amount_total,
    }


# ─────────────────────────────────────────────────────────────
# FUNCTION 3: generate_invoice_number
# ─────────────────────────────────────────────────────────────
# PURPOSE:
#   Generate a unique, human-readable invoice number.
#
# REAL-WORLD ANALOGY:
#   Every invoice needs a serial number, just like every check in a
#   checkbook has a number. The format is:
#     INV-{business_id}-{sequential_number}
#   For example: "INV-1-0001", "INV-1-0002", "INV-3-0015"
#
#   The "0001" part is zero-padded to 4 digits so that invoices
#   sort nicely in lists and look professional.
#
# PARAMETERS:
#   business_id (int)   — the ID of the business issuing the invoice
#   current_count (int) — how many invoices this business has issued so far
#                         (e.g., if they've issued 5, the next one is 6)
#
# RETURNS:
#   A string like "INV-1-0006"
#
# HOW THE FORMATTING WORKS:
#   f"INV-{business_id}-{current_count + 1:04d}"
#   ├── f"..."           = f-string (lets us embed variables inside text)
#   ├── {business_id}    = replaced by the actual business ID number
#   └── {current_count + 1:04d}
#       ├── current_count + 1  = the next invoice number
#       └── :04d               = format as a decimal number, padded to
#                                at least 4 digits with leading zeros
#                                (so 1 becomes "0001", 42 becomes "0042")
# ─────────────────────────────────────────────────────────────
def generate_invoice_number(business_id: int, current_count: int) -> str:
    """
    Generate the next invoice number for a business.

    Args:
        business_id:   The ID of the business.
        current_count: How many invoices this business has issued so far.

    Returns:
        A formatted invoice number string, e.g. "INV-1-0001".
    """

    # ── Step 1: Calculate the next number ──
    # If the business has issued 0 invoices so far, the next is #1.
    # If they've issued 15, the next is #16.
    next_number = current_count + 1

    # ── Step 2: Format and return ──
    # :04d means "pad with zeros to at least 4 digits"
    # Examples:
    #   1  → "0001"
    #   42 → "0042"
    #   999 → "0999"
    #   10000 → "10000" (no truncation — it just grows beyond 4 digits)
    return f"INV-{business_id}-{next_number:04d}"
