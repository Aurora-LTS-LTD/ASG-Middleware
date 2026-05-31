"""
H-CARL Ecosystem Orchestrator — Sprint 5 stub (Appendix M).

Concrete implementation of the Hierarchical & Constraint-Aware
Reinforcement Learning service from the Idea Overview PDF.

Sprint 5 ships the SKELETON only:
  • Fail-closed gate (via AbstractAutonomousService.run)
  • Real-active path (_run_active) returns a "ready but no model trained
    yet" structured response with the constraints + state metadata that
    a future Vertex AI Custom Training pipeline would consume.
  • Persists an HcarlPolicyState row tagged model_version='SKELETON_v0'
    on every active call so we can audit invocations during the warm-up
    phase before the real RL model is trained.

When the H-CARL training pipeline ships in a later sprint, the
`_run_active()` body is replaced with the Vertex Prediction Endpoint
call. The shape of AutonomousResult stays the same, so callers don't
churn.
"""

from __future__ import annotations

import json
import uuid
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


class HcarlOrchestratorService(AbstractAutonomousService):
    """H-CARL: hierarchical, constraint-aware decision orchestrator."""

    FEATURE = AutonomousFeature.HCARL_ORCHESTRATOR
    ALLOW_NULL_ORG = False  # H-CARL decisions are always tenant-scoped

    def validate_payload(self, payload: dict) -> None:
        super().validate_payload(payload)
        level = (payload.get("level") or "").lower()
        if level not in ("strategic", "tactical", "operational"):
            raise AutonomousServiceError(
                "H-CARL: 'level' must be one of "
                "'strategic' | 'tactical' | 'operational'"
            )

    async def _run_active(
        self, payload: dict, db: "Session"
    ) -> AutonomousResult:
        from aurora_shared.database.models import (
            HcarlPolicyState,
            ProjectConstraint,
        )

        org_id = int(payload["organization_id"])
        level = payload["level"].lower()
        project_external_id = payload.get("project_external_id")
        rollout_id = payload.get("rollout_id") or str(uuid.uuid4())
        step_index = int(payload.get("step_index", 0))

        # ── Gather active hard constraints for this org/project ──
        constraints_q = db.query(ProjectConstraint).filter(
            ProjectConstraint.organization_id == org_id,
            ProjectConstraint.is_active.is_(True),
        )
        if project_external_id:
            constraints_q = constraints_q.filter(
                ProjectConstraint.project_external_id == project_external_id
            )
        constraint_rows = constraints_q.all()

        constraints_summary = [
            {
                "id": c.id,
                "kind": c.constraint_kind,
                "name": c.name,
                "severity": c.severity,
            }
            for c in constraint_rows
        ]

        # ── Skeleton "decision" — Sprint 5 doesn't yet have a trained
        #    RL model in Vertex AI Custom Training. We return a
        #    constraint-aware "no-op" with full traceability so the
        #    UI/API surfaces show the wiring is intact.
        state = payload.get("state") or {}
        proposed_action = {
            "kind": "noop",
            "rationale": (
                f"H-CARL skeleton active at level={level}; awaiting "
                f"Vertex AI Custom Training pipeline (Sprint 6+). "
                f"No state transition recommended at step {step_index}."
            ),
            "level": level,
        }

        reward_metrics = {
            "cost": 0.0,
            "time": 0.0,
            "quality": 0.0,
            "safety": 0.0,
            "total": 0.0,
            "explanation": (
                "Skeleton response — reward metrics will be populated by "
                "the trained RL agent once the Vertex AI Custom Training "
                "pipeline produces a checkpoint."
            ),
        }

        # ── Persist the HcarlPolicyState row for audit ──
        try:
            row = HcarlPolicyState(
                organization_id=org_id,
                project_external_id=project_external_id,
                rollout_id=rollout_id,
                step_index=step_index,
                level=level,
                state_json=json.dumps(state, default=str, ensure_ascii=False),
                action_json=json.dumps(
                    proposed_action, default=str, ensure_ascii=False
                ),
                reward_metrics_json=json.dumps(
                    reward_metrics, default=str, ensure_ascii=False
                ),
                constraint_violations_json=json.dumps([]),
                is_human_overridden=False,
                model_version="SKELETON_v0",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        except Exception as e:
            db.rollback()
            log.warning(
                "[hcarl] policy-state persist failed: %s",
                type(e).__name__,
            )

        return AutonomousResult(
            feature=self.FEATURE.value,
            status="success",
            payload={
                "rollout_id": rollout_id,
                "step_index": step_index,
                "level": level,
                "proposed_action": proposed_action,
                "reward_metrics": reward_metrics,
                "active_constraints": constraints_summary,
                "model_version": "SKELETON_v0",
                "skeleton": True,
            },
        )
