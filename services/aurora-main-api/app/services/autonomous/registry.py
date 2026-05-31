"""
Autonomous service registry (Sprint 5 — Appendix M).

Singleton lookup: feature_name → concrete service instance.

    from app.services.autonomous import get_service
    from app.config.feature_flags import AutonomousFeature

    svc = get_service(AutonomousFeature.HCARL_ORCHESTRATOR)
    result = await svc.run({"organization_id": 1, "level": "tactical"}, db)

Services are constructed lazily on first access and cached for the
process lifetime (they're stateless wrappers around DB + LLMProvider).
"""

from __future__ import annotations

from typing import Dict, Union

from app.config.feature_flags import AutonomousFeature
from app.services.autonomous.base import AbstractAutonomousService


_INSTANCES: Dict[AutonomousFeature, AbstractAutonomousService] = {}


def get_service(
    feature: Union[AutonomousFeature, str]
) -> AbstractAutonomousService:
    """Resolve a feature → concrete service instance.

    Accepts either the enum value or its string value (e.g.,
    'hcarl_orchestrator') so route handlers can pass through
    user-supplied path params after validation.
    """
    if isinstance(feature, str):
        try:
            feature = AutonomousFeature(feature)
        except ValueError:
            raise ValueError(
                f"Unknown autonomous feature: {feature!r}. "
                f"Known: {[f.value for f in AutonomousFeature]}"
            )

    if feature in _INSTANCES:
        return _INSTANCES[feature]

    if feature == AutonomousFeature.HCARL_ORCHESTRATOR:
        from app.services.autonomous.hcarl_orchestrator import (
            HcarlOrchestratorService,
        )
        _INSTANCES[feature] = HcarlOrchestratorService()
    elif feature == AutonomousFeature.PREDICTIVE_SITE:
        from app.services.autonomous.predictive_site import (
            PredictiveSiteService,
        )
        _INSTANCES[feature] = PredictiveSiteService()
    elif feature == AutonomousFeature.CAUSAL_INSIGHTS:
        from app.services.autonomous.causal_insights import (
            CausalInsightsService,
        )
        _INSTANCES[feature] = CausalInsightsService()
    elif feature == AutonomousFeature.FEDERATED_LEARNING:
        from app.services.autonomous.federated_sync import (
            FederatedSyncService,
        )
        _INSTANCES[feature] = FederatedSyncService()
    elif feature == AutonomousFeature.DAILY_BRIEF:
        # P2-04 — first non-ML autonomous service with REAL behaviour.
        from app.services.autonomous.daily_brief import DailyBriefService
        _INSTANCES[feature] = DailyBriefService()
    else:
        raise ValueError(f"No concrete service mapped for {feature.value}")

    return _INSTANCES[feature]


def list_services() -> list[str]:
    return [f.value for f in AutonomousFeature]
