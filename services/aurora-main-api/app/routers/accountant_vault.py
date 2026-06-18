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
import secrets
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aurora_shared.database import get_db
from aurora_shared.database.models import (
    AccountantEngagement,
    ClientDocument,
    User,
    VaultIngestionAddress,
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


# Ingestion address email: docs+<token>@<domain>. No inbound parser is wired
# yet, so the format is configurable here and surfaced to the portal for display.
_INGEST_EMAIL_LOCALPART = (os.getenv("VAULT_INGEST_EMAIL_LOCALPART") or "docs").strip()
_INGEST_EMAIL_DOMAIN = (os.getenv("VAULT_INGEST_EMAIL_DOMAIN") or "api-aurora-lts.com").strip()


def _serialize_doc(doc: ClientDocument) -> dict:
    """ClientDocument → the shape the accountant-portal expects (types/vault.ts)."""
    return {
        "id": doc.id,
        "agency_id": doc.agency_id,
        "client_id": doc.client_id,
        "uploaded_by_vector": doc.uploaded_by_vector,
        "s3_key": doc.s3_key,
        "document_type": doc.document_type,
        "file_name": doc.file_name,
        "mime_type": doc.mime_type,
        "size_bytes": doc.size_bytes,
        "sha256": doc.sha256,
        "sender_phone_e164": doc.sender_phone_e164,
        "sender_email": doc.sender_email,
        "extracted_metadata": doc.extracted_metadata,
        "tax_year": doc.tax_year,
        "status": doc.status,
        "error_reason": doc.error_reason,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "archived_until": doc.archived_until.isoformat() if doc.archived_until else None,
    }


def _download_url(doc: ClientDocument) -> str:
    """Time-limited download URL for a stored document (keyless v4 signed GET on GCS)."""
    if _storage_backend() == "gcs":
        from app.services.gcp.storage import signed_get_url
        return signed_get_url(bucket=doc.s3_bucket, object_key=doc.s3_key)
    # stub: the upload wrote to _stub_dir() with "/" flattened to "_"
    return f"file://{os.path.join(_stub_dir(), doc.s3_key.replace('/', '_'))}"


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


# ─────────────────────────────────────────────────────────────
# Sprint 8.4 — vault read surface (list / get+download / ingestion / reclassify)
# ─────────────────────────────────────────────────────────────
@router.get("/clients/{client_id}/documents")
@limiter.limit("120/minute")
async def list_documents(
    request: Request,
    client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accountant),
    tax_year: Optional[int] = Query(None),
    document_type: Optional[str] = Query(None),
    uploaded_by_vector: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List a client's vault documents (most-recent first) with optional filters + pagination."""
    _assert_engagement(current_user.id, client_id, db)
    q = db.query(ClientDocument).filter(
        ClientDocument.client_id == client_id,
        ClientDocument.deleted_at.is_(None),
    )
    if tax_year is not None:
        q = q.filter(ClientDocument.tax_year == tax_year)
    if document_type:
        q = q.filter(ClientDocument.document_type == document_type)
    if uploaded_by_vector:
        q = q.filter(ClientDocument.uploaded_by_vector == uploaded_by_vector)
    if status:
        q = q.filter(ClientDocument.status == status)
    total = q.count()
    rows = (
        q.order_by(ClientDocument.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "documents": [_serialize_doc(d) for d in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/documents/{document_id}")
@limiter.limit("120/minute")
async def get_document(
    request: Request,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accountant),
):
    """Fetch a single vault document + a time-limited download URL."""
    doc = (
        db.query(ClientDocument)
        .filter(ClientDocument.id == document_id, ClientDocument.deleted_at.is_(None))
        .first()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    _assert_engagement(current_user.id, doc.client_id, db)
    return {"document": _serialize_doc(doc), "download_url": _download_url(doc)}


@router.get("/clients/{client_id}/ingestion-address")
@limiter.limit("60/minute")
async def get_ingestion_address(
    request: Request,
    client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accountant),
):
    """Get the client's email/WhatsApp ingestion address (provisioned on first call)."""
    _assert_engagement(current_user.id, client_id, db)
    addr = (
        db.query(VaultIngestionAddress)
        .filter(VaultIngestionAddress.client_id == client_id)
        .first()
    )
    if addr is None:
        addr = VaultIngestionAddress(
            client_id=client_id,
            email_alias_token=secrets.token_hex(8),
            active=True,
        )
        db.add(addr)
        try:
            db.commit()
            db.refresh(addr)
        except IntegrityError:
            # client_id is unique — a concurrent call won; reuse the winner's row.
            db.rollback()
            addr = (
                db.query(VaultIngestionAddress)
                .filter(VaultIngestionAddress.client_id == client_id)
                .first()
            )
    return {
        "ingestion_address": {
            "client_id": addr.client_id,
            "email_alias_token": addr.email_alias_token,
            "whatsapp_e164": addr.whatsapp_e164,
            "active": addr.active,
        },
        "email_full": f"{_INGEST_EMAIL_LOCALPART}+{addr.email_alias_token}@{_INGEST_EMAIL_DOMAIN}",
        "whatsapp_display": addr.whatsapp_e164,
    }


class ReclassifyRequest(BaseModel):
    document_type: Optional[str] = None
    tax_year: Optional[int] = None


@router.post("/documents/{document_id}/reclassify")
@limiter.limit("60/minute")
async def reclassify_document(
    request: Request,
    document_id: int,
    body: ReclassifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accountant),
):
    """Override a document's type / tax year after (mis)classification."""
    if body.document_type is None and body.tax_year is None:
        raise HTTPException(status_code=400, detail="nothing_to_update")
    if body.document_type is not None and body.document_type not in _ALLOWED_DOC_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"document_type must be one of {sorted(_ALLOWED_DOC_TYPES)}",
        )
    doc = (
        db.query(ClientDocument)
        .filter(ClientDocument.id == document_id, ClientDocument.deleted_at.is_(None))
        .first()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    _assert_engagement(current_user.id, doc.client_id, db)
    if body.document_type is not None:
        doc.document_type = body.document_type
        doc.status = "classified"
    if body.tax_year is not None:
        doc.tax_year = body.tax_year
    try:
        db.commit()
        db.refresh(doc)
    except Exception as exc:
        db.rollback()
        log.error("[vault-reclassify] DB commit failed: %s", exc)
        raise HTTPException(status_code=500, detail="reclassify_failed")
    return {"ok": True, "document": _serialize_doc(doc)}
