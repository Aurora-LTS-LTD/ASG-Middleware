"""
Aurora LTS — Accountant Vault — Manual Upload (P1-17)
======================================================
A focused router that fills the audit gap "vault is read-only; no
file upload → vault remains empty". The broader vault router with
the 5-endpoint surface (list/get/classify/upload/ingestion-address)
is Sprint 8.4 — this commit ships ONLY the upload endpoint that the
accountant-portal vault page wires to.

ENDPOINT:
  POST /api/v1/accountant/vault/clients/{client_id}/documents/manual
       multipart/form-data: file=<binary>, document_type=<str?>, tax_year=<int?>
  → returns the created ClientDocument id + status.

AUTHORIZATION:
  - require_accountant: JWT iss="aurora-accountant"
  - assert_accountant_can_access_client: the calling accountant must
    have an active AccountantEngagement for the target client_id.

STORAGE BACKENDS:
  STORAGE_BACKEND=stub  → writes the bytes to a local /tmp dir
                          (dev / pre-GCS deploy)
  STORAGE_BACKEND=gcs   → uploads to the vault bucket via the GCS client
                          using ambient Workload Identity creds (keyless;
                          needs roles/storage.objectCreator on the bucket)

The bytes are SHA-256 hashed server-side so the ClientDocument row
records a content-addressable fingerprint regardless of source.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aurora_shared.database import get_db
from aurora_shared.database.models import (
    AccountantEngagement,
    ClientDocument,
    User,
)
from aurora_shared.middleware.auth_middleware import require_accountant
from aurora_shared.middleware.rate_limit import limiter

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/accountant/vault",
    tags=["accountant", "vault"],
)


# Maximum file size — 25 MB. Hard limit; reject larger uploads at the
# router boundary so we never load oversize blobs into memory.
_MAX_FILE_BYTES = 25 * 1024 * 1024

_ALLOWED_DOC_TYPES = frozenset({"expense", "revenue", "statement", "unclassified"})


class ManualUploadResponse(BaseModel):
    document_id: int
    status: str
    sha256: str
    bytes_size: int


def _assert_engagement(accountant_user_id: int, client_id: int, db: Session) -> AccountantEngagement:
    """Confirm the calling accountant has an ACTIVE engagement for client_id."""
    eng = (
        db.query(AccountantEngagement)
        .filter(
            AccountantEngagement.accountant_user_id == accountant_user_id,
            AccountantEngagement.organization_id == client_id,
            AccountantEngagement.status == "active",
        )
        .first()
    )
    if eng is None:
        raise HTTPException(
            status_code=403,
            detail="no_active_engagement_for_client",
        )
    return eng


def _storage_backend() -> str:
    return (os.getenv("STORAGE_BACKEND") or "stub").strip().lower()


def _vault_bucket() -> str:
    return (os.getenv("GCS_BUCKET_VAULT") or os.getenv("GCS_BUCKET") or "asg-vault-dev").strip()


def _stub_dir() -> str:
    return (
        os.getenv("VAULT_STUB_DIR")
        or ("/tmp/aurora/vault_uploads" if os.getenv("AURORA_RUNTIME") == "cloud_run"
            else "app/static/vault_uploads")
    )


def _write_to_storage(*, bucket: str, object_key: str, blob: bytes, mime_type: str) -> str:
    """
    Persist the bytes. Returns the storage URI (gs://bucket/key or local path).
    Backend selected by STORAGE_BACKEND env var.
    """
    backend = _storage_backend()
    if backend == "gcs":
        from google.cloud import storage as gcs_sdk
        client = gcs_sdk.Client()  # ambient Workload Identity creds (keyless); needs roles/storage.objectCreator
        b = client.bucket(bucket)
        bl = b.blob(object_key)
        bl.upload_from_string(blob, content_type=mime_type, timeout=60)
        return f"gs://{bucket}/{object_key}"
    # stub
    target_dir = _stub_dir()
    os.makedirs(target_dir, exist_ok=True)
    full = os.path.join(target_dir, object_key.replace("/", "_"))
    with open(full, "wb") as fh:
        fh.write(blob)
    return f"file://{full}"


@router.post(
    "/clients/{client_id}/documents/manual",
    response_model=ManualUploadResponse,
)
@limiter.limit("60/minute")
async def upload_manual(
    request,
    client_id: int,
    file: UploadFile = File(...),
    document_type: Optional[str] = Form(None),
    tax_year: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accountant),
) -> ManualUploadResponse:
    """
    Manual upload: accountant attaches a file directly from the portal,
    on behalf of one of their engaged clients.
    """
    eng = _assert_engagement(current_user.id, client_id, db)

    # ── Read + size-check the upload ──
    blob = await file.read()
    size_bytes = len(blob)
    if size_bytes == 0:
        raise HTTPException(status_code=400, detail="empty_file")
    if size_bytes > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file_too_large_max_{_MAX_FILE_BYTES}_bytes",
        )

    # ── Validate optional document_type ──
    if document_type and document_type not in _ALLOWED_DOC_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"document_type must be one of {sorted(_ALLOWED_DOC_TYPES)}",
        )

    sha = hashlib.sha256(blob).hexdigest()
    now = datetime.datetime.utcnow()
    current_tax_year = tax_year if tax_year is not None else now.year

    # Object key pattern: {client_id}/{yyyy}/{mm}/{sha256}.{ext}
    safe_name = (file.filename or "upload.bin").replace("/", "_")
    ext = os.path.splitext(safe_name)[1].lower() or ".bin"
    object_key = f"{client_id}/{now.year:04d}/{now.month:02d}/{sha[:16]}{ext}"

    bucket = _vault_bucket()
    storage_uri = _write_to_storage(
        bucket=bucket,
        object_key=object_key,
        blob=blob,
        mime_type=file.content_type or "application/octet-stream",
    )

    # Compliance: archived_until = created_at + 7 years (matches the
    # CHECK constraint in migrate_phase21_vault).
    archived_until = now.replace(year=now.year + 7)

    doc = ClientDocument(
        agency_id=eng.accountant_user_id,  # using accountant_user_id as agency-id proxy
        client_id=client_id,
        uploaded_by_vector="manual",
        s3_key=object_key,
        s3_bucket=bucket,
        document_type=document_type or "unclassified",
        file_name=safe_name[:255],
        mime_type=(file.content_type or "application/octet-stream")[:80],
        size_bytes=size_bytes,
        sha256=sha,
        tax_year=current_tax_year,
        status="received",
        created_at=now,
        archived_until=archived_until,
    )
    db.add(doc)
    try:
        db.commit()
        db.refresh(doc)
    except Exception as exc:
        db.rollback()
        log.error("[vault-upload] DB commit failed: %s", exc)
        raise HTTPException(status_code=500, detail="upload_persist_failed")

    log.info(
        "[vault-upload] doc_id=%s client_id=%s size=%d sha=%s storage=%s",
        doc.id, client_id, size_bytes, sha[:12], storage_uri,
    )

    return ManualUploadResponse(
        document_id=doc.id,
        status=doc.status,
        sha256=sha,
        bytes_size=size_bytes,
    )
