"""
Aurora LTS — Phase 21 Migration (Sprint 8.2 sibling — Accountant Portal Auth)
=============================================================================

Probes the three new tables that back the Aurora LTS Accountant Portal
(Tauri + Next.js app at ~/Desktop/ASG-Middleware/accountant-portal/):

  1. accountant_devices         — multi-active device fingerprints
                                  (ADVISORY binding, not strict like
                                  native_device_keys for the CEO Mac shell)
  2. accountant_refresh_tokens  — hashed refresh tokens with rotation tracking
  3. accountant_otp_attempts    — short-lived OTPs + lockout tracking

All tables are created via SQLAlchemy `create_tables()` at startup.
This migration's job is to PROBE + LOG + (idempotent) tidy:
  - log presence of each expected table
  - sweep expired OTP attempts older than 1h
  - sweep expired refresh tokens older than 60 days
  - sweep revoked devices older than 1 year (preserves audit chain for 1y)

Idempotent — safe to re-run on every boot. No DDL is issued here.
"""

import datetime
import logging
import os

from sqlalchemy import text

from aurora_shared.database.connection import engine

log = logging.getLogger(__name__)


_EXPECTED_NEW = [
    "accountant_devices",
    "accountant_refresh_tokens",
    "accountant_otp_attempts",
]

# Sweep cadences — kept conservative to preserve audit visibility.
_OTP_ATTEMPT_TTL_HOURS = 1        # OTP rows older than this go away
_REFRESH_TOKEN_TTL_DAYS = 60      # expired/used/revoked refresh tokens
_DEVICE_REVOKED_TTL_DAYS = 365    # revoked devices linger for 1y for audit


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


def _sweep_stale_otp_attempts(conn) -> int:
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=_OTP_ATTEMPT_TTL_HOURS)
    try:
        result = conn.execute(
            text("DELETE FROM accountant_otp_attempts WHERE issued_at < :cutoff"),
            {"cutoff": cutoff},
        )
        conn.commit()
        return result.rowcount or 0
    except Exception as e:
        log.warning("[MIGRATE_P21] OTP attempt sweep failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


def _sweep_stale_refresh_tokens(conn) -> int:
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=_REFRESH_TOKEN_TTL_DAYS)
    try:
        result = conn.execute(
            text(
                "DELETE FROM accountant_refresh_tokens "
                "WHERE expires_at < :cutoff "
                "   OR (used_at IS NOT NULL AND used_at < :cutoff) "
                "   OR (revoked_at IS NOT NULL AND revoked_at < :cutoff)"
            ),
            {"cutoff": cutoff},
        )
        conn.commit()
        return result.rowcount or 0
    except Exception as e:
        log.warning("[MIGRATE_P21] refresh token sweep failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


def _sweep_old_revoked_devices(conn) -> int:
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=_DEVICE_REVOKED_TTL_DAYS)
    try:
        result = conn.execute(
            text(
                "DELETE FROM accountant_devices "
                "WHERE revoked_at IS NOT NULL AND revoked_at < :cutoff"
            ),
            {"cutoff": cutoff},
        )
        conn.commit()
        return result.rowcount or 0
    except Exception as e:
        log.warning("[MIGRATE_P21] revoked-device sweep failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


def run_phase21_migrations() -> None:
    """Probe Phase 21 tables, sweep stale rows. Idempotent."""
    if (os.getenv("AURORA_PHASE21_ENABLED") or "1").strip() != "1":
        log.info("[MIGRATE_P21] Skipped — AURORA_PHASE21_ENABLED != 1")
        return

    try:
        with engine.connect() as conn:
            present, missing = [], []
            for t in _EXPECTED_NEW:
                (present if _table_exists(conn, t) else missing).append(t)

            log.info(
                "[MIGRATE_P21] Tables: %d present, %d missing",
                len(present), len(missing),
            )
            if missing:
                log.error(
                    "[MIGRATE_P21] Missing tables: %s — create_tables() didn't run?",
                    missing,
                )

            if "accountant_otp_attempts" in present:
                swept = _sweep_stale_otp_attempts(conn)
                if swept > 0:
                    log.info("[MIGRATE_P21] Swept %d stale OTP attempts", swept)

            if "accountant_refresh_tokens" in present:
                swept = _sweep_stale_refresh_tokens(conn)
                if swept > 0:
                    log.info("[MIGRATE_P21] Swept %d stale refresh tokens", swept)

            if "accountant_devices" in present:
                swept = _sweep_old_revoked_devices(conn)
                if swept > 0:
                    log.info("[MIGRATE_P21] Swept %d revoked devices (>1y old)", swept)

    except Exception as e:
        log.error("[MIGRATE_P21] Unexpected error (non-fatal): %s", e)

    jwt_key = (os.getenv("JWT_SECRET") or os.getenv("JWT_SIGNING_KEY") or "").strip()
    if not jwt_key:
        log.warning(
            "[MIGRATE_P21] JWT_SECRET / JWT_SIGNING_KEY not set — accountant "
            "access-token issuance will 500. Set env var before clients start signing in."
        )
    else:
        log.info("[MIGRATE_P21] JWT signing key present (length=%d)", len(jwt_key))
