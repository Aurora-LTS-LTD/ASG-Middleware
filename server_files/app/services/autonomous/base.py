"""
Aurora LTS — Abstract autonomous service base class (Sprint 5).

EVERY pre-armed autonomous feature inherits from AbstractAutonomousService
to enforce four invariants:

  1. Feature-flag gate runs FIRST. If the flag is OFF (or the kill
     switch is on), the service returns a placeholder AutonomousResult
     immediately. NO provider call, NO DB write, NO budget burn.

  2. Tenant isolation. Every `run()` invocation MUST supply an
     `organization_id` in the input payload (or the service-level
     ALLOW_NULL_ORG flag must be True for system-wide services like
     federated_sync). Missing org → AutonomousServiceError (fail-closed).

  3. Audit logging. Every active invocation writes an ExecEvent
     (kind=autonomous_invoked) so the founder sees activity in the
     Alert Stream. Placeholders DO NOT log (avoid noise).

  4. Exception boundary. Any exception inside the concrete service's
     `_run_active()` is caught here, logged at WARNING, and converted
     to AutonomousResult(status="error", ...). The caller (FastAPI
     route) NEVER sees raw stack traces — sees a structured payload.

This base class is the contract for all four services. Concrete
implementations override:
  • FEATURE  — the AutonomousFeature enum value
  • ALLOW_NULL_ORG (default False) — set True for system-wide services
  • async def _run_active(self, payload, db) -> AutonomousResult
"""

from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from app.config.feature_flags import (
    AutonomousFeature,
    is_feature_active,
    kill_switch_active,
)

log = logging.getLogger(__name__)


class AutonomousServiceError(Exception):
    """Raised for caller-visible service contract failures (e.g., missing
    organization_id). Distinct from internal exceptions which are caught
    inside `run()` and converted to status='error' results."""


@dataclass
class AutonomousResult:
    """Canonical result shape returned by every autonomous service.

    Fields:
      feature       — AutonomousFeature enum value
      status        — 'placeholder' | 'success' | 'partial' | 'error'
      payload       — Service-specific result data (placeholder OR real)
      reason        — Human-readable explanation when status≠success
      duration_ms   — Wall-clock duration of the call
      gemini_runs   — Optional list of GeminiRun.id references created
      created_at    — UTC timestamp when the result was finalized
    """

    feature: str
    status: str
    payload: dict = field(default_factory=dict)
    reason: Optional[str] = None
    duration_ms: int = 0
    gemini_runs: list[int] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.datetime.utcnow().isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "feature": self.feature,
            "status": self.status,
            "payload": self.payload,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
            "gemini_runs": list(self.gemini_runs),
            "created_at": self.created_at,
        }


def _placeholder_payload(feature: AutonomousFeature, reason: str) -> dict:
    """Standard placeholder shape — surfaces to the UI cleanly."""
    return {
        "feature": feature.value,
        "dormant": True,
        "reason": reason,
        "next_step": (
            "Reach the Growth milestone threshold + activate via "
            "POST /api/v1/admin/exec/growth/activate/" + feature.value
        ),
    }


