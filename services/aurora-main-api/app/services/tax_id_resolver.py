"""
Aurora LTS — Seller Tax ID Resolver (P1-05)
=============================================
The legacy invoice + allocation paths hardcoded seller_tax_id = "000000000"
as a placeholder. Sending that to the Israeli Tax Authority (rashut hamisim)
in production would either be rejected outright or — worse — accepted and
mis-attributed to a non-existent taxpayer.

This module pulls the real seller tax ID from the invoice's owning entity:

  1. Try invoice.business.tax_id (legacy Business table)
  2. Try the Organization paired with business_id via identity service
  3. Raise SellerTaxIdMissing with a clear error if neither exists

The error is raised loudly rather than falling back to "000000000" so that
a misconfigured Business profile produces an obvious operator-visible
failure at allocation time, not a silent ITA submission with a bogus ID.

USAGE:
    from app.services.tax_id_resolver import resolve_seller_tax_id
    seller_tax_id = resolve_seller_tax_id(invoice, db)
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Israeli tax IDs are 9 digits. Strict check is mod-11 (rashut hamisim).
# For sanity checks we just confirm the shape — full mod-11 is enforced
# at the ITA API boundary, not here.
_TAX_ID_PATTERN = re.compile(r"^\d{9}$")

# The historical placeholder — explicitly rejected.
_FORBIDDEN_PLACEHOLDERS = frozenset({"000000000", "0", "", "TODO", "PLACEHOLDER"})


class SellerTaxIdMissing(RuntimeError):
    """Raised when the seller's tax ID cannot be resolved for an invoice."""


def resolve_seller_tax_id(invoice, db: Session) -> str:
    """
    Return the 9-digit Israeli tax ID of the invoice's owning business.

    Resolution order:
      1. invoice.business.tax_id  (Business model)
      2. Organization paired with business_id via identity service
      3. Raise SellerTaxIdMissing

    The resolved value is stripped, validated against the 9-digit shape,
    and rejected if it matches any historical placeholder.
    """
    invoice_id = getattr(invoice, "id", "?")
    business_id = getattr(invoice, "business_id", None)

    raw: Optional[str] = None

    # Path 1: legacy Business.tax_id via the relationship.
    business = getattr(invoice, "business", None)
    if business is not None:
        raw = getattr(business, "tax_id", None)

    # Path 2: explicit lookup if relationship isn't populated.
    if not raw and business_id is not None:
        from aurora_shared.database import Business
        biz = db.query(Business).filter(Business.id == business_id).first()
        if biz is not None:
            raw = biz.tax_id

    # Path 3: the paired Organization.
    if not raw and business_id is not None:
        try:
            from aurora_shared.services.identity import get_or_create_organization_for_business
            org = get_or_create_organization_for_business(business_id, db)
            raw = getattr(org, "tax_id", None)
        except Exception as exc:
            log.debug(
                "[tax_id_resolver] identity lookup failed for business_id=%s: %s",
                business_id, exc,
            )

    if not raw:
        raise SellerTaxIdMissing(
            f"Invoice {invoice_id}: seller tax_id is not set on Business "
            f"id={business_id}. Set Business.tax_id (or the paired "
            f"Organization.tax_id) before allocating this invoice."
        )

    candidate = str(raw).strip()
    if candidate in _FORBIDDEN_PLACEHOLDERS:
        raise SellerTaxIdMissing(
            f"Invoice {invoice_id}: seller tax_id is a placeholder "
            f"('{candidate}'). Set a real 9-digit ID on Business id={business_id}."
        )

    if not _TAX_ID_PATTERN.match(candidate):
        raise SellerTaxIdMissing(
            f"Invoice {invoice_id}: seller tax_id '{candidate}' is not a "
            f"valid 9-digit Israeli tax ID."
        )

    return candidate


__all__ = ["resolve_seller_tax_id", "SellerTaxIdMissing"]
