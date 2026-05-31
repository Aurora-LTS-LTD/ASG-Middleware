"""
Aurora LTS — Phase 20 Migration (Sprint 8.2, Mac Shell backend)
================================================================

Probes the two new tables that back the cryptographic handshake
protocol between the Aurora Mac Shell (Secure Enclave keypair on
the founder's MacBook) and aurora-api:

  1. native_device_keys           — registered device public keys
  2. native_handshake_challenges  — single-shot 60s challenges

Both tables are created via SQLAlchemy `create_tables()` at startup.
This migration's job is to PROBE + LOG + (idempotent) tidy:
  - log presence of each expected table
  - sweep expired/consumed challenges older than 24h (housekeeping)
  - confirm `JWT_SIGNING_KEY` is set (session JWTs use it)

Idempotent — safe to re-run on every boot. No DDL is issued here.
"""

import datetime
import logging
import os

from sqlalchemy import text

from aurora_shared.database.connection import engine

log = logging.getLogger(__name__)


_EXPECTED_NEW = [
    "native_device_keys",
    "native_handshake_challenges",
]

# Stale-challenge sweep — challenges older than this are deleted on each
# Phase 20 run. The router itself never queries old rows (it filters by
# `expires_at >= now AND consumed_at IS NULL`), so the table would just
# grow forever without this cleanup.
_CHALLENGE_TTL_DAYS = 1


def _table_exists(conn, table_name: str) -> bool:
    try:
        conn.execute(text(f"SELECT 1 FROM {table_name} LIMIT 1"))
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _sweep_stale_challenges(conn) -> int:
    """Delete challenges older than 24h. Returns count deleted."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=_CHALLENGE_TTL_DAYS)
    try:
        result = conn.execute(
            text(
                "DELETE FROM native_handshake_challenges "
                "WHERE issued_at < :cutoff"
            ),
            {"cutoff": cutoff},
        )
        conn.commit()
        return result.rowcount or 0
    except Exception as e:
        log.warning("[MIGRATE_P20] stale-challenge sweep failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


def run_phase20_migrations() -> None:
    """
    Probe the Phase 20 tables, sweep stale challenges, confirm config.

    Controlled by env `AURORA_PHASE20_ENABLED` (default `1`). Set to
    `0` to skip — useful during rollback windows.
    """
    if (os.getenv("AURORA_PHASE20_ENABLED") or "1").strip() != "1":
        log.info("[MIGRATE_P20] Skipped — AURORA_PHASE20_ENABLED != 1")
        return

    try:
        with engine.connect() as conn:
            present = []
            missing = []
            for t in _EXPECTED_NEW:
                if _table_exists(conn, t):
                    present.append(t)
                else:
                    missing.append(t)

            log.info(
                "[MIGRATE_P20] Tables: %d present, %d missing",
                len(present),
                len(missing),
            )
            if missing:
                log.error(
                    "[MIGRATE_P20] Missing tables: %s — create_tables() didn't run? "
                    "Check models.py imports and SQLAlchemy metadata.",
                    missing,
                )
                # We deliberately do NOT raise — the rest of aurora-api can
                # still serve traffic. The native-shell endpoints will 500
                # when called, surfacing the issue clearly in Cloud Run logs.

            if "native_handshake_challenges" in present:
                swept = _sweep_stale_challenges(conn)
                if swept > 0:
                    log.info(
                        "[MIGRATE_P20] Swept %d stale handshake challenges (>%d day old)",
                        swept,
                        _CHALLENGE_TTL_DAYS,
                    )

    except Exception as e:
        # Defensive — never let migration crash Cloud Run startup.
        log.error("[MIGRATE_P20] Unexpected error (non-fatal): %s", e)

    # Config sanity check (warning only — endpoints check at call time too).
    if not (os.getenv("JWT_SIGNING_KEY") or "").strip():
        log.warning(
            "[MIGRATE_P20] JWT_SIGNING_KEY is not set — native session JWT "
            "issuance will 500. Set the env var before clients start handshakes."
        )
    else:
        log.info("[MIGRATE_P20] JWT_SIGNING_KEY present (length=%d)", len(os.environ["JWT_SIGNING_KEY"]))
