"""
Aurora LTS — Migration Lock (P1-01)
====================================
Serializes the startup migration block across all Cloud Run instances
using a PostgreSQL session-level advisory lock.

WHY THIS EXISTS:
  Cloud Run runs N instances. Without coordination, every instance's
  startup() races the others to ALTER TABLE / CREATE INDEX / backfill —
  Postgres acquires AccessExclusiveLock per ALTER TABLE, instances
  deadlock or block past Cloud Run's startup probe timeout.

THE PRIMITIVE:
  pg_advisory_lock(int8) is a Postgres-native distributed mutex keyed
  on a single int8. First caller succeeds immediately; subsequent
  callers BLOCK until the holder releases (session end, explicit
  unlock, or connection drop). When the holder's process crashes,
  Postgres auto-releases — no zombie locks.

  We use SESSION-level (not transaction-level) so the lock survives
  across the many commits inside the 22-phase migration sequence.

KEY SELECTION:
  AURORA_MIGRATION_LOCK_KEY = 0x4155524F52414D31 == ASCII "AURORAM1".
  Globally unique to this app — even if another tenant shares the
  Cloud SQL instance, collision on a 64-bit literal is astronomical.

SQLITE:
  Local dev uses SQLite (single-process). The lock becomes a no-op.

TIMEOUT:
  AURORA_MIGRATION_LOCK_TIMEOUT_MS (default 300_000 = 5 min). If a
  stuck holder doesn't release within the timeout, the waiter raises
  rather than blocking the fleet indefinitely. The lock is a wait
  bound, not a migration runtime bound.
"""
from __future__ import annotations

import contextlib
import logging
import os
from typing import Iterator

from sqlalchemy import text

from aurora_shared.database.connection import engine, DIALECT

log = logging.getLogger(__name__)

# 0x4155524F52414D31 = ASCII "AURORAM1" — Aurora Migration 1
AURORA_MIGRATION_LOCK_KEY = 0x4155524F52414D31

_LOCK_WAIT_TIMEOUT_MS = int(
    os.getenv("AURORA_MIGRATION_LOCK_TIMEOUT_MS", "300000")
)


@contextlib.contextmanager
def with_migration_lock() -> Iterator[None]:
    """
    Context manager that serializes migration execution across instances.

    Postgres: acquires pg_advisory_lock(AURORA_MIGRATION_LOCK_KEY) before
    yielding, releases on exit (even on exception). Sets lock_timeout so
    a wedged holder cannot block the fleet forever.

    SQLite: yields immediately (no lock — single process).
    """
    if DIALECT == "sqlite":
        log.info("[migration-lock] SQLite — bypass (single-process dev)")
        yield
        return

    # Dedicated connection — the lock is session-scoped, so we MUST NOT
    # use a pooled session that might be returned to the pool mid-run.
    with engine.connect() as conn:
        try:
            conn.execute(text(f"SET lock_timeout = {_LOCK_WAIT_TIMEOUT_MS}"))
        except Exception as exc:
            log.warning(
                "[migration-lock] could not set lock_timeout (%s) — continuing",
                exc,
            )

        log.info(
            "[migration-lock] acquiring pg_advisory_lock(%s) …",
            hex(AURORA_MIGRATION_LOCK_KEY),
        )
        try:
            conn.execute(
                text("SELECT pg_advisory_lock(:k)"),
                {"k": AURORA_MIGRATION_LOCK_KEY},
            )
        except Exception as exc:
            log.error("[migration-lock] failed to acquire lock: %s", exc)
            raise

        log.info("[migration-lock] acquired — running migrations")
        try:
            yield
        finally:
            try:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"),
                    {"k": AURORA_MIGRATION_LOCK_KEY},
                )
                log.info("[migration-lock] released")
            except Exception as exc:
                log.warning(
                    "[migration-lock] unlock failed "
                    "(will auto-release on conn close): %s",
                    exc,
                )
