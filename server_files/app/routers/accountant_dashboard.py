"""
Aurora LTS — Accountant Dashboard KPIs (P1-16)
================================================
Exposes a single endpoint:

  GET /api/v1/accountant/dashboard/kpis

…returning the four numbers the accountant portal's home dashboard
previously rendered as "—" placeholder strings:

  {
    "vault_docs_this_month":   int,
    "active_clients":          int,
    "active_devices":          int,
    "security_status":         "ok" | "warning" | "critical"
  }

SCOPING:
  All counts are scoped to the calling accountant — confirmed via the
  require_accountant dependency (JWT iss="aurora-accountant" + Bearer).

PERFORMANCE:
  All four counts are single-table queries with covering indexes:
    - client_documents.created_at + agency_id (Phase 21 vault migration)
    - accountant_engagements.accountant_user_id + status (Phase 6)
    - accountant_devices.user_id + revoked_at (Phase 21 accountant migration)

  None of these are large at current scale; no caching needed.
"""
from __future__ import annotations

import datetime
import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.middleware.auth_middleware import require_accountant
from app.middleware.rate_limit import limiter

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/accountant/dashboard",
    tags=["accountant", "dashboard"],
)


class KpisResponse(BaseModel):
    vault_docs_this_month: int
    active_clients: int
    active_devices: int
    security_status: Literal["ok", "warning", "critical"]


@router.get("/kpis", response_model=KpisResponse)
@limiter.limit("60/minute")
def get_kpis(
    request,
    db: Session = Depends(get_db),
    current_user=Depends(require_accountant),
) -> KpisResponse:
    """
    Return the four dashboard KPI cards' live values.
    Counts are scoped to the calling accountant.
    """
    now = datetime.datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # 1. Vault documents this month — agency-scoped via ClientDocument.agency_id.
    #    The accountant's organization is tracked separately from User.business_id
    #    (an accountant doesn't own a business — they belong to an agency).
    #    For now we attribute the agency by joining accountant_engagements:
    #    documents whose `client_id` matches an org this accountant has an
    #    active engagement with count as "their vault".
    vault_docs_this_month = 0
    active_clients = 0
    try:
        from app.database.models import ClientDocument, AccountantEngagement

        # Active engagements → set of client org ids visible to this accountant.
        active_client_ids = [
            row[0]
            for row in db.query(AccountantEngagement.organization_id)
            .filter(
                AccountantEngagement.accountant_user_id == current_user.id,
                AccountantEngagement.status == "active",
            )
            .all()
        ]
        active_clients = len(active_client_ids)

        if active_client_ids:
            vault_docs_this_month = (
                db.query(func.count(ClientDocument.id))
                .filter(
                    ClientDocument.client_id.in_(active_client_ids),
                    ClientDocument.created_at >= month_start,
                    ClientDocument.deleted_at.is_(None),
                )
                .scalar()
                or 0
            )
    except Exception as exc:
        # Models may not exist yet in older DB snapshots. Default to 0;
        # don't fail the whole KPI endpoint over one missing table.
        log.warning("[kpis] vault/clients query failed: %s", exc)

    # 2. Active devices — accountant_devices for this user, not revoked.
    active_devices = 0
    try:
        from app.database.models import AccountantDevice
        active_devices = (
            db.query(func.count(AccountantDevice.id))
            .filter(
                AccountantDevice.user_id == current_user.id,
                AccountantDevice.revoked_at.is_(None),
            )
            .scalar()
            or 0
        )
    except Exception as exc:
        log.warning("[kpis] devices query failed: %s", exc)

    # 3. Security status — derived from device count.
    #    > 5 active devices on one accountant → warning (unusual fan-out)
    #    > 10 → critical (almost certainly token leakage)
    if active_devices > 10:
        security_status: Literal["ok", "warning", "critical"] = "critical"
    elif active_devices > 5:
        security_status = "warning"
    else:
        security_status = "ok"

    return KpisResponse(
        vault_docs_this_month=vault_docs_this_month,
        active_clients=active_clients,
        active_devices=active_devices,
        security_status=security_status,
    )
