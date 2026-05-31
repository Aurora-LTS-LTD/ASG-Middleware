"""
Aurora LTS — Alembic Bootstrap (P1-02)
========================================
Wires Alembic into the startup() flow so the alembic_version table is
established on first deploy without requiring a separate manual step.

POLICY:
  - First encounter (alembic_version table missing) → stamp to "head".
    The schema produced by the legacy migrate_phase*.py files IS the
    baseline. No DDL is run.
  - Subsequent boots → `alembic upgrade head` applies any new revisions
    that have been merged since.
  - SQLite (dev) → behaves identically. Local dev gets the same flow.

Called from inside _run_all_phase_migrations() AFTER the legacy phases
have run, so the schema is fully materialised before we stamp.

The advisory lock from P1-01 already wraps the caller, so this is safe
to run concurrently across Cloud Run instances — only one runs the
stamp/upgrade; the rest wait for the lock and find alembic_version
already set when they enter.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import inspect, text

from app.database.connection import engine

log = logging.getLogger(__name__)


def _alembic_version_exists() -> bool:
    """Probe — does the alembic_version table exist in the live schema?"""
    try:
        return "alembic_version" in inspect(engine).get_table_names()
    except Exception as exc:
        log.warning("[alembic-bootstrap] could not introspect schema: %s", exc)
        return False


def _alembic_config():
    """Build an Alembic Config pointing at server_files/alembic.ini."""
    from alembic.config import Config

    # server_files/app/db/alembic_bootstrap.py → server_files/
    here = os.path.dirname(os.path.abspath(__file__))
    server_files_dir = os.path.dirname(os.path.dirname(here))
    ini_path = os.path.join(server_files_dir, "alembic.ini")

    cfg = Config(ini_path)
    # env.py reads DATABASE_URL directly; this is a safety net.
    cfg.set_main_option(
        "sqlalchemy.url",
        os.getenv("DATABASE_URL", "sqlite:///./asg_platform.db"),
    )
    # Resolve script_location to an absolute path (env.py is generic).
    cfg.set_main_option(
        "script_location",
        os.path.join(server_files_dir, "alembic"),
    )
    return cfg


def alembic_bootstrap_or_upgrade() -> None:
    """
    First encounter: stamp to head (no DDL — current schema is baseline).
    Subsequent encounters: alembic upgrade head.
    """
    try:
        from alembic import command
    except ImportError:
        log.warning(
            "[alembic-bootstrap] alembic not installed — skipping "
            "(install via pip install alembic to enable)"
        )
        return

    cfg = _alembic_config()

    if _alembic_version_exists():
        log.info("[alembic-bootstrap] alembic_version present — running upgrade head")
        try:
            command.upgrade(cfg, "head")
            log.info("[alembic-bootstrap] upgrade head complete")
        except Exception as exc:
            # Don't crash startup on migration failure — log loudly and
            # let the existing P1-01 lock + idempotent phases pick up the
            # slack. Operator-visible via Cloud Logging.
            log.error("[alembic-bootstrap] upgrade FAILED: %s", exc)
        return

    # First encounter — stamp without running DDL.
    log.info(
        "[alembic-bootstrap] alembic_version missing — stamping head "
        "(baseline = legacy migrate_phase*.py schema)"
    )
    try:
        command.stamp(cfg, "head")
        log.info("[alembic-bootstrap] stamped to head — Alembic now tracking schema")
    except Exception as exc:
        log.error("[alembic-bootstrap] stamp FAILED: %s", exc)


__all__ = ["alembic_bootstrap_or_upgrade"]
