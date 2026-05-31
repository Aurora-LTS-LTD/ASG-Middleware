"""
Aurora LTS — Phase 21 Vault Migration (Sprint 8.3 — Document Vault DB Layer)
=============================================================================

Provisions the Sprint 8.3 Document Vault tables and seeds the per-client
ingestion address registry so every existing organization has a stable,
unguessable email alias the moment this migration completes.

Tables provisioned (idempotent — CREATE TABLE IF NOT EXISTS):
  1. client_documents
        Vault docs with object-store pointer, compliance retention
        (7-year minimum CHECK), and tamper-evident soft-delete CHECK.
  2. vault_ingestion_addresses
        One row per client_organization mapping email-alias-token +
        WhatsApp phone DID to the client's organization_id.

Composite indexes provisioned (idempotent — CREATE INDEX IF NOT EXISTS):
  • ix_client_doc_agency_client      (agency_id, client_id)
  • ix_client_doc_taxyear_status     (tax_year, status)
  • ix_client_doc_client_created     (client_id, created_at)
  • ix_vault_ingest_whatsapp_active  (whatsapp_e164) WHERE active

Backfill pipeline (idempotent — only writes rows when missing):
  Iterates every Organization row. For each one that does NOT already
  have a row in vault_ingestion_addresses, inserts a fresh row with:
    • email_alias_token = secrets.token_hex(8)   # 16 hex chars
    • whatsapp_e164     = NULL                   # opt-in linkage later
    • active            = TRUE

Safety:
  • Idempotent — re-running is a no-op against an already-provisioned DB.
  • Defensive — every operation is wrapped, failures log + rollback
    rather than crashing Cloud Run boot.
  • Postgres-aware — `interval '7 years'` CHECK constraints only run on
    Postgres; SQLite dev sessions skip them (the CHECK syntax differs).
  • Token collisions — token_hex(8) gives 64 bits of entropy. We still
    handle the impossible case of an IntegrityError on insert by
    re-rolling once before giving up.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Tuple

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, ProgrammingError, OperationalError

from app.database.connection import engine, SessionLocal
from app.database.models import Organization, VaultIngestionAddress

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────

_EXPECTED_TABLES = ("client_documents", "vault_ingestion_addresses")

_BACKFILL_MAX_ROWS_PER_RUN = 25_000  # safety guard — alert if exceeded
_TOKEN_HEX_BYTES = 8                  # 16 hex chars per alias


# ─────────────────────────────────────────────────────────────
# DDL — table provisioning
# ─────────────────────────────────────────────────────────────
#
# We issue raw SQL here (rather than relying on Base.metadata.create_all)
# because (a) we need IF NOT EXISTS semantics, (b) we need to add the
# Postgres-specific CHECK constraints with interval syntax conditionally,
# and (c) we want explicit, reviewable DDL for an audit-class table.

_DDL_CLIENT_DOCUMENTS_PG = """
CREATE TABLE IF NOT EXISTS client_documents (
    id                  SERIAL PRIMARY KEY,
    agency_id           INTEGER NOT NULL REFERENCES organizations(id),
    client_id           INTEGER NOT NULL REFERENCES organizations(id),
    uploaded_by_vector  VARCHAR(16)  NOT NULL,
    s3_key              VARCHAR(512) NOT NULL UNIQUE,
    s3_bucket           VARCHAR(120) NOT NULL,
    document_type       VARCHAR(24)  NOT NULL DEFAULT 'unclassified',
    file_name           VARCHAR(255) NOT NULL,
    mime_type           VARCHAR(80)  NOT NULL,
    size_bytes          INTEGER      NOT NULL,
    sha256              VARCHAR(64)  NOT NULL,
    sender_phone_e164   VARCHAR(20),
    sender_email        VARCHAR(255),
    extracted_metadata  JSONB,
    tax_year            INTEGER      NOT NULL,
    status              VARCHAR(16)  NOT NULL DEFAULT 'received',
    error_reason        TEXT,
    created_at          TIMESTAMP    NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    archived_until      TIMESTAMP    NOT NULL,
    deleted_at          TIMESTAMP,
    CONSTRAINT compliance_7_year_retention_check
        CHECK (archived_until >= created_at + interval '7 years'),
    CONSTRAINT retention_lock_prevent_premature_delete
        CHECK (deleted_at IS NULL OR deleted_at > archived_until)
);
"""

# SQLite fallback — same shape, but CHECK constraints use plain
# comparisons rather than Postgres interval syntax. This keeps local
# dev usable without dragging the production-only retention math in.
_DDL_CLIENT_DOCUMENTS_SQLITE = """
CREATE TABLE IF NOT EXISTS client_documents (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agency_id           INTEGER NOT NULL REFERENCES organizations(id),
    client_id           INTEGER NOT NULL REFERENCES organizations(id),
    uploaded_by_vector  VARCHAR(16)  NOT NULL,
    s3_key              VARCHAR(512) NOT NULL UNIQUE,
    s3_bucket           VARCHAR(120) NOT NULL,
    document_type       VARCHAR(24)  NOT NULL DEFAULT 'unclassified',
    file_name           VARCHAR(255) NOT NULL,
    mime_type           VARCHAR(80)  NOT NULL,
    size_bytes          INTEGER      NOT NULL,
    sha256              VARCHAR(64)  NOT NULL,
    sender_phone_e164   VARCHAR(20),
    sender_email        VARCHAR(255),
    extracted_metadata  TEXT,
    tax_year            INTEGER      NOT NULL,
    status              VARCHAR(16)  NOT NULL DEFAULT 'received',
    error_reason        TEXT,
    created_at          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_until      TIMESTAMP    NOT NULL,
    deleted_at          TIMESTAMP,
    CONSTRAINT retention_lock_prevent_premature_delete
        CHECK (deleted_at IS NULL OR deleted_at > archived_until)
);
"""

_DDL_VAULT_INGEST_PG = """
CREATE TABLE IF NOT EXISTS vault_ingestion_addresses (
    id                 SERIAL PRIMARY KEY,
    client_id          INTEGER NOT NULL UNIQUE REFERENCES organizations(id),
    email_alias_token  VARCHAR(48) NOT NULL UNIQUE,
    whatsapp_e164      VARCHAR(20),
    active             BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMP   NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);
