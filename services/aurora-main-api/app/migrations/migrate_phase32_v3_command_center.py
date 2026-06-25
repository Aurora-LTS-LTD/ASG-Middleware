"""Aurora LTS — Phase 32: CEO Command Center (v3.0) schema.

Adds the admin/ops/audit/analytics/RBAC schema behind CEO Dashboard v3:
  - organizations.archived_at  (soft-delete marker)
  - organizations.is_pilot     (pilot-cohort flag)
  - tables: customer_notes, admin_audit_events, analytics_events,
            roles, permissions, role_permissions

PRODUCTION applies schema via app.db_setup (create_tables + these phases),
NOT Alembic — so on a fresh prod DB create_tables() already builds the NEW
tables (they're in Base.metadata); this phase's load-bearing job is the two
ADD COLUMNs on the existing `organizations` table. The create_all(checkfirst)
below is idempotent belt-and-braces. Mirrors migrate_phase31. The Alembic
revision (alembic/versions/0010_v3_command_center.py) covers Alembic envs.
"""
from __future__ import annotations
import logging
from sqlalchemy import inspect, text
from aurora_shared.database.connection import engine, get_engine
from aurora_shared.database import models

log = logging.getLogger(__name__)


def run() -> None:
    eng = get_engine()
    inspector = inspect(eng)

    # ── 1) organizations: ADD COLUMN archived_at, is_pilot (idempotent) ──
    try:
        org_cols = {c["name"] for c in inspector.get_columns("organizations")}
    except Exception as e:
        org_cols = set()
        log.warning("[phase32] could not introspect organizations: %s", e)

    if "archived_at" not in org_cols:
        with engine.begin() as conn:
            try:
                conn.execute(text("ALTER TABLE organizations ADD COLUMN archived_at TIMESTAMP"))
                log.info("[phase32] added organizations.archived_at")
            except Exception as e:
                log.warning("[phase32] could not add organizations.archived_at: %s", e)

    if "is_pilot" not in org_cols:
        with engine.begin() as conn:
            try:
                conn.execute(text(
                    "ALTER TABLE organizations ADD COLUMN is_pilot BOOLEAN NOT NULL DEFAULT FALSE"
                ))
                log.info("[phase32] added organizations.is_pilot")
            except Exception as e:
                log.warning("[phase32] could not add organizations.is_pilot: %s", e)

    # ── 2) new tables (idempotent — checkfirst skips existing) ──
    try:
        new_tables = [
            models.CustomerNote.__table__,
            models.AdminAuditEvent.__table__,
            models.AnalyticsEvent.__table__,
            models.Role.__table__,
            models.Permission.__table__,
            models.RolePermission.__table__,
        ]
        models.Base.metadata.create_all(bind=eng, tables=new_tables, checkfirst=True)
        log.info("[phase32] ensured v3 command-center tables exist")
    except Exception as e:
        log.warning("[phase32] could not create v3 tables: %s", e)

    log.info("[phase32] CEO Command Center schema done")
