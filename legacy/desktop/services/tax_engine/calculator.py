"""
Aurora LTS — Tax Calculator (pure functions)
=============================================
Everything here is a pure function. NO database, NO network, NO time
queries (caller passes `tax_year` and `today_gross` explicitly).

Public API:
  - compute_tax_position(...) -> TaxPosition
  - progressive_tax(amount, brackets) -> float
  - marginal_rate_for(amount, *bracket_lists) -> float

Conventions:
  - All amounts are ILS as `float`.
  - All rates are decimal (`0.10 = 10%`).
  - "ytd_gross" / "ytd_expenses" are year-to-date totals for the
    tax year of `tax_year`. The caller is responsible for matching
    period semantics.

Tax-status values used:
  - "osek_patur"               — Israeli עוסק פטור (no VAT charged)
  - "osek_morshe"              — עוסק מורשה (charges + remits VAT)
  - "company"                  — חברה בע״מ (not in v1 scope; pass-through)
  - "salaried_plus_freelance" — שכיר + עצמאי (Aurora computes the
                                freelance side only)
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Sequence

from app.services.tax_engine.brackets import (
    Bracket,
    get_brackets,
)
from app.services.tax_engine.constants import TaxConstants, get_constants


# ─────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TaxPosition:
    """Computed Aurora tax position for a given (org, period)."""

    tax_year: int
    tax_status: str

    # Inputs (mirrored for traceability)
    ytd_gross: float
    ytd_expenses: float

    # Components
    taxable_income: float
    income_tax: float                # after credit-point subtraction, floored at 0
    income_tax_credits_applied: float
    national_insurance: float
    health_tax: float
    vat_owed: float                  # 0 unless osek_morshe
    pension_recommended: float       # Aurora's recommendation; user pays separately

    total_owed: float                # Sum of income_tax + NI + health + VAT (+ pension)

    # Rates
    marginal_rate: float             # 0..1, combined across IT + NI + Health

    # Optional "today" projection
    today_gross: Optional[float] = None
    today_net: Optional[float] = None              # today_gross * (1 - marginal_rate)
    today_shield_set_aside: Optional[float] = None # today_gross * marginal_rate

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────
# Building blocks
# ─────────────────────────────────────────────────────────────
def progressive_tax(amount: float, brackets: Sequence[Bracket]) -> float:
    """
    Apply progressive brackets to `amount`. Each bracket has
    `lower`, `upper` (None = no ceiling), and `rate`. Brackets
    must be sorted ascending by `lower`.
    """
    if amount <= 0:
        return 0.0
    total = 0.0
    for b in brackets:
        lower = b["lower"]
        upper = b["upper"]
        rate = b["rate"]
        if upper is None:
            slice_top = amount
        else:
            slice_top = min(amount, upper)
        if slice_top <= lower:
            continue
        total += (slice_top - lower) * rate
        if upper is not None and amount <= upper:
            break
    return total


def _rate_at(amount: float, brackets: Sequence[Bracket]) -> float:
    """Return the bracket's rate that contains `amount` (inclusive lower,
    exclusive upper). The top bracket (upper=None) catches the tail."""
    for b in brackets:
        lower = b["lower"]
        upper = b["upper"]
        if amount >= lower and (upper is None or amount < upper):
            return b["rate"]
    return 0.0


def marginal_rate_for(amount: float, *bracket_lists: Sequence[Bracket]) -> float:
    """
    Combined marginal rate at `amount` summed across all bracket
    lists provided (e.g., income_tax + national_insurance + health).
    """
    if amount < 0:
        return 0.0
    return sum(_rate_at(amount, bl) for bl in bracket_lists)


# ─────────────────────────────────────────────────────────────
# Main calculation
# ─────────────────────────────────────────────────────────────
def compute_tax_position(
    ytd_gross: float,
    ytd_expenses: float,
    tax_status: str,
    *,
    tax_year: int = 2026,
    today_gross: Optional[float] = None,
    ytd_vat_collected: float = 0.0,
    ytd_vat_paid: float = 0.0,
    credit_points: float = 2.25,
) -> TaxPosition:
    """
    Compute a year-to-date Aurora tax position.

    Returns a TaxPosition dataclass. Caller is responsible for
    persisting (e.g., into the `tax_calculations` table).

    Notes:
      - For Osek Patur, `ytd_vat_collected` and `ytd_vat_paid`
        should both be 0 (the status disallows charging VAT).
      - `credit_points`: Israeli individual default is 2.25 points
        (1 for being Israeli + 1.25 for being an Israeli resident).
        Single parents, military veterans, etc. get extras — caller
        passes a different value.
      - For salaried_plus_freelance, this engine computes the
        FREELANCE side only. The salary side already had tax
        withheld by the employer; combining them at year-end
        annual reconciliation is the user's accountant's job.
    """
    if ytd_gross < 0:
        ytd_gross = 0.0
    if ytd_expenses < 0:
        ytd_expenses = 0.0

    # 1. Taxable income
    taxable_income = max(0.0, ytd_gross - ytd_expenses)

    # 2. Look up brackets
    income_brackets = get_brackets("income_tax", tax_year)
    ni_brackets = get_brackets("national_insurance", tax_year)
    health_brackets = get_brackets("health_tax", tax_year)
    constants = get_constants(tax_year)

    # 3. Income tax — progressive minus credit-point value
    gross_income_tax = progressive_tax(taxable_income, income_brackets)
    credit_value = credit_points * constants.credit_point_value
    net_income_tax = max(0.0, gross_income_tax - credit_value)
    credits_applied = gross_income_tax - net_income_tax

    # 4. National Insurance + Health (self-employed brackets)
    national_insurance = progressive_tax(taxable_income, ni_brackets)
    health_tax = progressive_tax(taxable_income, health_brackets)

    # 5. VAT (Osek Morshe only)
    if tax_status == "osek_morshe":
        vat_owed = max(0.0, ytd_vat_collected - ytd_vat_paid)
    else:
        vat_owed = 0.0

    # 6. Pension contribution recommendation
    # 5% of הכנסה הקובעת (capped at pension_ceiling).
    pension_base = min(taxable_income, constants.pension_ceiling)
    pension_recommended = max(
        constants.pension_floor_min_ils,
        pension_base * constants.pension_min_rate,
    )

    # 7. Total
    total_owed = (
        net_income_tax
        + national_insurance
        + health_tax
        + vat_owed
        + pension_recommended
    )

    # 8. Marginal rate (IT + NI + Health at the current point)
    marginal = marginal_rate_for(
        taxable_income,
        income_brackets,
        ni_brackets,
        health_brackets,
    )

    # 9. Today projection
    today_net: Optional[float] = None
    today_shield: Optional[float] = None
    if today_gross is not None and today_gross > 0:
        today_shield = today_gross * marginal
        today_net = today_gross - today_shield

    return TaxPosition(
        tax_year=tax_year,
        tax_status=tax_status,
        ytd_gross=ytd_gross,
        ytd_expenses=ytd_expenses,
        taxable_income=taxable_income,
        income_tax=net_income_tax,
        income_tax_credits_applied=credits_applied,
        national_insurance=national_insurance,
        health_tax=health_tax,
        vat_owed=vat_owed,
        pension_recommended=pension_recommended,
        total_owed=total_owed,
        marginal_rate=marginal,
        today_gross=today_gross,
        today_net=today_net,
        today_shield_set_aside=today_shield,
    )