"""

_DDL_VAULT_INGEST_SQLITE = """
CREATE TABLE IF NOT EXISTS vault_ingestion_addresses (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id          INTEGER NOT NULL UNIQUE REFERENCES organizations(id),
    email_alias_token  VARCHAR(48) NOT NULL UNIQUE,
    whatsapp_e164      VARCHAR(20),
    active             BOOLEAN     NOT NULL DEFAULT 1,
    created_at         TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# Composite indexes — IF NOT EXISTS keeps re-runs free.
_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_client_doc_agency_client  ON client_documents (agency_id, client_id);",
    "CREATE INDEX IF NOT EXISTS ix_client_doc_taxyear_status ON client_documents (tax_year, status);",
    "CREATE INDEX IF NOT EXISTS ix_client_doc_client_created ON client_documents (client_id, created_at);",
    "CREATE INDEX IF NOT EXISTS ix_client_doc_sha256          ON client_documents (sha256);",
    "CREATE INDEX IF NOT EXISTS ix_client_doc_archived_until  ON client_documents (archived_until);",
    "CREATE INDEX IF NOT EXISTS ix_vault_ingest_whatsapp      ON vault_ingestion_addresses (whatsapp_e164);",
]


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _is_postgres() -> bool:
    return engine.dialect.name == "postgresql"


def _table_exists(conn, name: str) -> bool:
    try:
        return inspect(conn).has_table(name)
    except Exception:
        # Fall back to a probe SELECT — useful on older Inspector impls.
        try:
            conn.execute(text(f"SELECT 1 FROM {name} LIMIT 1"))
            return True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False


def _generate_alias_token() -> str:
    """16-character lowercase hex token (64 bits of entropy)."""
    return secrets.token_hex(_TOKEN_HEX_BYTES)


def _execute_ddl(conn, sql: str, label: str) -> None:
    try:
        conn.execute(text(sql))
        conn.commit()
        log.info("[MIGRATE_VAULT] %s OK", label)
    except (ProgrammingError, OperationalError) as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.warning("[MIGRATE_VAULT] %s skipped/failed: %s", label, e)


# ─────────────────────────────────────────────────────────────
# Step 1 — Provision tables + indexes (idempotent)
# ─────────────────────────────────────────────────────────────

def _provision_schema() -> Tuple[int, int]:
    """
    Provision the two vault tables and their composite indexes.

    Returns (tables_present, indexes_attempted).
    """
    is_pg = _is_postgres()
    ddl_clients = _DDL_CLIENT_DOCUMENTS_PG if is_pg else _DDL_CLIENT_DOCUMENTS_SQLITE
    ddl_ingest = _DDL_VAULT_INGEST_PG if is_pg else _DDL_VAULT_INGEST_SQLITE

    indexes_attempted = 0
    with engine.connect() as conn:
        _execute_ddl(conn, ddl_clients, "client_documents table")
        _execute_ddl(conn, ddl_ingest, "vault_ingestion_addresses table")

        for ddl in _DDL_INDEXES:
            _execute_ddl(conn, ddl, ddl.split(" ")[5])  # the index name
            indexes_attempted += 1

        # Confirm tables now exist; report any missing.
        present = sum(1 for t in _EXPECTED_TABLES if _table_exists(conn, t))

    return present, indexes_attempted


