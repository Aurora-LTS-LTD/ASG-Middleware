"""
Aurora LTS — Annual Tax Constants (Israel)
============================================
Per-year scalar constants used by the calculator: VAT rate, pension
floor, Osek Patur threshold, tax-credit-point value, etc.

⚠️  Numbers below are 2024 placeholders applied to 2025 + 2026.
    Update via PR with each official ITA / NI / Health Authority
    release.
"""

from dataclasses import dataclass, asdict
from typing import Dict


@dataclass(frozen=True)
class TaxConstants:
    tax_year: int

    # מע״מ — VAT rate (Osek Morshe). Decimal (0.17 = 17%).
    vat_rate: float

    # סף עוסק פטור — annual gross revenue ceiling for Osek Patur
    # status (above this, must register as Osek Morshe and charge VAT).
    osek_patur_threshold: float

    # ערך נקודת זיכוי שנתי — annual value of one tax-credit point
    # (default adult Israeli citizen gets 2.25 points).
    credit_point_value: float

    # פנסיה חובה לעצמאים — minimum rate (decimal) and the income
    # ceiling on which it is computed.
    pension_min_rate: float
    pension_ceiling: float
    pension_floor_min_ils: float
    # Floor in absolute ILS (i.e., the minimum monthly pension
    # contribution Aurora will recommend regardless of income).

    # שכר ממוצע במשק — average monthly wage. Used as the boundary
    # for NI reduced/full rate (60% of this).
    average_monthly_wage: float


# ─────────────────────────────────────────────────────────────
# Per-year constants
# ─────────────────────────────────────────────────────────────
_CONSTANTS: Dict[int, TaxConstants] = {
    2024: TaxConstants(
        tax_year=2024,
        vat_rate=0.17,
        osek_patur_threshold=120_000.0,
        credit_point_value=2_904.0,        # 12 × ₪242
        pension_min_rate=0.05,
        pension_ceiling=106_272.0,
        pension_floor_min_ils=0.0,
        average_monthly_wage=11_870.0,
    ),
}

_CONSTANTS[2025] = TaxConstants(
    tax_year=2025,
    vat_rate=0.18,                        # VAT rose to 18% on 2025-01-01
    osek_patur_threshold=120_000.0,
    credit_point_value=2_904.0,
    pension_min_rate=0.05,
    pension_ceiling=106_272.0,
    pension_floor_min_ils=0.0,
    average_monthly_wage=11_870.0,
)

_CONSTANTS[2026] = _CONSTANTS[2025]


def get_constants(year: int) -> TaxConstants:
    """Return the constants for a given year, falling back to latest known."""
    if year in _CONSTANTS:
        return _CONSTANTS[year]
    fallback_year = max(_CONSTANTS.keys())
    return _CONSTANTS[fallback_year]
