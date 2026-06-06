"""
Aurora LTS — Israeli Tax Brackets (data only)
==============================================
Source: Israel Tax Authority published annual brackets.

⚠️  THESE ARE 2024 BRACKETS USED AS A PLACEHOLDER FOR 2026.
    Update with the official ITA-published 2026 numbers when they
    are released. Update via PR — every change to these values is
    audit-trail-relevant (the calculator's output depends on them).

Source references (all need a 2026 refresh before launch):
  - מס הכנסה (income tax) — Tax Ordinance §121 + annual תקנות
  - ביטוח לאומי (national insurance, self-employed) — Insurance
    Inst.Law + annual תקנות
  - מס בריאות (health tax) — Health Insurance Law + annual תקנות

All amounts are ILS. All `lower` and `upper` are INCLUSIVE / EXCLUSIVE
boundaries in the annual income axis — i.e., `lower <= x < upper`.
The top bracket has `upper = None` (no ceiling).
"""

from typing import Optional, List, Dict


# ─────────────────────────────────────────────────────────────
# Type alias for clarity
# ─────────────────────────────────────────────────────────────
Bracket = Dict[str, Optional[float]]
# Each row: {"lower": float, "upper": float | None, "rate": float}
# rate is decimal (0.10 = 10%).


# ─────────────────────────────────────────────────────────────
# Income tax brackets (מדרגות מס הכנסה) — for individuals (יחיד)
# Placeholder = ITA 2024 numbers.
# ─────────────────────────────────────────────────────────────
INCOME_TAX_BRACKETS: Dict[int, List[Bracket]] = {
    2024: [
        {"lower":      0.0, "upper":  84_120.0, "rate": 0.10},
        {"lower":  84_120.0, "upper": 120_720.0, "rate": 0.14},
        {"lower": 120_720.0, "upper": 193_800.0, "rate": 0.20},
        {"lower": 193_800.0, "upper": 269_280.0, "rate": 0.31},
        {"lower": 269_280.0, "upper": 560_280.0, "rate": 0.35},
        {"lower": 560_280.0, "upper": 721_560.0, "rate": 0.47},
        {"lower": 721_560.0, "upper":      None, "rate": 0.50},
    ],
    # 2025/2026 should be added here when ITA publishes them.
}

# Default fallback to most recent known year
INCOME_TAX_BRACKETS[2025] = INCOME_TAX_BRACKETS[2024]
INCOME_TAX_BRACKETS[2026] = INCOME_TAX_BRACKETS[2024]


# ─────────────────────────────────────────────────────────────
# National Insurance (ביטוח לאומי) — self-employed (עצמאי) rates
# Below the reduced-rate ceiling, the rate is 2.87%. Above it (up
# to the maximum-insured ceiling), it jumps to 12.83%. Above the
# maximum-insured ceiling, no further NI is charged.
# Numbers below are ANNUAL placeholders matching 2024.
# ─────────────────────────────────────────────────────────────
NATIONAL_INSURANCE_BRACKETS: Dict[int, List[Bracket]] = {
    2024: [
        # Reduced rate up to ~60% of average wage
        {"lower":       0.0, "upper":  85_464.0, "rate": 0.0287},
        # Full rate up to ceiling
        {"lower":  85_464.0, "upper": 624_780.0, "rate": 0.1283},
        # Above ceiling: no NI
        {"lower": 624_780.0, "upper":      None, "rate": 0.0000},
    ],
}

NATIONAL_INSURANCE_BRACKETS[2025] = NATIONAL_INSURANCE_BRACKETS[2024]
NATIONAL_INSURANCE_BRACKETS[2026] = NATIONAL_INSURANCE_BRACKETS[2024]


# ─────────────────────────────────────────────────────────────
# Health tax (ביטוח בריאות) — self-employed rates
# ─────────────────────────────────────────────────────────────
HEALTH_TAX_BRACKETS: Dict[int, List[Bracket]] = {
    2024: [
        {"lower":      0.0, "upper":  85_464.0, "rate": 0.0310},
        {"lower":  85_464.0, "upper": 624_780.0, "rate": 0.0500},
        {"lower": 624_780.0, "upper":      None, "rate": 0.0000},
    ],
}

HEALTH_TAX_BRACKETS[2025] = HEALTH_TAX_BRACKETS[2024]
HEALTH_TAX_BRACKETS[2026] = HEALTH_TAX_BRACKETS[2024]


def get_brackets(kind: str, year: int) -> List[Bracket]:
    """
    Look up a bracket list by kind ('income_tax' | 'national_insurance'
    | 'health_tax') and year. Falls back to the latest known year if
    the requested year is unknown.
    """
    if kind == "income_tax":
        source = INCOME_TAX_BRACKETS
    elif kind == "national_insurance":
        source = NATIONAL_INSURANCE_BRACKETS
    elif kind == "health_tax":
        source = HEALTH_TAX_BRACKETS
    else:
        raise ValueError(f"unknown bracket kind: {kind!r}")

    if year in source:
        return source[year]

    # Fallback to the largest year we know
    fallback_year = max(source.keys())
    return source[fallback_year]
