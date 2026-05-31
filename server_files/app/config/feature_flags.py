"""
Aurora LTS — Feature Flags + Growth Milestones (Sprint 5, Appendix M).

The "Pre-Armed / Feature-Flagged Architecture": every advanced autonomous
service (H-CARL Ecosystem Orchestrator, PredictiveSite Digital Twin,
Causal Insights Graph, Federated Learning Sync) ships its full skeleton
into production today — tables, models, service classes, endpoints,
audit logging — but stays DORMANT at zero operational cost until Aurora
crosses a real-world milestone threshold AND the CEO formally activates
the service via a WebAuthn-gated approve.

This file is the SINGLE SOURCE OF TRUTH for:
  • Which autonomous services exist
  • Which milestone threshold gates each one
  • How to read "is feature X currently active?" from anywhere in the app
  • The display metadata (name, description, severity) for the Growth UI

NO BUSINESS LOGIC LIVES HERE — only configuration. Services that need
to check a flag import `is_feature_active()` and pass their DB session.

DESIGN PRINCIPLES:

  1. Fail-CLOSED — if the feature flag system itself errors, the
     feature is treated as INACTIVE. The platform never accidentally
     turns on an autonomous service due to a config/DB hiccup.

  2. Two gates for activation — a feature becomes "active" ONLY when:
       (a) Its milestone threshold is met (current ≥ target), AND
       (b) A GrowthMilestone row with is_unlocked=true exists
           (set by the CEO via the WebAuthn-gated activate endpoint).
     Either gate failing → feature is inactive → service returns
     placeholder payload, no LLM/Vertex/cost incurred.

  3. Thresholds are TUNABLE via env vars without redeploy — defaults
     are baked into MILESTONE_THRESHOLDS but `MIN_ORGS_FOR_HCARL` etc.
     env overrides take precedence at read time.

  4. New features added later: extend AutonomousFeature enum +
     FEATURE_METADATA dict + MILESTONE_THRESHOLDS entry.  No other
     code change required for the Growth UI to pick them up.
"""

from __future__ import annotations

import os
import enum
import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Enumeration of pre-armed autonomous features
# ─────────────────────────────────────────────────────────────

class AutonomousFeature(str, enum.Enum):
    """The four advanced services from the Idea Overview PDF.

    Stored verbatim as strings (StrEnum behavior) so DB rows reference
    `feature_name` exactly without needing custom serialization.
    """

    HCARL_ORCHESTRATOR = "hcarl_orchestrator"
    PREDICTIVE_SITE = "predictive_site"
    CAUSAL_INSIGHTS = "causal_insights"
    FEDERATED_LEARNING = "federated_learning"


ALL_FEATURES: list[AutonomousFeature] = list(AutonomousFeature)


# ─────────────────────────────────────────────────────────────
# Milestone thresholds — tunable via env, default to baked-in values.
# ─────────────────────────────────────────────────────────────
#
# Each entry maps an AutonomousFeature to:
#   • metric:           the system-scale metric watched by the Growth Engine
#   • default_threshold: the value we believe is the right "Aurora has reached
#                        scale to safely activate this" trigger point
#   • env_override:     env var name that, if set, overrides the default
#
# Metrics that the Growth Engine knows how to compute (see
# `app/routers/admin_exec.py::growth_summary`):
#   • active_orgs        — count of Organization.status='active'
#   • total_invoices     — count of all Invoice rows (any status)
#   • classified_receipts — count of Receipt.gemini_classified_at IS NOT NULL
#   • active_sessions_30d — count of WhatsAppSession with traffic in last 30d
#   • copilot_runs       — count of CopilotProvisioningRun.status='success'

@dataclass(frozen=True)
class MilestoneConfig:
    feature: AutonomousFeature
    metric: str
    default_threshold: int
    env_override: str
    display_label: str
    display_unit: str
    display_description: str
    severity_unlock: str = "info"


