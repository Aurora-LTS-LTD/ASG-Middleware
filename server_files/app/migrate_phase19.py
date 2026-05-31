"""
Aurora LTS — Phase 19 Migration (Sprint 6, Appendix M)
=========================================================

Domain migration: `api-aurora.com` → `api-aurora-lts.com` and
`admin.aurora-ltd.co.il` → `console.api-aurora-lts.com`.

The cross-domain change forces WebAuthn `RP_ID` to flip from
`admin.aurora-ltd.co.il` to `console.api-aurora-lts.com`. Existing
`webauthn_credentials` rows are cryptographically bound to the OLD
RP_ID and will fail signature verification at the new origin. Rather
than letting them silently break (and pollute the audit trail), this
migration explicitly revokes them with a clear timestamp + reason —
so the binder narrative reads "credentials were retired at the
cutover" instead of "the system stopped accepting our passkeys."

The CEO re-enrolls fresh credentials at the new URL in P11 of the
Appendix M execution plan.

Idempotent: safe to re-run. Subsequent runs find zero unrevoked rows
and no-op.
"""

import logging
import os

from sqlalchemy import text

from aurora_shared.database.connection import engine

log = logging.getLogger(__name__)


_REVOCATION_REASON = (
    "Decommissioned at Appendix-M domain cutover: WebAuthn RP_ID "
    "changed from admin.aurora-ltd.co.il to console.api-aurora-lts.com; "
    "credentials bound to old RP_ID are no longer signature-compatible."
)


def run_phase19_migrations() -> None:
    """
    Revoke any unrevoked `webauthn_credentials` rows that pre-date
    the Appendix M domain migration.

    Controlled by env `AURORA_PHASE19_REVOKE_ON_BOOT` (default `1`).
    Set to `0` to skip — useful if the deploy goes wrong and we need
    to roll back without losing the original credential rows.
    """
    if (os.getenv("AURORA_PHASE19_REVOKE_ON_BOOT") or "1").strip() != "1":
        log.info("[MIGRATE_P19] Skipped — AURORA_PHASE19_REVOKE_ON_BOOT != 1")
        return

    try:
        with engine.connect() as conn:
            # Count first so we know what we're about to do
            try:
                pre = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM webauthn_credentials "
                        "WHERE revoked_at IS NULL"
                    )
                ).scalar()
            except Exception as e:
                # Table may not exist on a brand-new install. Phase 15
                # creates it via SQLAlchemy create_tables. If it's missing
                # we have nothing to revoke — safe no-op.
                log.info(
                    "[MIGRATE_P19] webauthn_credentials probe failed (table likely "
                    "absent on fresh install): %s",
                    e,
                )
                try:
                    conn.rollback()
                except Exception:
                    pass
                return

            if not pre:
                log.info(
                    "[MIGRATE_P19] No unrevoked credentials found — no-op."
                )
                return

            # Revoke. We don't TOUCH revoked_at on rows already revoked —
            # that preserves the original revocation timestamp for audit.
            try:
                conn.execute(
                    text(
                        "UPDATE webauthn_credentials "
                        "SET revoked_at = NOW() "
                        "WHERE revoked_at IS NULL"
                    )
                )
                conn.commit()
            except Exception as e:
                log.error("[MIGRATE_P19] Revocation UPDATE failed: %s", e)
                try:
                    conn.rollback()
                except Exception:
                    pass
                return

            log.warning(
                "[MIGRATE_P19] Revoked %d existing WebAuthn credential(s) "
                "due to Appendix M domain cutover. CEO must re-enroll at "
                "https://console.api-aurora-lts.com/executive. Reason: %s",
                int(pre),
                _REVOCATION_REASON,
            )

    except Exception as e:
        # Defensive: never let the migration crash startup. Cloud Run
        # health probes must succeed for the new revision to roll.
        log.error("[MIGRATE_P19] Unexpected error (non-fatal): %s", e)
