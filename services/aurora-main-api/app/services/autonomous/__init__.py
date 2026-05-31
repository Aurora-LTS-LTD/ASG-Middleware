"""
Aurora LTS — Pre-Armed Autonomous Services package (Sprint 5, Appendix M).

Houses the abstract service contract + four concrete service stubs:
  • hcarl_orchestrator   — Hierarchical & Constraint-Aware RL
  • predictive_site      — Real-time multi-modal Digital Twin
  • causal_insights      — Probabilistic Causal Graph + explanations
  • federated_sync       — Privacy-preserving cross-org learning

EVERY service inherits from AbstractAutonomousService which enforces
the fail-closed gating: if the feature flag is OFF, the service
returns an elegant placeholder payload WITHOUT invoking any LLM,
WITHOUT writing to the DB, WITHOUT spending budget.

When the flag flips ON via the Growth Engine, the same code path
delegates to the real provider (Vertex Gemini via LLMProvider abstraction).

Exported entry points:
    from app.services.autonomous import get_service
    service = get_service(AutonomousFeature.HCARL_ORCHESTRATOR)
    result = await service.run({"project_id": "TLV-tower-A", "level": "tactical"}, db)
"""

from app.services.autonomous.base import (
    AbstractAutonomousService,
    AutonomousResult,
    AutonomousServiceError,
)
from app.services.autonomous.registry import get_service, list_services

__all__ = [
    "AbstractAutonomousService",
    "AutonomousResult",
    "AutonomousServiceError",
    "get_service",
    "list_services",
]