# ─────────────────────────────────────────────────────────────
# Step 2 — Backfill VaultIngestionAddress rows for every org
# ─────────────────────────────────────────────────────────────

def _backfill_ingestion_addresses() -> Tuple[int, int]:
    """
    Walk every Organization. Insert a VaultIngestionAddress row for
    each one missing it.

    Returns (orgs_scanned, rows_inserted).
    """
    inserted = 0
    scanned = 0

    session = SessionLocal()
    try:
        # Pre-load existing client_ids — one cheap SELECT vs. N exists() calls.
        existing_ids = {
            cid for (cid,) in session.query(
                VaultIngestionAddress.client_id
            ).all()
        }
        log.info(
            "[MIGRATE_VAULT] backfill: %d ingestion address rows already present",
            len(existing_ids),
        )

        # Stream organizations in id order — bounded by safety guard.
        orgs_iter = (
            session.query(Organization.id)
            .order_by(Organization.id.asc())
            .yield_per(500)
        )

        for (org_id,) in orgs_iter:
            scanned += 1
            if scanned > _BACKFILL_MAX_ROWS_PER_RUN:
                log.warning(
                    "[MIGRATE_VAULT] backfill safety cap hit at %d orgs — "
                    "re-run migration to continue.",
                    _BACKFILL_MAX_ROWS_PER_RUN,
                )
                break

            if org_id in existing_ids:
                continue

            # 2 attempts to dodge the (statistically impossible) token collision.
            for attempt in (1, 2):
                token = _generate_alias_token()
                row = VaultIngestionAddress(
                    client_id=org_id,
                    email_alias_token=token,
                    whatsapp_e164=None,
                    active=True,
                )
                session.add(row)
                try:
                    session.flush()
                    inserted += 1
                    existing_ids.add(org_id)
                    break
                except IntegrityError as e:
                    session.rollback()
                    if attempt == 2:
                        log.error(
                            "[MIGRATE_VAULT] alias token collision on retry "
                            "for org_id=%d: %s",
                            org_id, e,
                        )
                        # Skip this org; humans can investigate via the log.
                        break

        session.commit()
    except Exception as e:
        log.exception(
            "[MIGRATE_VAULT] backfill aborted (orgs_scanned=%d, inserted=%d): %s",
            scanned, inserted, e,
        )
        try:
            session.rollback()
        except Exception:
            pass
    finally:
        session.close()

    return scanned, inserted


# ─────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────

def run_phase21_vault_migrations() -> None:
    """
    Orchestrate the Sprint 8.3 Document Vault migration:
      1. Provision client_documents + vault_ingestion_addresses
      2. Provision composite indexes
      3. Backfill VaultIngestionAddress rows for every Organization

    Idempotent. Controlled by env `AURORA_PHASE21_VAULT_ENABLED`
    (default `1`). Set to `0` during rollback windows.
    """
    if (os.getenv("AURORA_PHASE21_VAULT_ENABLED") or "1").strip() != "1":
        log.info("[MIGRATE_VAULT] Skipped — AURORA_PHASE21_VAULT_ENABLED != 1")
        return

    log.info(
        "[MIGRATE_VAULT] Starting Sprint 8.3 vault migration (dialect=%s)",
        engine.dialect.name,
    )

    # Step 1 — schema
    try:
        present, idx_attempted = _provision_schema()
        log.info(
            "[MIGRATE_VAULT] Schema: %d/%d expected tables present, %d index DDLs issued",
            present, len(_EXPECTED_TABLES), idx_attempted,
        )
        if present != len(_EXPECTED_TABLES):
            log.error(
                "[MIGRATE_VAULT] Expected tables not all present — backfill skipped.",
            )
            return
    except Exception as e:
        log.exception("[MIGRATE_VAULT] Schema provisioning failed: %s", e)
        return

    # Step 2 — backfill
    try:
        scanned, inserted = _backfill_ingestion_addresses()
        log.info(
            "[MIGRATE_VAULT] Backfill: scanned=%d orgs, inserted=%d new ingestion addresses",
            scanned, inserted,
        )
    except Exception as e:
        log.exception("[MIGRATE_VAULT] Backfill failed: %s", e)


# Allow `python -m app.migrations.migrate_phase21_vault` for one-off CLI runs
# (e.g., the operator invokes it manually after a Cloud SQL maintenance window).
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    run_phase21_vault_migrations()
