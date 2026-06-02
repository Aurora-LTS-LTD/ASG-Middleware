"""
Aurora LTS — FX Router (P2-02)
================================
Two endpoints:

  POST /api/v1/fx/refresh
       service-to-service (X-API-Key, scope="fx-refresh").
       Cloud Scheduler hits this daily ~07:00 IDT (after BoI's morning
       publication) to refresh ILS-pair rates.

  GET  /api/v1/fx/rate?currency=USD
       Authenticated read. Returns the latest cached rate.
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aurora_shared.database import get_db
from aurora_shared.middleware.auth_middleware import get_current_user
from aurora_shared.middleware.rate_limit import limiter
from aurora_shared.middleware.api_key_auth import require_api_key
from app.services.fx_rates import (
    get_rate_to_ils,
    refresh_boi_rates,
    FxRateUnavailable,
    SUPPORTED_CURRENCIES,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/fx", tags=["fx"])


class RateResponse(BaseModel):
    currency: str
    rate_to_ils: float
    observed_date: Optional[datetime.datetime]
    source: str = "boi"


class RefreshResponse(BaseModel):
    fetched_count: int
    skipped: list[str]


@router.post("/refresh", response_model=RefreshResponse)
@limiter.limit("30/minute")
def refresh(
    request: Request,
    db: Session = Depends(get_db),
    _api_key=Depends(require_api_key(scope="fx-refresh")),
) -> RefreshResponse:
    """Fetch latest BoI rates + upsert into fx_rates."""
    summary = refresh_boi_rates(db)
    return RefreshResponse(**summary)


@router.get("/rate", response_model=RateResponse)
@limiter.limit("120/minute")
def get_rate(
    request: Request,
    currency: str = Query(..., description="ISO-4217 code (USD/EUR/GBP/ILS)"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> RateResponse:
    currency = currency.upper()
    if currency != "ILS" and currency not in SUPPORTED_CURRENCIES:
        raise HTTPException(
            status_code=400,
            detail=f"currency must be one of {('ILS',) + SUPPORTED_CURRENCIES}",
        )

    if currency == "ILS":
        return RateResponse(currency="ILS", rate_to_ils=1.0, observed_date=None, source="identity")

    try:
        rate = get_rate_to_ils(db, currency)
    except FxRateUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # Pull the actual row for the date stamp.
    from aurora_shared.database.models import FxRate
    row = (
        db.query(FxRate)
        .filter(FxRate.currency == currency)
        .order_by(FxRate.observed_date.desc())
        .first()
    )
    return RateResponse(
        currency=currency,
        rate_to_ils=rate,
        observed_date=row.observed_date if row else None,
        source=row.source if row else "boi",
    )
