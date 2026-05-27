"""
Aurora LTS — Daily Brief Agent (P2-04)
========================================
The first non-ML autonomous service that ships with REAL behaviour.

  HCARL / Predictive / Causal / Federated stay SKELETON_v0 — they need
  Vertex AI training before they can do anything useful. DailyBrief
  needs none of that. It's a pure SQL aggregation over the last 24h
  of business activity, packaged as a structured AutonomousResult.

USAGE:
    from app.services.autonomous.registry import get_service
    svc = get_service("daily_brief")
    result = await svc.run({"organization_id": 7}, db)

OUTPUT SHAPE (payload):
    {
        "window_hours": 24,
        "invoices": {
            "created":   { "count": 3, "total_amount_ils": 4500.0 },
            "finalized": { "count": 2, "total_amount_ils": 3000.0 }
        },
        "receipts":  { "count": 7, "total_amount_ils": 1820.5 },
        "vault":     { "count": 5 },   # documents received via vault
        "headline":  "Three new invoices today, two finalised; ₪4,500 booked."
    }

The headline is template-based (no LLM) so this remains zero-cost +
deterministic. Future versions can swap to Gemini for richer prose
when the LLM budget allows.
"""
from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config.feature_flags import AutonomousFeature
from app.services.autonomous.base import (
    AbstractAutonomousService,
    AutonomousResult,
)


_WINDOW_HOURS = 24


class DailyBriefService(AbstractAutonomousService):
    FEATURE = AutonomousFeature.DAILY_BRIEF
    ALLOW_NULL_ORG = False  # Always tenant-scoped.

    async def _run_active(self, payload: dict, db: "Session") -> AutonomousResult:
        org_id = payload["organization_id"]
        now = datetime.datetime.utcnow()
        cutoff = now - datetime.timedelta(hours=_WINDOW_HOURS)

        from app.database.models import (
            Invoice, Receipt, ClientDocument,
        )

        # ── Invoices created in window (status = draft on first save) ──
        created_q = (
            db.query(
                func.count(Invoice.id),
                func.coalesce(func.sum(Invoice.amount_total), 0.0),
            )
            .filter(
                Invoice.created_at >= cutoff,
            )
        )
        # Scope by org if Invoice carries one (legacy ones may not).
        if hasattr(Invoice, "organization_id"):
            created_q = created_q.filter(Invoice.organization_id == org_id)
        created_count, created_total = created_q.one()

        # ── Invoices finalized in window ──
        finalized_q = (
            db.query(
                func.count(Invoice.id),
                func.coalesce(func.sum(Invoice.amount_total), 0.0),
            )
            .filter(
                Invoice.status == "finalized",
                Invoice.created_at >= cutoff,
            )
        )
        if hasattr(Invoice, "organization_id"):
            finalized_q = finalized_q.filter(Invoice.organization_id == org_id)
        finalized_count, finalized_total = finalized_q.one()

        # ── Receipts in window ──
        receipts_count, receipts_total = (
            db.query(
                func.count(Receipt.id),
                func.coalesce(func.sum(Receipt.total_amount_minor_units), 0.0),
            )
            .filter(
                Receipt.organization_id == org_id,
                Receipt.created_at >= cutoff,
            )
            .one()
        )

        # ── Vault documents received in window ──
        vault_count = (
            db.query(func.count(ClientDocument.id))
            .filter(
                ClientDocument.client_id == org_id,
                ClientDocument.created_at >= cutoff,
            )
            .scalar()
            or 0
        )

        headline = _build_headline(
            created_count=int(created_count or 0),
            finalized_count=int(finalized_count or 0),
            created_total=float(created_total or 0.0),
            receipts_count=int(receipts_count or 0),
            vault_count=int(vault_count or 0),
        )

        return AutonomousResult(
            feature=self.FEATURE.value,
            status="success",
            payload={
                "window_hours": _WINDOW_HOURS,
                "invoices": {
                    "created":   {"count": int(created_count or 0),
                                  "total_amount_ils": float(created_total or 0.0)},
                    "finalized": {"count": int(finalized_count or 0),
                                  "total_amount_ils": float(finalized_total or 0.0)},
                },
                "receipts": {
                    "count": int(receipts_count or 0),
                    # receipts.total_amount_minor_units → ILS
                    "total_amount_ils": round(
                        float(receipts_total or 0.0) / 100.0, 2
                    ),
                },
                "vault": {"count": int(vault_count or 0)},
                "headline": headline,
                "generated_at": now.isoformat() + "Z",
            },
        )


def _build_headline(
    *,
    created_count: int,
    finalized_count: int,
    created_total: float,
    receipts_count: int,
    vault_count: int,
) -> str:
    """Compose a one-line Hebrew/English summary. Template-driven (no LLM)."""
    parts: list[str] = []
    if created_count:
        parts.append(f"{created_count} new invoice(s)")
    if finalized_count:
        parts.append(f"{finalized_count} finalised")
    if created_total > 0:
        parts.append(f"₪{int(round(created_total)):,} booked")
    if receipts_count:
        parts.append(f"{receipts_count} receipt(s)")
    if vault_count:
        parts.append(f"{vault_count} vault doc(s)")

    if not parts:
        return "Quiet day — no business activity in the last 24h."
    return ", ".join(parts) + "."


__all__ = ["DailyBriefService"]
