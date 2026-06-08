"""
ASG / Aurora Solutions — KYC Document Service
================================================
Manages the upload + review lifecycle of identity / business-cert
documents during onboarding.

UPLOAD FLOW:
  1. POST /onboarding/documents/init-upload
     → server creates a KycDocument(status='pending_upload')
     → returns: doc_id + pre-signed PUT URL valid 15 minutes
  2. Browser PUTs the bytes directly to GCS (or to the local stub
     endpoint while GCS isn't wired)
  3. POST /onboarding/documents/finalize {doc_id}
     → server verifies the upload, hashes the bytes,
       sets status='pending_review' (manual-review queue)

GCS BACKEND POSTURE:
  - Production: signed URLs against gs://asg-kyc-prod with CMEK
    encryption, 15-min TTL, 7-year retention via lifecycle policy.
  - Stub mode (current): the "pre-signed URL" points to a LOCAL
    PUT endpoint that writes into app/static/kyc_uploads/.
    This lets the founder test the full UX end-to-end before GCS
    integration lands in Sprint 2.

MANUAL REVIEW (FIRST 50 TENANTS):
  Per Aurora spec, the founder reviews the first N onboardings by
  hand to gather ground-truth for later automation. KycDocument rows
  land in status='pending_review' until an admin flips them to
  'approved' or 'rejected' via the admin dashboard.

REQUIRED DOC TYPES BY LEGAL STRUCTURE:
  osek_morshe / osek_patur:  ID front, ID back, business certificate (אישור עוסק)
  chevra_baam:               ID front, ID back, company registry extract (נסח חברה)
"""

import datetime
import hashlib
import os
import pathlib
from typing import Optional

from sqlalchemy.orm import Session

from aurora_shared.database import KycDocument, User, ActionLog


# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────
SIGNED_URL_TTL_SECONDS = 900    # 15 minutes
ACCEPTED_MIME_TYPES = {
    "image/jpeg", "image/png", "image/heic", "image/heif",
    "application/pdf",
}
MAX_BYTES = 10 * 1024 * 1024     # 10 MB hard cap

# Per-legal-structure required doc types — surfaced to the wizard
# so the UI knows which slots to render.
REQUIRED_DOC_TYPES_BY_LEGAL_STRUCTURE = {
    "osek_morshe": ["israeli_id_front", "israeli_id_back", "business_certificate"],
    "osek_patur":  ["israeli_id_front", "israeli_id_back", "business_certificate"],
    "chevra_baam": ["israeli_id_front", "israeli_id_back", "company_registry_extract"],
}


def _default_kyc_stub_dir() -> str:
    """
    Cloud Run is read-only except /tmp. AURORA_RUNTIME=cloud_run flips
    the default to a writable scratch path.
    """
    if os.getenv("AURORA_RUNTIME", "").lower() == "cloud_run":
        return "/tmp/aurora/kyc_uploads"
    return "app/static/kyc_uploads"


# Local-stub upload directory (replaces GCS until Sprint 2 ships GCS).
# Resolved at MODULE LOAD time to honour env overrides.
_LOCAL_KYC_DIR = pathlib.Path(os.getenv("KYC_STUB_DIR") or _default_kyc_stub_dir())


def _kyc_backend() -> str:
    """Read KYC_BACKEND from env (default 'stub')."""
    return (os.getenv("KYC_BACKEND") or "stub").strip().lower()


def _bucket_name() -> str:
    return os.getenv("GCS_BUCKET_KYC", "asg-kyc-prod")


class KycStorageError(Exception):
    """
    Raised when the KYC storage backend (GCS) is reachable-but-failing:
    transport/timeout, permission, or a transient SDK error. The router maps
    this to HTTP 503 ('storage temporarily unavailable, retry') — distinct from
    a ValueError (client/config error → 400).
    """