class AbstractAutonomousService:
    """Base class for all pre-armed autonomous services.

    Subclasses MUST set:
      FEATURE: AutonomousFeature
      ALLOW_NULL_ORG: bool (default False)

    Subclasses MUST implement:
      async def _run_active(self, payload: dict, db: Session) -> AutonomousResult

    Subclasses MAY override:
      def validate_payload(self, payload: dict) -> None
        — runs BEFORE the gate check; raises AutonomousServiceError on bad input.
    """

    FEATURE: AutonomousFeature = None  # type: ignore[assignment]
    ALLOW_NULL_ORG: bool = False

    def __init__(self):
        if self.FEATURE is None:
            raise TypeError(
                f"{type(self).__name__} must set FEATURE class attribute"
            )

    # ── Payload validation ──
    def validate_payload(self, payload: dict) -> None:
        """Default validation — checks organization_id presence.

        Subclasses can override to add domain-specific checks. Call
        `super().validate_payload(payload)` to keep org-id enforcement.
        """
        if self.ALLOW_NULL_ORG:
            return
        org_id = payload.get("organization_id")
        if not org_id or not isinstance(org_id, int):
            raise AutonomousServiceError(
                f"{self.FEATURE.value}: 'organization_id' (int) is required "
                "in payload for tenant isolation"
            )

    # ── Concrete subclass override target ──
    async def _run_active(
        self, payload: dict, db: "Session"
    ) -> AutonomousResult:
        """Run the real autonomous workload. Subclasses MUST implement.

        Should return AutonomousResult(status='success'|'partial', payload=...).
        Exceptions are caught in `run()` and converted to status='error'.
        """
        raise NotImplementedError(
            f"{type(self).__name__}._run_active not implemented"
        )

    # ── Caller entry point ──
    async def run(
        self, payload: Optional[dict] = None, db: Optional["Session"] = None
    ) -> AutonomousResult:
        """Run the service. Always returns AutonomousResult (never raises).

        Lifecycle:
          1. Validate payload (raises AutonomousServiceError if bad)
          2. Kill-switch check → placeholder if active
          3. Feature-flag check → placeholder if inactive
          4. Run _run_active()  → result
          5. Catch exceptions → status='error' result with reason

        Raises ONLY AutonomousServiceError (caller-bug indicator); never
        propagates internal exceptions.
        """
        t0 = time.monotonic()
        payload = payload or {}

        # ── Validate payload (fail-fast for caller bugs) ──
        try:
            self.validate_payload(payload)
        except AutonomousServiceError:
            raise  # caller bug — surface to API layer
        except Exception as e:
            # Validator subclass raised non-AutonomousServiceError → coerce
            raise AutonomousServiceError(
                f"{self.FEATURE.value} validate_payload raised "
                f"{type(e).__name__}: {str(e)[:160]}"
            )

        # ── Kill switch (env-level emergency stop) ──
        if kill_switch_active():
            return AutonomousResult(
                feature=self.FEATURE.value,
                status="placeholder",
                payload=_placeholder_payload(
                    self.FEATURE,
                    "AURORA_AUTONOMOUS_KILL_SWITCH=1 (emergency disable)",
                ),
                reason="kill_switch_active",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

        # ── Feature flag gate (the canonical "dormant?" check) ──
        if not is_feature_active(self.FEATURE, db):
            return AutonomousResult(
                feature=self.FEATURE.value,
                status="placeholder",
                payload=_placeholder_payload(
                    self.FEATURE,
                    "Feature not yet unlocked via Growth Engine",
                ),
                reason="feature_inactive",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

        # ── Active path — delegate to subclass ──
        try:
            result = await self._run_active(payload, db)
        except Exception as e:
            log.warning(
                "[autonomous.%s] _run_active raised %s: %s",
                self.FEATURE.value, type(e).__name__, str(e)[:240],
            )
            result = AutonomousResult(
                feature=self.FEATURE.value,
                status="error",
                payload={"feature": self.FEATURE.value, "error_type": type(e).__name__},
                reason=str(e)[:240],
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

        # Ensure duration_ms is populated (subclass may have set its own)
        if not result.duration_ms:
            result.duration_ms = int((time.monotonic() - t0) * 1000)

        # Audit: publish ExecEvent so the Alert Stream surfaces activity
        try:
            from app.services.exec_events import publish_exec_event
            publish_exec_event(
                db,
                kind=f"autonomous_invoked",
                severity="info" if result.status in ("success", "placeholder")
                else "warning",
                title=(
                    f"Autonomous service: {self.FEATURE.value} "
                    f"({result.status})"
                ),
                detail=(
                    f"org_id={payload.get('organization_id')} "
                    f"duration_ms={result.duration_ms} "
                    f"reason={result.reason or '-'}"
                ),
                related_entity_type="autonomous_service",
                related_entity_id=None,
            )
        except Exception:
            # Audit write failure must NEVER alter the service result.
            pass

        return result
