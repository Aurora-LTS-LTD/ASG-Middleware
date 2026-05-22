"""
Aurora LTS — BigQuery Audit Export (Sprint 6)
==================================================
Daily job that ships every new ActionLog + ItaAuditLog row into a
BigQuery dataset for long-term retention + cross-cutting analytics.

BACKEND SELECTOR:
  AUDIT_BIGQUERY_BACKEND = 'stub' (default — local dev, writes to a
                                    temp file, no GCP cost)
                          'gcp' — real google-cloud-bigquery insert

CURSOR PERSISTENCE:
  AuditExportCursor.last_exported_id tracks per-table progress. The
  export queries `WHERE id > cursor` so re-runs never gap-skip or
  double-export rows.

HASH CHAIN:
  Each batch's hash = sha256(prev_batch_hash + concat(row_hashes)).
  Stored on AuditExportCursor.last_batch_hash. Any tampering with a
  past batch breaks the chain — auditor can replay forward and
  detect the fork.

PII REDACTION:
  Every row goes through pii_redactor.redact_pii on string fields
  before export. The Aurora promise: BigQuery never sees raw PII.

CALLED BY:
  /api/v1/internal/audit-export (Cloud Scheduler cron)
"""

import datetime
import hashlib
import json
import os
from typing import Optional

from sqlalchemy.orm import Session

from app.database import (
    ActionLog,
    ItaAuditLog,
    AuditExportCursor,
)
from app.services.compliance.pii_redactor import redact_pii


AUDIT_BIGQUERY_BACKEND = (os.getenv("AUDIT_BIGQUERY_BACKEND") or "stub").strip().lower()


def export_audit_to_bigquery(
    *,
    db: Session,
    batch_size: int = 1000,
) -> dict:
    """
    Run the daily export.

    Returns a summary dict:
      {
        "tables": {table_name: {rows: int, last_id: int, batch_hash: str}, ...},
        "total_rows": int,
        "backend": "stub" | "gcp",
      }
    """
    summary = {"tables": {}, "total_rows": 0, "backend": AUDIT_BIGQUERY_BACKEND}

    sources = [
        ("action_logs", ActionLog),
        ("ita_audit_log", ItaAuditLog),
    ]

    for source_name, model in sources:
        cursor = (
            db.query(AuditExportCursor)
            .filter(AuditExportCursor.source_table == source_name)
            .first()
        )
        if not cursor:
            cursor = AuditExportCursor(source_table=source_name, last_exported_id=0)
            db.add(cursor)
            db.flush()

        rows = (
            db.query(model)
            .filter(model.id > (cursor.last_exported_id or 0))
            .order_by(model.id.asc())
            .limit(batch_size)
            .all()
        )
        if not rows:
            summary["tables"][source_name] = {
                "rows": 0,
                "last_id": cursor.last_exported_id or 0,
                "batch_hash": cursor.last_batch_hash,
            }
            continue

        # Build redacted payloads
        payloads = [_redact_row(r) for r in rows]

        # Hash chain
        prev_hash = cursor.last_batch_hash or ""
        row_hashes = [
            hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()
            for p in payloads
        ]
        batch_hash = hashlib.sha256(
            (prev_hash + "".join(row_hashes)).encode()
        ).hexdigest()

        # Dispatch
        if AUDIT_BIGQUERY_BACKEND == "stub":
            _stub_write(source_name, payloads)
        elif AUDIT_BIGQUERY_BACKEND == "gcp":
            _gcp_insert(source_name, payloads)
        else:
            raise ValueError(f"Unknown AUDIT_BIGQUERY_BACKEND={AUDIT_BIGQUERY_BACKEND!r}")

        # Advance cursor
        last_id = max(r.id for r in rows)
        cursor.last_exported_id = last_id
        cursor.last_exported_at = datetime.datetime.utcnow()
        cursor.rows_in_last_batch = len(rows)
        cursor.last_batch_hash = batch_hash
        db.commit()

        summary["tables"][source_name] = {
            "rows": len(rows),
            "last_id": last_id,
            "batch_hash": batch_hash[:16] + "...",  # truncate for log
        }
        summary["total_rows"] += len(rows)

    return summary


# ─────────────────────────────────────────────────────────────
# Row redaction
# ─────────────────────────────────────────────────────────────
def _redact_row(row) -> dict:
    """Convert a SQLAlchemy row → redacted dict ready for BigQuery."""
    raw = {}
    for col in row.__table__.columns:
        v = getattr(row, col.name)
        if isinstance(v, datetime.datetime):
            raw[col.name] = v.isoformat()
        elif isinstance(v, datetime.date):
            raw[col.name] = v.isoformat()
        elif isinstance(v, str):
            raw[col.name] = redact_pii(v)
        else:
            raw[col.name] = v
    return raw


# ─────────────────────────────────────────────────────────────
# Stub backend — write to /tmp/aurora-audit-{table}.ndjson
# ─────────────────────────────────────────────────────────────
def _stub_write(table_name: str, payloads: list[dict]) -> None:
    out = "/tmp/aurora-audit-" + table_name + ".ndjson"
    with open(out, "a", encoding="utf-8") as f:
        for p in payloads:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"[BQ_EXPORT/stub] {len(payloads)} rows → {out}")


# ─────────────────────────────────────────────────────────────
# Production backend — real BigQuery
# ─────────────────────────────────────────────────────────────
def _gcp_insert(table_name: str, payloads: list[dict]) -> None:
    """Real BigQuery insert. Lazy SDK import (google-cloud-bigquery)."""
    from google.cloud import bigquery  # type: ignore

    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    dataset = os.getenv("BIGQUERY_AUDIT_DATASET", "asg_audit")
    if not project:
        raise RuntimeError("AUDIT_BIGQUERY_BACKEND=gcp requires GOOGLE_CLOUD_PROJECT")

    client = bigquery.Client(project=project)
    table_ref = f"{project}.{dataset}.{table_name}"
    errors = client.insert_rows_json(table_ref, payloads)
    if errors:
        raise RuntimeError(f"BigQuery insert errors: {errors}")
    print(f"[BQ_EXPORT/gcp] {len(payloads)} rows → {table_ref}")