# ─────────────────────────────────────────────────────────────
# init_document_upload
# ─────────────────────────────────────────────────────────────
def init_document_upload(
    *,
    user_id: int,
    document_type: str,
    mime_type: str,
    bytes_size: int,
    organization_id: Optional[int] = None,
    db: Session,
) -> dict:
    """
    Step 1 of the upload flow. Returns:
        {
          "doc_id": "<uuid>",
          "upload_url": "<signed PUT URL>",
          "upload_method": "PUT",
          "expires_in": 900,
          "headers": {"Content-Type": "<mime>"}
        }

    Raises ValueError on:
      - unknown document_type
      - rejected MIME type
      - bytes_size > MAX_BYTES
      - missing/inactive user
    """
    # Validation
    valid_types = {
        t for types in REQUIRED_DOC_TYPES_BY_LEGAL_STRUCTURE.values() for t in types
    }
    valid_types |= {"vat_certificate", "signature_card"}  # optional extras
    if document_type not in valid_types:
        raise ValueError(f"Unknown document_type: {document_type}")
    if mime_type not in ACCEPTED_MIME_TYPES:
        raise ValueError(
            f"Unsupported mime_type: {mime_type}. "
            f"Accepted: {sorted(ACCEPTED_MIME_TYPES)}"
        )
    if bytes_size <= 0 or bytes_size > MAX_BYTES:
        raise ValueError(
            f"bytes_size must be 1..{MAX_BYTES} bytes (got {bytes_size})"
        )

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()  # noqa: E712
    if not user:
        raise ValueError(f"user_id={user_id} not found or inactive")

    # ── Build the GCS object key ──
    # Path includes user_id and date for tidy organization in the bucket.
    today = datetime.datetime.utcnow().strftime("%Y/%m")
    extension = _ext_from_mime(mime_type)
    object_key = (
        f"u{user_id}/{today}/{document_type}_"
        f"{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}{extension}"
    )

    # ── Build the upload URL FIRST ──
    # Generating the signed URL before any DB write means a signing failure
    # (bad/missing SA key, GCS transport error) does NOT leave an orphaned
    # pending_upload row. ValueError → actionable 400 (config); any GCS SDK /
    # transport error → KycStorageError → 503 (retryable).
    from app.services.gcp.storage import signed_put_url
    try:
        upload_url = signed_put_url(
            bucket=_bucket_name(),
            object_key=object_key,
            mime_type=mime_type,
            max_bytes=bytes_size,
            ttl_seconds=SIGNED_URL_TTL_SECONDS,
        )
    except ValueError:
        raise  # missing/malformed creds, unknown backend → 400
    except Exception as exc:  # noqa: BLE001 — GCS SDK / transport / credential errors
        raise KycStorageError(f"Could not prepare document upload: {exc}") from exc

    # ── Persist the KycDocument row in 'pending_upload' state ──
    doc = KycDocument(
        organization_id=organization_id,
        user_id=user.id,
        document_type=document_type,
        gcs_bucket=_bucket_name(),
        gcs_object_key=object_key,
        mime_type=mime_type,
        bytes_size=bytes_size,
        status="pending_upload",
    )
    db.add(doc)
    db.flush()  # assign doc.id (python-side uuid default) so the audit log isn't doc_id=None

    db.add(ActionLog(
        business_id=None,
        status="kyc_doc.upload_initiated",
        detail=(
            f"doc_id={doc.id} user_id={user.id} type={document_type} "
            f"size={bytes_size} mime={mime_type}"
        ),
    ))
    db.commit()
    db.refresh(doc)

    return {
        "doc_id": doc.id,
        "upload_url": upload_url,
        "upload_method": "PUT",
        "expires_in": SIGNED_URL_TTL_SECONDS,
        "headers": {"Content-Type": mime_type},
    }


# ─────────────────────────────────────────────────────────────
# finalize_document_upload
# ─────────────────────────────────────────────────────────────
def finalize_document_upload(
    *,
    doc_id: str,
    user_id: int,
    db: Session,
) -> KycDocument:
    """
    Step 3 of the upload flow. Verifies the bytes landed, computes
    sha256, and flips status to 'pending_review'.

    Raises ValueError if the doc isn't found, doesn't belong to the
    user, or the bytes are missing.
    """
    doc = db.query(KycDocument).filter(KycDocument.id == doc_id).first()
    if not doc:
        raise ValueError(f"doc_id={doc_id} not found")
    if doc.user_id != user_id:
        raise ValueError("Access denied: this document belongs to another user")

    if doc.status != "pending_upload":
        # Already finalized / approved / rejected — return as-is (idempotent).
        return doc

    # ── Verify the bytes landed and compute sha256 ──
    backend = _kyc_backend()
    if backend == "stub":
        local_path = _LOCAL_KYC_DIR / doc.id
        if not local_path.exists():
            raise ValueError(
                "No bytes received yet at the upload URL. Did the browser PUT succeed?"
            )
        sha = hashlib.sha256()
        actual_size = 0
        with open(local_path, "rb") as fp:
            for chunk in iter(lambda: fp.read(65536), b""):
                sha.update(chunk)
                actual_size += len(chunk)
        doc.sha256 = sha.hexdigest()
        doc.bytes_size = actual_size

    elif backend == "gcs":
        from app.services.gcp.storage import _kyc_credentials

        creds = _kyc_credentials()  # ValueError (missing/malformed key) → 400
        try:
            from google.cloud import storage as gcs_sdk  # lazy import
            client = gcs_sdk.Client(credentials=creds)
            blob = client.bucket(doc.gcs_bucket).blob(doc.gcs_object_key)
            exists = blob.exists()
        except Exception as exc:  # noqa: BLE001 — GCS transport/permission/SDK error → 503, not a raw 500
            raise KycStorageError(f"Could not reach document storage: {exc}") from exc

        if not exists:
            raise ValueError(
                "No bytes received in GCS yet. Did the browser PUT to the signed URL succeed?"
            )

        # Download to compute sha256 server-side (max 10 MB — ~50ms on Cloud Run)
        # SPRINT-9: replace with async Cloud Function trigger on bucket notification
        # to avoid blocking the finalize request on large files.
        try:
            raw = blob.download_as_bytes(timeout=30)
        except Exception as exc:  # noqa: BLE001 — GCS transport/timeout/permission error → 503
            raise KycStorageError(f"Could not read the uploaded document: {exc}") from exc

        # Defense-in-depth: re-enforce the size cap on the actual bytes (the signed
        # URL constrains this at the network layer, but never trust a single guard).
        if len(raw) > MAX_BYTES:
            raise ValueError(f"Uploaded document exceeds the {MAX_BYTES}-byte limit")

        doc.sha256 = hashlib.sha256(raw).hexdigest()
        doc.bytes_size = len(raw)

    else:
        raise ValueError(f"Unknown KYC_BACKEND='{backend}'")

    # ── Flip to pending_review (manual review queue per spec) ──
    doc.status = "pending_review"

    db.add(ActionLog(
        business_id=None,
        status="kyc_doc.uploaded",
        detail=(
            f"doc_id={doc.id} user_id={user_id} type={doc.document_type} "
            f"sha256={doc.sha256[:12]}... size={doc.bytes_size}"
        ),
    ))
    db.commit()
    db.refresh(doc)
    return doc


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────
def _ext_from_mime(mime_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png":  ".png",
        "image/heic": ".heic",
        "image/heif": ".heif",
        "application/pdf": ".pdf",
    }.get(mime_type, "")
