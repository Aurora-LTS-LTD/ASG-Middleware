"""
Aurora LTS — Exports Service Orchestrator
=============================================
Sprint 4 — single entry point that:
  1. Records an Export row in `pending` state
  2. Dispatches to the appropriate writer (uniform_file / hashavshevet)
  3. Uploads the bytes to GCS (or local stub) at:
       gs://asg-exports-prod/{org_id}/{yyyy-mm}/{format}/{filename}
  4. Updates the Export row to `completed` (or `failed`)
  5. Returns the Export row + a signed URL for download

REUSE:
  - storage wrapper from app/services/gcp/storage.py (stub default)
  - models from app/database

ALL WRITERS HAVE THE SAME SHAPE:
    bytes_, summary = build_xxx(organization_id, period_start, period_end, db)
"""

import datetime
import hashlib
from typing import Optional

from sqlalchemy.orm import Session

from aurora_shared.database import (
    Export,
    Organization,
    User,
    ActionLog,
)
from app.services.gcp.storage import upload_bytes, signed_url
from app.services.exports.uniform_file import build_uniform_file
from app.services.exports.hashavshevet import build_hashavshevet_csv


SUPPORTED_FORMATS = ("uniform_file", "hashavshevet")


class ExportFormatError(Exception):
    """Raised when an unsupported format is requested."""


def create_export(
    *,
    organization_id: int,
    requested_by_user_id: int,
    format: str,
    period_start: datetime.date,
    period_end: datetime.date,
    db: Session,
    accountant_user_id: Optional[int] = None,
    software_house_id: str = "",
) -> Export:
    """
    Build + upload an export. Always returns an Export row (committed).
    On failure the row's status='failed' and error_message is populated.
    """
    if format not in SUPPORTED_FORMATS:
        raise ExportFormatError(
            f"Unsupported format {format!r}. Supported: {SUPPORTED_FORMATS}"
        )

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if not org:
        raise ValueError(f"organization_id={organization_id} not found")

    # 1. Create the row in 'pending' state
    export = Export(
        organization_id=organization_id,
        requested_by_user_id=requested_by_user_id,
        format=format,
        period_start=period_start,
        period_end=period_end,
        status="pending",
    )
    db.add(export)
    db.commit()
    db.refresh(export)

    export.status = "running"
    export.started_at = datetime.datetime.utcnow()
    db.commit()

    try:
        # 2. Dispatch to the right writer
        if format == "uniform_file":
            file_bytes, summary = build_uniform_file(
                organization_id=organization_id,
                period_start=period_start,
                period_end=period_end,
                db=db,
                software_house_id=software_house_id,
            )
        elif format == "hashavshevet":
            file_bytes, summary = build_hashavshevet_csv(
                organization_id=organization_id,
                period_start=period_start,
                period_end=period_end,
                db=db,
                accountant_user_id=accountant_user_id,
            )
        else:
            raise ExportFormatError(format)

        sha256_hex = hashlib.sha256(file_bytes).hexdigest()

        # 3. Upload to GCS / stub
        period_label = f"{period_start.isoformat()}_{period_end.isoformat()}"
        ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        object_key = (
            f"exports/{organization_id}/{period_start.strftime('%Y-%m')}/"
            f"{format}/{ts}-{sha256_hex[:8]}-{summary['filename']}"
        )
        mime = (
            "application/zip" if format == "uniform_file"
            else "text/csv; charset=" + (summary.get("encoding") or "utf-8")
        )
        gcs_uri = upload_bytes(
            object_key=object_key,
            data=file_bytes,
            mime_type=mime,
        )

        # 4. Mark completed
        export.status = "completed"
        export.completed_at = datetime.datetime.utcnow()
        export.gcs_uri = gcs_uri
        export.file_size_bytes = len(file_bytes)
        export.record_count = int(summary.get("rows", summary.get("records", 0)))
        export.sha256 = sha256_hex

        db.add(ActionLog(
            business_id=org.legacy_business_id,
            status="export.completed",
            detail=(
                f"export_id={export.id} format={format} period={period_label} "
                f"records={export.record_count} bytes={export.file_size_bytes}"
            ),
        ))
        db.commit()
        db.refresh(export)
        return export

    except Exception as e:
        export.status = "failed"
        export.error_message = str(e)[:500]
        export.completed_at = datetime.datetime.utcnow()
        db.add(ActionLog(
            business_id=org.legacy_business_id,
            status="export.failed",
            detail=f"export_id={export.id} format={format} error={str(e)[:200]!r}",
        ))
        db.commit()
        db.refresh(export)
        return export


def get_export(*, export_id: int, db: Session) -> Optional[Export]:
    return db.query(Export).filter(Export.id == export_id).first()


def list_exports(
    *,
    organization_id: int,
    db: Session,
    limit: int = 50,
    offset: int = 0,
) -> list[Export]:
    return (
        db.query(Export)
        .filter(Export.organization_id == organization_id)
        .order_by(Export.created_at.desc())
        .offset(offset)
        .limit(min(max(limit, 1), 200))
        .all()
    )


def export_signed_url(export: Export, ttl_seconds: int = 900) -> Optional[str]:
    """Return a signed URL the accountant can use to download the file."""
    if not export.gcs_uri:
        return None
    # Reverse the gs://bucket/object_key path
    if export.gcs_uri.startswith("gs://"):
        key = export.gcs_uri.split("/", 3)[3]
    elif export.gcs_uri.startswith("file://"):
        # Stub mode — return the file:// URL as-is
        return export.gcs_uri
    else:
        key = export.gcs_uri
    try:
        return signed_url(object_key=key, ttl_seconds=ttl_seconds)
    except Exception:
        return None
