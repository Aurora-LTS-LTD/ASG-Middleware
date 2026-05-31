"""
Causal Insights Graph — Sprint 5 stub (Appendix M).

When unlocked, generates probabilistic causal explanations for H-CARL
recommendations using Vertex AI Gemini Pro. Each "insight" is a node
in a causal graph (parent_insight_id chains nodes), with posterior
probability + 95% credible interval + supporting evidence.

Sprint 5 ships the skeleton:
  • Fail-closed gate
  • Real path: if there's already a recent CausalInsight row for the
    requested (org, project), returns it. Otherwise creates a placeholder
    insight that the future Bayesian inference engine will replace.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from app.config.feature_flags import AutonomousFeature
from app.services.autonomous.base import (
    AbstractAutonomousService,
    AutonomousResult,
    AutonomousServiceError,
)

log = logging.getLogger(__name__)


class CausalInsightsService(AbstractAutonomousService):
    """Causal Insights: probabilistic explainability for H-CARL outputs."""

    FEATURE = AutonomousFeature.CAUSAL_INSIGHTS
    ALLOW_NULL_ORG = False

    async def _run_active(
        self, payload: dict, db: "Session"
    ) -> AutonomousResult:
        from aurora_shared.database.models import CausalInsight

        org_id = int(payload["organization_id"])
        project_external_id = payload.get("project_external_id")
        question = (payload.get("question") or "").strip()
        related_constraint_id = payload.get("related_constraint_id")

        # ── Return existing insight tree (most recent N) ──
        q = (
            db.query(CausalInsight)
            .filter(CausalInsight.organization_id == org_id)
            .order_by(CausalInsight.id.desc())
            .limit(20)
        )
        if project_external_id:
            q = q.filter(
                CausalInsight.project_external_id == project_external_id
            )

        rows = q.all()
        if rows:
            return AutonomousResult(
                feature=self.FEATURE.value,
                status="success",
                payload={
                    "insights": [
                        {
                            "id": r.id,
                            "parent_insight_id": r.parent_insight_id,
                            "insight_kind": r.insight_kind,
                            "summary": r.summary,
                            "narrative": r.narrative,
                            "probability": r.probability,
                            "confidence_low": r.confidence_low,
                            "confidence_high": r.confidence_high,
                            "is_validated": r.is_validated,
                            "created_at": r.created_at.isoformat()
                            if r.created_at else None,
                        }
                        for r in rows
                    ],
                    "skeleton": False,
                    "total_returned": len(rows),
                },
            )

        # ── No prior insights — generate a SKELETON placeholder so the
        #    Causal Insights tab in the UI has something to render. The
        #    future Bayesian inference engine will create real insights;
        #    we mark this row is_validated=False so it doesn't pollute
        #    downstream consumers.
        try:
            placeholder = CausalInsight(
                organization_id=org_id,
                project_external_id=project_external_id,
                parent_insight_id=None,
                insight_kind="root_cause",
                summary=(
                    "Causal Insights skeleton — awaiting Bayesian inference "
                    "engine (Sprint 7+)"
                ),
                narrative=(
                    f"Question received: {question or '(none)'}. "
                    "The Causal Insights service is wired but the "
                    "probabilistic inference layer is not yet trained. "
                    "This row is a placeholder so the UI surfaces the "
                    "feature is active; replace via Vertex AI Workbench "
                    "Bayesian model serving."
                ),
                probability=0.0,
                confidence_low=0.0,
                confidence_high=0.0,
                evidence_json=json.dumps([]),
                related_constraint_id=related_constraint_id,
                is_validated=False,
            )
            db.add(placeholder)
            db.commit()
            db.refresh(placeholder)
        except Exception as e:
            db.rollback()
            log.warning("[causal] placeholder persist failed: %s", e)
            placeholder = None

        return AutonomousResult(
            feature=self.FEATURE.value,
            status="success",
            payload={
                "insights": (
                    [
                        {
                            "id": placeholder.id,
                            "parent_insight_id": None,
                            "insight_kind": placeholder.insight_kind,
                            "summary": placeholder.summary,
                            "narrative": placeholder.narrative,
                            "probability": 0.0,
                            "confidence_low": 0.0,
                            "confidence_high": 0.0,
                            "is_validated": False,
                            "created_at": placeholder.created_at.isoformat(),
                        }
                    ]
                    if placeholder
                    else []
                ),
                "skeleton": True,
                "total_returned": 1 if placeholder else 0,
            },
        )
