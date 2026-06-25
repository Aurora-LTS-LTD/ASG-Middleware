"""Analytics events — the BI/metrics stream feeding Growth (v3.2) + Copilot (v3.4).

Call `emit_event(...)` at key business moments (signup, kyc_approved,
payment_succeeded, subscription_changed, churned, ...). Fail-safe by design:
analytics must NEVER break the request it rides on, so all errors are swallowed
and logged. Exported to BigQuery later via the existing audit-cursor pipeline.
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from sqlalchemy.orm import Session

from aurora_shared.database.models import AnalyticsEvent

log = logging.getLogger(__name__)


def emit_event(
    db: Session,
    *,
    event_type: str,
    organization_id: Optional[int] = None,
    user_id: Optional[int] = None,
    actor: str = "system",
    properties: Optional[dict] = None,
) -> None:
    """Best-effort append of a business/product event. Never raises."""
    try:
        db.add(AnalyticsEvent(
            event_type=event_type,
            organization_id=organization_id,
            user_id=user_id,
            actor=actor,
            properties_json=properties,
            created_at=datetime.datetime.utcnow(),
        ))
        db.flush()
    except Exception as e:
        log.warning("[analytics] emit_event(%s) failed (ignored): %s", event_type, e)
