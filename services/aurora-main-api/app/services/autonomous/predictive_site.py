"""
PredictiveSite Digital Twin — Sprint 5 stub (Appendix M).

Real-time, multi-modal digital twin of client project sites. When
unlocked, fuses IoT telemetry + drone imagery + BIM data into a single
queryable state via Vertex AI Vision + Pub/Sub.

Sprint 5 ships the skeleton:
  • Fail-closed gate via AbstractAutonomousService
  • Real path returns the LATEST known state-summary for the requested
    organization/project (mostly empty until the data ingestion
    pipeline is built in Sprint 7+)
  • No Pub/Sub subscription is created in Sprint 5 — that's the next
    layer of build-out
"""

from __future__ import annotations

import datetime
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


class PredictiveSiteService(AbstractAutonomousService):
    """PredictiveSite: multi-modal digital-twin state synthesis."""

    FEATURE = AutonomousFeature.PREDICTIVE_SITE
    ALLOW_NULL_ORG = False

    async def _run_active(
        self, payload: dict, db: "Session"
    ) -> AutonomousResult:
        from aurora_shared.database.models import Invoice, Receipt, Organization

        org_id = int(payload["organization_id"])
        project_external_id = payload.get("project_external_id")

        # ── Verify org exists (defense-in-depth tenant check) ──
        org = (
            db.query(Organization)
            .filter(Organization.id == org_id)
            .first()
        )
        if not org:
            return AutonomousResult(
                feature=self.FEATURE.value,
                status="error",
                reason=f"organization {org_id} not found",
                payload={},
            )

        # ── Compute the skeleton twin state ──
        # Real implementation will pull IoT telemetry from Pub/Sub +
        # Vertex AI Vision outputs from a worker queue. Skeleton uses
        # what we DO have: financial activity (invoices/receipts).
        invoice_count = (
            db.query(Invoice)
            .filter(Invoice.business_id == org.legacy_business_id)
            .count()
            if org.legacy_business_id
            else 0
        )
        receipt_count = (
            db.query(Receipt)
            .filter(Receipt.organization_id == org_id)
            .count()
        )

        twin_state = {
            "organization_id": org_id,
            "display_name": org.display_name,
            "project_external_id": project_external_id,
            "modalities": {
                "iot_telemetry": {
                    "ingestion_active": False,
                    "last_packet_at": None,
                    "note": (
                        "Pub/Sub topic 'aurora-iot-{org_id}' not yet "
                        "provisioned. Activate via Sprint 7 IoT module."
                    ),
                },
                "drone_imagery": {
                    "ingestion_active": False,
                    "last_frame_at": None,
                    "note": (
                        "Vertex AI Vision pipeline not yet attached."
                    ),
                },
                "bim_model": {
                    "loaded": False,
                    "note": (
                        "BIM artifact references will land in "
                        "gs://aurora-bim-prod/ once the upload workflow "
                        "is built."
                    ),
                },
                "financial_activity": {
                    "invoice_count": invoice_count,
                    "receipt_count": receipt_count,
                    "ingestion_active": True,
                    "note": (
                        "Financial dimension of the twin is fully wired "
                        "via existing Invoice + Receipt pipelines."
                    ),
                },
            },
            "as_of": datetime.datetime.utcnow().isoformat(),
            "skeleton": True,
        }

        return AutonomousResult(
            feature=self.FEATURE.value,
            status="success",
            payload={
                "twin_state": twin_state,
                "ready_modalities": ["financial_activity"],
                "pending_modalities": [
                    "iot_telemetry",
                    "drone_imagery",
                    "bim_model",
                ],
                "next_milestone": (
                    "Provision Pub/Sub topic + IoT gateway role bindings "
                    "in Sprint 7"
                ),
            },
        )
