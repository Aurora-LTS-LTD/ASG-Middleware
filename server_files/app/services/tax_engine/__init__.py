"""
Aurora LTS — Tax Engine
========================
Pure, deterministic Israeli tax calculation. NO database, NO network,
NO side effects. Inputs in, dataclass out. Fully unit-testable.

Use the public API:
    from app.services.tax_engine import compute_tax_position, TaxPosition

CRITICAL DISCLAIMER:
    This module computes ESTIMATES. The Israel Tax Authority is the
    final authority on tax owed. Aurora is a calculation tool; the
    user is responsible for filing and paying. See
    /legal/calculation-tool-disclosure for the customer-facing terms.
"""

from app.services.tax_engine.calculator import (
    TaxPosition,
    compute_tax_position,
    progressive_tax,
)
from app.services.tax_engine.brackets import (
    INCOME_TAX_BRACKETS,
    NATIONAL_INSURANCE_BRACKETS,
    HEALTH_TAX_BRACKETS,
    get_brackets,
)
from app.services.tax_engine.constants import (
    TaxConstants,
    get_constants,
)

__all__ = [
    "TaxPosition",
    "compute_tax_position",
    "progressive_tax",
    "INCOME_TAX_BRACKETS",
    "NATIONAL_INSURANCE_BRACKETS",
    "HEALTH_TAX_BRACKETS",
    "get_brackets",
    "TaxConstants",
    "get_constants",
]
