"""
Aurora LTS — Referral Tracking (Sprint 5)
=============================================
Records which accountant brought which Org onto Aurora. Backs the
leaderboard and the per-firm earnings dashboard.

Not part of the rev-share ledger (that's per-charge); this is per-Org
provenance. The two tables join on accountant_user_id + organization_id.
"""

import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.database import AccountantReferral, ActionLog


def record_referral(
    *,
    accountant_user_id: int,
    organization_id: int,
    db: Session,
    source: str = "portal",
    notes: Optional[str] = None,
) -> AccountantReferral:
    """
    Idempotently record that `accountant_user_id` referred `organization_id`.
    Re-calling with the same pair updates `notes`/`source` rather than
    creating a duplicate.
    """
    existing = (
        db.query(AccountantReferral)
        .filter(
            AccountantReferral.accountant_user_id == accountant_user_id,
            AccountantReferral.organization_id == organization_id,
        )
        .first()
    )
    if existing:
        if source and existing.source != source:
            existing.source = source
        if notes:
            existing.notes = (existing.notes or "") + f"\n{notes}"
        db.commit()
        return existing

    row = AccountantReferral(
        accountant_user_id=accountant_user_id,
        organization_id=organization_id,
        source=source or "portal",
        notes=notes,
    )
    db.add(row)
    db.add(ActionLog(
        business_id=None,
        status="referral.recorded",
        detail=f"acct={accountant_user_id} org={organization_id} source={source!r}",
    ))
    db.commit()
    db.refresh(row)
    return row