# Canonical thresholds — calibrated for "is Aurora big enough that
# spinning this up is justified?" rather than "what's the minimum
# datapoint count?" The defaults bias toward conservatism (turn ON
# late, not early) because false-positive activation burns LLM cost.
MILESTONE_THRESHOLDS: dict[AutonomousFeature, MilestoneConfig] = {
    AutonomousFeature.HCARL_ORCHESTRATOR: MilestoneConfig(
        feature=AutonomousFeature.HCARL_ORCHESTRATOR,
        metric="active_orgs",
        default_threshold=50,
        env_override="MIN_ORGS_FOR_HCARL",
        display_label="H-CARL Ecosystem Orchestrator",
        display_unit="active organizations",
        display_description=(
            "Hierarchical Reinforcement Learning agent that balances cost, "
            "schedule, quality, safety under hard constraints across the "
            "client portfolio. Unlocks when Aurora has enough orgs to make "
            "cross-tenant policy learning meaningful."
        ),
        severity_unlock="info",
    ),
    AutonomousFeature.PREDICTIVE_SITE: MilestoneConfig(
        feature=AutonomousFeature.PREDICTIVE_SITE,
        metric="total_invoices",
        default_threshold=1000,
        env_override="MIN_INVOICES_FOR_PREDICTIVE_SITE",
        display_label="PredictiveSite Digital Twin",
        display_unit="invoices",
        display_description=(
            "Real-time multi-modal digital twin of client project sites "
            "fusing IoT telemetry, drone imagery, and BIM data. Unlocks "
            "once Aurora's data warehouse has enough transactional volume "
            "to anchor predictive models."
        ),
        severity_unlock="info",
    ),
    AutonomousFeature.CAUSAL_INSIGHTS: MilestoneConfig(
        feature=AutonomousFeature.CAUSAL_INSIGHTS,
        metric="classified_receipts",
        default_threshold=5000,
        env_override="MIN_DATA_POINTS_FOR_CAUSAL",
        display_label="Causal Insights Graph",
        display_unit="classified data points",
        display_description=(
            "Probabilistic causal graph powering explainable AI "
            "recommendations with confidence envelopes and what-if "
            "simulation. Requires enough labeled data to build "
            "statistically defensible causal edges."
        ),
        severity_unlock="info",
    ),
    AutonomousFeature.FEDERATED_LEARNING: MilestoneConfig(
        feature=AutonomousFeature.FEDERATED_LEARNING,
        metric="active_orgs",
        default_threshold=10,
        env_override="MIN_ACTIVE_ORGS_FOR_FL",
        display_label="Federated Learning Sync",
        display_unit="active organizations",
        display_description=(
            "Privacy-preserving cross-org model improvement. Aggregates "
            "weight updates without exchanging raw tenant data. Unlocks "
            "as soon as we have enough orgs to make federation valuable "
            "while preserving k-anonymity in aggregated updates."
        ),
        severity_unlock="info",
    ),
}


def get_threshold(feature: AutonomousFeature) -> int:
    """Return the active threshold (env override or default)."""
    config = MILESTONE_THRESHOLDS[feature]
    raw = os.getenv(config.env_override, "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            log.warning(
                "[feature_flags] env override %s=%r is not int — "
                "falling back to default %d",
                config.env_override, raw, config.default_threshold,
            )
    return config.default_threshold


# ─────────────────────────────────────────────────────────────
# Activation check — the canonical "is this feature live?" query
# ─────────────────────────────────────────────────────────────

def is_feature_active(
    feature: AutonomousFeature,
    db: Optional["Session"] = None,
) -> bool:
    """Return True iff this autonomous feature should produce real output.

    Two gates (both required):
      1. CEO has activated the feature → growth_milestones row with
         feature_name=<feature> AND is_unlocked=true exists
      2. (Implicit) Threshold was met at activation time — enforced by
         the activate endpoint, not re-checked here

    If `db` is None we fail-closed (return False) — the autonomous
    services MUST be invoked from a request context that has a DB
    session, and a missing session is treated as a system error.

    Defensive: any DB exception → return False. Never raise to the
    caller; an active feature failing-open could spend $$$ accidentally.
    """
    if db is None:
        log.warning("[feature_flags] is_feature_active called without db — "
                    "treating %s as INACTIVE (fail-closed)", feature.value)
        return False

    # Lazy import — avoid circular dep with models.py
    try:
        from aurora_shared.database.models import GrowthMilestone
        row = (
            db.query(GrowthMilestone)
            .filter(GrowthMilestone.feature_name == feature.value)
            .filter(GrowthMilestone.is_unlocked.is_(True))
            .first()
        )
        return row is not None
    except Exception as e:
        log.warning(
            "[feature_flags] DB check failed for %s — fail-closed (%s)",
            feature.value, e,
        )
        return False


# ─────────────────────────────────────────────────────────────
# Optional kill switch — instant disable without DB write
# ─────────────────────────────────────────────────────────────
#
# Env var `AURORA_AUTONOMOUS_KILL_SWITCH=1` disables ALL autonomous
# features regardless of DB state. Used for emergency shutdowns
# (runaway Vertex cost, model misbehavior). Single env update +
# Cloud Run revision rollover = ~30s.

def kill_switch_active() -> bool:
    return os.getenv("AURORA_AUTONOMOUS_KILL_SWITCH", "0") == "1"


# ─────────────────────────────────────────────────────────────
# Display helpers for the Growth UI
# ─────────────────────────────────────────────────────────────

def feature_display_meta(feature: AutonomousFeature) -> dict:
    config = MILESTONE_THRESHOLDS[feature]
    return {
        "feature_name": feature.value,
        "display_label": config.display_label,
        "display_unit": config.display_unit,
        "display_description": config.display_description,
        "metric": config.metric,
        "threshold": get_threshold(feature),
        "severity_unlock": config.severity_unlock,
    }


def all_feature_metas() -> list[dict]:
    return [feature_display_meta(f) for f in ALL_FEATURES]
