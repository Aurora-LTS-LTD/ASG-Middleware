"""
Federated Learning Sync — Sprint 5 stub (Appendix M).

When unlocked, runs cross-org federated training rounds with k-anonymity
+ differential privacy guarantees. Each round aggregates model weight
deltas from participating orgs WITHOUT centralizing raw tenant data.

Sprint 5 ships the skeleton:
  • Fail-closed gate
  • Real path: schedules a "round" by inserting a FederatedSyncLog row
    in `status='started'` then immediately transitions it to
    `status='rejected_k_anon'` (because we don't have 5+ active orgs yet)
    OR `status='success'` with mock metrics if AURORA_FL_MOCK_AGG=1 is set
  • No actual model weights are aggregated — that requires the TF
    Federated runtime + Vertex AI Custom Training stack to be live

Service-wide tenancy: ALLOW_NULL_ORG=True because FL rounds operate
across organizations, not within one. The check is k-anonymity floor
(participating_org_count >= FEDERATED_MIN_PARTICIPANTS) which defaults
to 5 — the standard floor below which differential analysis can
re-identify individual tenants from aggregated weights.
"""

from __future__ import annotations

import datetime
import logging
import os
import uuid
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


_DEFAULT_K_ANON_MIN = 5


def _k_anon_min() -> int:
    raw = os.getenv("FEDERATED_MIN_PARTICIPANTS", "")
    if not raw:
        return _DEFAULT_K_ANON_MIN
    try:
        v = int(raw)
        return max(v, _DEFAULT_K_ANON_MIN)  # never below 5
    except ValueError:
        return _DEFAULT_K_ANON_MIN


class FederatedSyncService(AbstractAutonomousService):
    """Federated Learning training-round orchestration."""

    FEATURE = AutonomousFeature.FEDERATED_LEARNING
    # Cross-org service — no per-tenant org_id required
    ALLOW_NULL_ORG = True

    def validate_payload(self, payload: dict) -> None:
        # Skip super() validation (ALLOW_NULL_ORG bypasses org-id check)
        model_name = (payload.get("model_name") or "").strip()
        if not model_name:
            raise AutonomousServiceError(
                "federated_learning: 'model_name' is required "
                "(e.g., 'hcarl_strategic_v1')"
            )

    async def _run_active(
        self, payload: dict, db: "Session"
    ) -> AutonomousResult:
        from aurora_shared.database.models import FederatedSyncLog, Organization

        model_name = payload["model_name"].strip()
        round_id = payload.get("round_id") or str(uuid.uuid4())

        # ── Count eligible participating orgs (active status) ──
        try:
            participating_count = (
                db.query(Organization)
                .filter(Organization.status == "active")
                .count()
            )
        except Exception as e:
            log.warning("[fl] org count failed: %s", e)
            participating_count = 0

        k_min = _k_anon_min()
        started_at = datetime.datetime.utcnow()

        # ── k-anonymity floor check (privacy invariant) ──
        if participating_count < k_min:
            row = FederatedSyncLog(
                round_id=round_id,
                model_name=model_name,
                started_at=started_at,
                finished_at=datetime.datetime.utcnow(),
                duration_ms=0,
                participating_org_count=participating_count,
                total_aggregated_samples=0,
                aggregated_weights_uri=None,
                accuracy_metric=None,
                accuracy_delta_vs_prev=None,
                status="rejected_k_anon",
                error=(
                    f"k-anonymity floor not met: "
                    f"{participating_count} < {k_min} participants"
                ),
                dp_epsilon=None,
                dp_delta=None,
            )
            try:
                db.add(row)
                db.commit()
                db.refresh(row)
            except Exception as e:
                db.rollback()
                log.warning("[fl] rejected round persist failed: %s", e)
                row = None

            return AutonomousResult(
                feature=self.FEATURE.value,
                status="partial",
                reason=f"k-anonymity floor not met ({participating_count}/{k_min})",
                payload={
                    "round_id": round_id,
                    "model_name": model_name,
                    "status": "rejected_k_anon",
                    "participating_org_count": participating_count,
                    "k_anon_min": k_min,
                    "fed_sync_log_id": row.id if row else None,
                    "skeleton": True,
                },
            )

        # ── Mock aggregation path (skeleton) ──
        # Real implementation will:
        #   1. Trigger TF Federated runtime on Vertex AI Custom Training
        #   2. Each org's local trainer pulls its data + computes weight delta
        #   3. Secure aggregation server merges deltas under DP noise
        #   4. Aggregated weights uploaded to gs://aurora-fl-weights-prod/
        # Sprint 5 simulates: writes a 'success' row with no real weights.
        mock_enabled = os.getenv("AURORA_FL_MOCK_AGG", "0") == "1"

        if not mock_enabled:
            row = FederatedSyncLog(
                round_id=round_id,
                model_name=model_name,
                started_at=started_at,
                finished_at=None,
                duration_ms=None,
                participating_org_count=participating_count,
                total_aggregated_samples=0,
                aggregated_weights_uri=None,
                accuracy_metric=None,
                accuracy_delta_vs_prev=None,
                status="started",
                error=(
                    "Skeleton — TF Federated runtime not yet provisioned. "
                    "Set AURORA_FL_MOCK_AGG=1 to write a mock success row."
                ),
                dp_epsilon=None,
                dp_delta=None,
            )
            try:
                db.add(row)
                db.commit()
                db.refresh(row)
            except Exception as e:
                db.rollback()
                log.warning("[fl] skeleton round persist failed: %s", e)
                row = None

            return AutonomousResult(
                feature=self.FEATURE.value,
                status="partial",
                reason="Skeleton — TF Federated runtime pending",
                payload={
                    "round_id": round_id,
                    "model_name": model_name,
                    "status": "started",
                    "participating_org_count": participating_count,
                    "fed_sync_log_id": row.id if row else None,
                    "skeleton": True,
                },
            )

        # Mock-success path
        finished_at = datetime.datetime.utcnow()
        row = FederatedSyncLog(
            round_id=round_id,
            model_name=model_name,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            participating_org_count=participating_count,
            total_aggregated_samples=participating_count * 100,  # mock
            aggregated_weights_uri=(
                f"gs://aurora-fl-weights-prod/{model_name}/{round_id}.bin"
            ),
            accuracy_metric=0.85,
            accuracy_delta_vs_prev=0.012,
            status="success",
            error=None,
            dp_epsilon=1.0,
            dp_delta=1e-5,
        )
        try:
            db.add(row)
            db.commit()
            db.refresh(row)
        except Exception as e:
            db.rollback()
            log.warning("[fl] mock-success round persist failed: %s", e)
            row = None

        return AutonomousResult(
            feature=self.FEATURE.value,
            status="success",
            payload={
                "round_id": round_id,
                "model_name": model_name,
                "status": "success",
                "participating_org_count": participating_count,
                "total_aggregated_samples": participating_count * 100,
                "accuracy_metric": 0.85,
                "accuracy_delta_vs_prev": 0.012,
                "dp_epsilon": 1.0,
                "dp_delta": 1e-5,
                "fed_sync_log_id": row.id if row else None,
                "mock": True,
            },
        )
