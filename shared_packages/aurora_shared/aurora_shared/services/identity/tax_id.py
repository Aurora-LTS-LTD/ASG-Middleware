"""
ASG Solutions — Israeli Tax ID Validator
==========================================
Validates 9-digit Israeli identifiers against the official mod-11
checksum algorithm. The same algorithm is used for:

  - תעודת זהות (ID card number, individuals)
  - עוסק מורשה  (Authorized Dealer / VAT registration number)
  - עוסק פטור   (Exempt Dealer / business ID)
  - ח.פ.        (Company ID / Ltd, registered with the Registrar of Companies)

THE ALGORITHM (mod-11 weighted check digit):
  Take the 9 digits. For each digit, multiply by 1 or 2 alternating
  (positions 0,2,4,6,8 → ×1; positions 1,3,5,7 → ×2). If the result
  of any ×2 is 10 or more, sum its digits (i.e., subtract 9). The
  total of all 9 weighted digits must be a multiple of 10.

  This is "Luhn-like" but specific to the Israeli system.

REAL-WORLD ANALOGY:
  Like the last digit of a credit card number — it's a self-check.
  If someone mistypes one digit, the math no longer adds up to a
  multiple of 10, and we know to ask them to retype before sending
  anything to the Tax Authority. Saves a network round-trip and
  saves the user from a confusing ITA error.

SECURITY:
  This is NOT a uniqueness check or an "is this a real business"
  check. It only verifies the CHECKSUM is consistent. A real-world
  validation must also call the Israeli Companies Registrar API
  (or equivalent) to confirm the entity exists. We do that step
  separately during KYC document review (Sprint 1 / Onboarding).

PUBLIC FUNCTIONS:
  - validate_tax_id_israel(value)        → bool
  - infer_legal_structure_from_tax_id(v) → 'osek_morshe' | 'osek_patur' | 'chevra_baam' | None
  - normalize_tax_id(value)              → str (zero-padded to 9 digits)
"""

import re


# ─────────────────────────────────────────────────────────────
# Acceptable input shapes — strip these before validation
# ─────────────────────────────────────────────────────────────
# Users frequently include hyphens or spaces, e.g. "12-3456789" or
# "123 456 789" or "ת.ז. 123456789". Only digits matter for the math.
_NON_DIGIT_RE = re.compile(r"[^0-9]")


def normalize_tax_id(raw: str) -> str:
    """
    Strip non-digits and zero-pad to 9 characters.

    Examples:
        "12-3456789"   →  "123456789"
        "12345678"     →  "012345678"
        "ת.ז. 12345"   →  "000012345"
        ""             →  ""
        None           →  ""

    Note: zero-padding is correct behavior for the Israeli system.
    A 7-digit ID card is just an older one issued before the system
    moved to 9-digit numbers; the leading zeros are implicit.
    """
    if not raw:
        return ""
    digits_only = _NON_DIGIT_RE.sub("", str(raw))
    if not digits_only:
        return ""
    if len(digits_only) > 9:
        # Too long — return as-is for the caller to reject.
        return digits_only
    return digits_only.zfill(9)


def validate_tax_id_israel(raw: str) -> bool:
    """
    Validate an Israeli 9-digit identifier using the mod-11 algorithm.

    Returns True iff:
      - input normalizes to exactly 9 digits
      - the weighted sum is a multiple of 10

    Examples:
        validate_tax_id_israel("123456782")  → True   (well-known test value)
        validate_tax_id_israel("000000018")  → True
        validate_tax_id_israel("123456789")  → False
        validate_tax_id_israel("")           → False
    """
    normalized = normalize_tax_id(raw)
    if len(normalized) != 9:
        return False
    if not normalized.isdigit():
        return False

    total = 0
    for i, ch in enumerate(normalized):
        digit = int(ch)
        weight = 1 if i % 2 == 0 else 2
        product = digit * weight
        # If the product is 10 or 12, sum its digits (10 → 1+0=1; 12 → 1+2=3).
        # Equivalent to subtracting 9 when the product is ≥ 10.
        if product >= 10:
            product -= 9
        total += product

    return total % 10 == 0


# ─────────────────────────────────────────────────────────────
# Legal-structure inference
# ─────────────────────────────────────────────────────────────
# In Israel, the first digit of a 9-digit tax ID hints at the entity type:
#   '5' → Limited Liability Company (חברה בע"מ)
#         issued by the Registrar of Companies
#   '0'-'4', '6'-'9' → Individual (תעודת זהות) or
#                       Authorized/Exempt Dealer (עוסק)
#
# This heuristic is a SIGNAL, not a guarantee. We surface it as a
# UI default ("looks like a חברה בע"מ — confirm?") but always let
# the user override during onboarding.
# ─────────────────────────────────────────────────────────────
def infer_legal_structure_from_tax_id(raw: str) -> str | None:
    """
    Best-effort guess of legal_structure from a tax_id.

    Returns one of:
        'chevra_baam'  — likely Ltd company
        'osek_morshe'  — likely Authorized Dealer (default for individuals)
        None           — input was not a valid 9-digit ID

    The caller should ALWAYS surface this as a default the user can change.
    """
    normalized = normalize_tax_id(raw)
    if len(normalized) != 9 or not normalized.isdigit():
        return None
    if normalized.startswith("5"):
        return "chevra_baam"
    # Individuals and Dealers — default to osek_morshe; user adjusts to
    # osek_patur if they're under the VAT threshold.
    return "osek_morshe"
