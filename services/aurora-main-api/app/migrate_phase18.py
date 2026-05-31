"""
Aurora LTS — Phase 18 Migration (Sprint 5, Appendix M)
=========================================================

Probes the five new tables that back the Pre-Armed Autonomous
Architecture:

  1. project_constraints   — H-CARL hard-constraint layer
  2. hcarl_policy_states   — RL training rollouts + reward metrics
  3. causal_insights       — Probabilistic causal graph nodes
  4. federated_sync_logs   — Federated Learning round audit (no raw data)
  5. growth_milestones     — CEO-facing target gating

All tables are created by SQLAlchemy `create_tables()`. This migration's
job is to PROBE + LOG. Idempotent; safe to re-run on every startup.

Also seeds the four canonical GrowthMilestone rows (one per
AutonomousFeature) on first install — saves a "wait for the founder
to hit the endpoint" delay on the very first deploy.
"""

import logging

from sqlalchemy import text

from aurora_shared.database.connection import engine, SessionLocal

log = logging.getLogger(__name__)


_EXPECTED_NEW = [
    "project_constraints",
    "hcarl_policy_states",
    "causal_insights",
    "federated_sync_logs",
    "growth_milestones",
]


def _table_exists(conn, t: str) -> bool:
    try:
        conn.execute(text(f"SELECT 1 FROM {t} LIMIT 1"))
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _seed_growth_milestones_if_empty() -> None:
    """Seed the four canonical milestone rows for the autonomous features.

    Idempotent: skipped if any row already exists. Reads thresholds from
    feature_flags so env overrides take effect on first install.
    """
    from app.config.feature_flags import (
        ALL_FEATURES,
        MILESTONE_THRESHOLDS,
        get_threshold,
    )
    from aurora_shared.database.models import GrowthMilestone

    with SessionLocal() as db:
        try:
            existing = db.query(GrowthMilestone).count()
        except Exception as e:
            print(f"[MIGRATE_P18] growth_milestones count failed: {e}")
            return

        if existing > 0:
            print(
                f"[MIGRATE_P18] growth_milestones already has "
                f"{existing} row(s) — skip seed"
            )
            return

        for feature in ALL_FEATURES:
            cfg = MILESTONE_THRESHOLDS[feature]
            row = GrowthMilestone(
                feature_name=feature.value,
                threshold_metric=cfg.metric,
                threshold_value=get_threshold(feature),
                current_value=0,
                is_unlocked=False,
                unlocked_at=None,
                unlocked_by_user_id=None,
            )
            db.add(row)

        try:
            db.commit()
            print(
                f"[MIGRATE_P18] ✅ Seeded {len(ALL_FEATURES)} "
                f"GrowthMilestone rows (all locked, awaiting activation)"
            )
        except Exception as e:
            db.rollback()
            print(f"[MIGRATE_P18] seed commit failed (non-fatal): {e}")


def run_phase18_migrations() -> None:
    print("=" * 60)
    print(
        "[MIGRATE_P18] Phase 18 — Pre-Armed Autonomous Architecture (Appendix M)"
    )
    print("=" * 60)

    found, missing = [], []
    with engine.connect() as conn:
        for t in _EXPECTED_NEW:
            if _table_exists(conn, t):
                found.append(t)
            else:
                missing.append(t)

    for t in found:
        print(f"[MIGRATE_P18] ✅ {t} present")
    for t in missing:
        print(f"[MIGRATE_P18] ⚠️ {t} MISSING — ensure create_tables() ran first")

    # Seed milestones if the table is present and empty
    if "growth_milestones" in found:
        try:
            _seed_growth_milestones_if_empty()
        except Exception as e:
            print(f"[MIGRATE_P18] seed step failed (non-fatal): {e}")

    print("-" * 60)
    print(f"[MIGRATE_P18] Summary: {len(found)} present, {len(missing)} missing")
    print("=" * 60)


if __name__ == "__main__":
    run_phase18_migrations()
