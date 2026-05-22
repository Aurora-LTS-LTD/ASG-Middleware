"""
Aurora LTS — Admin Break-Glass Router (Track 3)
================================================
Manage break-glass JWT lifecycle: list, revoke. All endpoints are
**IAP-gated and reject break-glass tokens** (a stolen break-glass
token cannot be used to revoke or list other tokens — closes the
self-protection loop).

Token issuance is intentionally NOT exposed via API. Issuance must
go through the CLI script (`scripts/issue_break_glass_token.py`)
which requires direct DB access. This is by design: the CLI is the
trust anchor; the API is for routine ops.

ENDPOINTS:
  GET  /api/v1/admin/break-glass             — list recent tokens
  POST /api/v1/admin/break-glass/revoke/{jti} — revoke one
"""

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, BreakGlassToken, User, ActionLog
from app.middleware.auth_middleware import require_admin_iap_strict


router = APIRouter(prefix="/api/v1/admin/break-glass", tags=["admin-break-glass"])


class RevokeBody(BaseModel):
    reason: Optional[str] = None


@router.get("")
def list_break_glass_tokens(
    current_user: User = Depends(require_admin_iap_strict),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List the most recent 50 break-glass tokens. Strictly IAP-only."""
    rows = (
        db.query(BreakGlassToken)
        .order_by(BreakGlassToken.id.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": r.id,
            "jti": r.jti,
            "issued_at": r.issued_at.isoformat() if r.issued_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
            "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
            "use_count": r.use_count or 0,
            "issued_by_user_id": r.issued_by_user_id,
            "issued_for_user_id": r.issued_for_user_id,
            "notes": r.notes,
        }
        for r in rows
    ]


@router.post("/revoke/{jti}")
def revoke_break_glass_token(
    jti: str,
    body: Optional[RevokeBody] = None,
    current_user: User = Depends(require_admin_iap_strict),
    db: Session = Depends(get_db),
) -> dict:
    """Revoke a specific break-glass token. Strictly IAP-only (a
    break-glass token cannot revoke itself)."""
    token = db.query(BreakGlassToken).filter(BreakGlassToken.jti == jti).first()
    if not token:
        raise HTTPException(status_code=404, detail="break-glass token not found")
    if token.revoked_at is not None:
        return {
            "ok": True,
            "already_revoked": True,
            "jti": jti,
            "revoked_at": token.revoked_at.isoformat(),
        }

    now = datetime.datetime.utcnow()
    reason = (body.reason if body and body.reason else "manual revocation via IAP")
    token.revoked_at = now
    token.revoked_by_user_id = current_user.id
    token.revoke_reason = reason

    db.add(ActionLog(
        status="break_glass_revoked",
        detail=f"jti={jti} by_user_id={current_user.id} reason={reason!r}",
        triggered_at=now,
    ))

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="revocation commit failed")

    return {
        "ok": True,
        "already_revoked": False,
        "jti": jti,
        "revoked_at": now.isoformat(),
        "revoked_by_user_id": current_user.id,
        "reason": reason,
    }
