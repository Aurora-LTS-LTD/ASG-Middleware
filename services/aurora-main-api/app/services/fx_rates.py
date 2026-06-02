"""
Aurora LTS — FX Rates from Bank of Israel (P2-02)
===================================================
Fetches the daily ILS-pair rates from the Bank of Israel public feed
and caches them in the fx_rates table for deterministic, audit-friendly
currency conversion.

WHY BoI:
  Israeli Tax Authority requires invoices to be reported in ILS, even
  when the customer was billed in USD/EUR/etc. Using the BoI rate (the
  central-bank publication of the official daily exchange rate) means
  our conversion matches what the regulator would compute themselves.

PUBLIC FEED:
  https://boi.org.il/PublicApi/GetExchangeRates
  Returns a JSON array of {"key":"USD","currentExchangeRate":3.65,...}.

WHAT YOU GET BACK:
  Per-currency: latest rate, observed_date (BoI's last publication
  date — typically the previous business day).

USAGE:
    from app.services.fx_rates import get_rate_to_ils, convert_to_ils
    rate = get_rate_to_ils(db, "USD", on_date=invoice.created_at)
    ils  = convert_to_ils(db, 100.0, "USD", on_date=invoice.created_at)

If the requested date has no cached rate, we fall back to the most
recent CACHED rate before that date (typical BoI gap: weekends +
Israeli holidays). If no rate is ever cached, raise FxRateUnavailable.
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

import httpx
from sqlalchemy import desc
from sqlalchemy.orm import Session

from aurora_shared.database.models import FxRate

log = logging.getLogger(__name__)

_BOI_FEED_URL = "https://boi.org.il/PublicApi/GetExchangeRates"

# Currencies we care about for Aurora's customer base.
SUPPORTED_CURRENCIES = ("USD", "EUR", "GBP")


class FxRateUnavailable(RuntimeError):
    """Raised when no cached rate exists for the requested currency."""


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def get_rate_to_ils(
    db: Session,
    currency: str,
    on_date: datetime.datetime | datetime.date | None = None,
) -> float:
    """
    Return how many ILS one unit of `currency` is worth.

    Looks up the most recent cached rate WHOSE observed_date <= on_date.
    If on_date is None, returns the most recent rate overall.

    Raises FxRateUnavailable if no rate is cached.
    """
    if currency == "ILS":
        return 1.0

    q = db.query(FxRate).filter(FxRate.currency == currency)
    if on_date is not None:
        cutoff = on_date if isinstance(on_date, datetime.datetime) else \
                 datetime.datetime.combine(on_date, datetime.time(23, 59, 59))
        q = q.filter(FxRate.observed_date <= cutoff)
    row = q.order_by(desc(FxRate.observed_date)).first()

    if row is None:
        raise FxRateUnavailable(
            f"No cached FX rate for {currency} (on_date={on_date}). "
            f"Run POST /api/v1/fx/refresh to populate."
        )
    return row.rate_to_ils


def convert_to_ils(
    db: Session,
    amount: float,
    currency: str,
    on_date: datetime.datetime | datetime.date | None = None,
) -> float:
    """Convenience: amount * rate_to_ils, rounded to 2 dp."""
    rate = get_rate_to_ils(db, currency, on_date=on_date)
    return round(amount * rate, 2)


# ─────────────────────────────────────────────────────────────
# Refresh — fetch from BoI and persist
# ─────────────────────────────────────────────────────────────

def refresh_boi_rates(db: Session, now: datetime.datetime | None = None) -> dict:
    """
    Fetch the latest rates from BoI and upsert into fx_rates.

    Returns a summary dict { "fetched_count": N, "skipped": [...] }.
    Idempotent for the same (currency, observed_date) pair —
    UniqueConstraint prevents duplicates; we skip rather than UPSERT
    to keep the audit trail clean.
    """
    if now is None:
        now = datetime.datetime.utcnow()

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(_BOI_FEED_URL)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.error("[fx] BoI fetch failed: %s", exc)
        raise

    rates = data.get("exchangeRates") if isinstance(data, dict) else data
    if not isinstance(rates, list):
        raise RuntimeError(f"[fx] Unexpected BoI payload shape: {type(rates).__name__}")

    fetched = 0
    skipped: list[str] = []

    for item in rates:
        try:
            currency = (item.get("key") or item.get("currencyCode") or "").upper()
            if currency not in SUPPORTED_CURRENCIES:
                continue

            # BoI's "currentExchangeRate" is rate × unit (some rates are
            # quoted per 100 units, e.g. JPY). Normalise via "unit".
            raw_rate = (
                item.get("currentExchangeRate")
                or item.get("rate")
                or item.get("exchangeRate")
            )
            unit = item.get("unit") or 1
            if raw_rate is None:
                skipped.append(f"{currency}:no-rate-field")
                continue
            rate_to_ils = float(raw_rate) / float(unit)

            observed_iso = (
                item.get("lastUpdate") or item.get("currentDate") or now.isoformat()
            )
            try:
                observed_date = datetime.datetime.fromisoformat(
                    observed_iso.replace("Z", "+00:00")
                )
                # Strip tz to match the DB column (DateTime not DateTime(timezone=True)).
                observed_date = observed_date.replace(tzinfo=None)
            except Exception:
                observed_date = now

            existing = (
                db.query(FxRate)
                .filter(
                    FxRate.currency == currency,
                    FxRate.observed_date == observed_date,
                )
                .first()
            )
            if existing is not None:
                skipped.append(f"{currency}:duplicate-{observed_date.date()}")
                continue

            db.add(FxRate(
                currency=currency,
                rate_to_ils=rate_to_ils,
                observed_date=observed_date,
                fetched_at=now,
                source="boi",
            ))
            fetched += 1
        except Exception as exc:
            log.warning("[fx] row skipped: %s — %s", item, exc)
            skipped.append(f"{item}:err-{exc}")

    if fetched:
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            log.error("[fx] commit failed: %s", exc)
            raise

    log.info("[fx] refresh: fetched=%d skipped=%d", fetched, len(skipped))
    return {"fetched_count": fetched, "skipped": skipped}


__all__ = [
    "get_rate_to_ils",
    "convert_to_ils",
    "refresh_boi_rates",
    "FxRateUnavailable",
    "SUPPORTED_CURRENCIES",
]
