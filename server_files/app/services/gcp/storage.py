"""
Aurora LTS — Cloud Storage Wrapper
====================================
Thin abstraction over Google Cloud Storage. The same code runs against:

  STORAGE_BACKEND='stub' (default)
    - Bytes land in /tmp/aurora/receipts/ (or STORAGE_LOCAL_DIR override)
    - Returns a `file://` URL — usable for local testing only
    - No SDK calls, no GCP cost
    - Object keys still follow the production layout so the FSM and
      tests exercise the same code paths

  STORAGE_BACKEND='gcs'
    - Real google-cloud-storage uploads to gs://{GCS_BUCKET_RECEIPTS}
    - Signed URLs for download (15-min TTL)
    - Lazy SDK import: zero runtime cost when backend is stub

OBJECT KEY SCHEME:
    {organization_id}/{yyyy}/{mm}/{sha256}.{ext}

  This mirrors what the deployment runbook will configure on the
  production bucket lifecycle (archive after 90 days, delete after 7 years).
"""

import datetime
import hashlib
import json
import logging
import os
import pathlib
from typing import Optional

log = logging.getLogger(__name__)

STORAGE_BACKEND = (os.getenv("STORAGE_BACKEND") or "stub").strip().lower()


# ─────────────────────────────────────────────────────────────
# Stub-backend filesystem root
# ─────────────────────────────────────────────────────────────
def _stub_root() -> pathlib.Path:
    """Where stub-backend bytes are persisted on the local filesystem."""
    return pathlib.Path(
        os.getenv("STORAGE_LOCAL_DIR")
        or ("/tmp/aurora/receipts" if os.getenv("AURORA_RUNTIME") == "cloud_run"
            else "app/static/receipts")
    )


def _bucket_name() -> str:
    return os.getenv("GCS_BUCKET_RECEIPTS", "asg-receipts-prod")


# ─────────────────────────────────────────────────────────────
# Public API — sha256_object_key
# ─────────────────────────────────────────────────────────────
def sha256_object_key(
    *,
    organization_id: int,
    sha256_hex: str,
    extension: str,
    when: Optional[datetime.datetime] = None,
) -> str:
    """
    Build the GCS object key for a receipt.

    Pattern: "{org_id}/{yyyy}/{mm}/{sha256}.{ext}"

    Example:
      sha256_object_key(organization_id=42, sha256_hex='abc123…',
                        extension='jpg')
      → '42/2026/04/abc123….jpg'

    Stable across stub + gcs backends so the path is portable.
    """
    if not sha256_hex or len(sha256_hex) < 32:
        raise ValueError("sha256_hex must be a sha256 digest")
    when = when or datetime.datetime.utcnow()
    ext = (extension or "").lstrip(".") or "bin"
    return f"{organization_id}/{when.strftime('%Y')}/{when.strftime('%m')}/{sha256_hex}.{ext}"


def sha256_of(blob: bytes) -> str:
    """Return the hex sha256 of a byte string (utility, used by callers too)."""
    return hashlib.sha256(blob).hexdigest()


# ─────────────────────────────────────────────────────────────
# Public API — upload_bytes
# ─────────────────────────────────────────────────────────────
def upload_bytes(
    *,
    object_key: str,
    data: bytes,
    mime_type: str,
) -> str:
    """
    Upload `data` to GCS at `object_key`. Returns the URI.

    Stub backend: writes to {_stub_root()}/{object_key} and returns
                  a "file://" URL.
    GCS backend:  uploads to gs://{bucket}/{object_key} and returns
                  the gs:// URI.

    Idempotency: re-uploading the same key with the same bytes is
    allowed (stub overwrites; GCS overwrites unless object versioning
    + retention prevents it).
    """
    if not object_key:
        raise ValueError("object_key is required")
    if not data:
        raise ValueError("empty data")

    if STORAGE_BACKEND == "stub":
        return _stub_upload(object_key, data, mime_type)

    if STORAGE_BACKEND == "gcs":
        return _gcs_upload(object_key, data, mime_type)

    raise ValueError(f"Unknown STORAGE_BACKEND='{STORAGE_BACKEND}'")


def _stub_upload(object_key: str, data: bytes, mime_type: str) -> str:
    target = _stub_root() / object_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(f"[STORAGE/stub] wrote {len(data)} bytes → {target}")
    return f"file://{target.resolve()}"


def _gcs_upload(object_key: str, data: bytes, mime_type: str) -> str:
    """Real Google Cloud Storage upload. Lazy SDK import."""
    from google.cloud import storage  # type: ignore

    client = storage.Client()
    bucket = client.bucket(_bucket_name())
    blob = bucket.blob(object_key)
    blob.upload_from_string(data, content_type=mime_type)
    uri = f"gs://{_bucket_name()}/{object_key}"
    print(f"[STORAGE/gcs] uploaded {len(data)} bytes → {uri}")
    return uri


# ─────────────────────────────────────────────────────────────
# Public API — signed_url
# ─────────────────────────────────────────────────────────────
def signed_url(*, object_key: str, ttl_seconds: int = 900) -> str:
    """
    Generate a time-limited URL the browser can hit to download `object_key`.

    Stub backend: returns "file://..." — usable from a local browser only.
    GCS backend:  generates a v4 signed URL with the given TTL.

    Sprint 2 use cases:
      - Receipts API GET /receipts/{id} returns this URL so the dashboard
        / accountant portal can render a thumbnail.
    """
    if STORAGE_BACKEND == "stub":
        # We don't hide stub bytes; the local file path IS the URL.
        path = _stub_root() / object_key
        return f"file://{path.resolve()}"

    if STORAGE_BACKEND == "gcs":
        from google.cloud import storage  # type: ignore
        from datetime import timedelta

        client = storage.Client()
        bucket = client.bucket(_bucket_name())
        blob = bucket.blob(object_key)
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=ttl_seconds),
            method="GET",
        )

    raise ValueError(f"Unknown STORAGE_BACKEND='{STORAGE_BACKEND}'")


# ─────────────────────────────────────────────────────────────
# Public API — exists
# ─────────────────────────────────────────────────────────────
def exists(*, object_key: str) -> bool:
    """
    True iff `object_key` is present in the configured backend.

    Used by the OCR pipeline's dedup check: if a receipt with the same
    sha256 was already uploaded for this org, we skip re-upload + reuse
    the existing Receipt row.
    """
    if STORAGE_BACKEND == "stub":
        return (_stub_root() / object_key).exists()

    if STORAGE_BACKEND == "gcs":
        from google.cloud import storage  # type: ignore

        client = storage.Client()
        bucket = client.bucket(_bucket_name())
        return bucket.blob(object_key).exists()

    raise ValueError(f"Unknown STORAGE_BACKEND='{STORAGE_BACKEND}'")


# ─────────────────────────────────────────────────────────────
# KYC — signed PUT URLs  (separate from receipts signed_url above)
# Uses the KYC service account key (GCS_KYC_SA_KEY_JSON) for signing
# because Cloud Run Workload Identity does not expose a private key,
# which is required for v4 signed URL generation.
# ─────────────────────────────────────────────────────────────

def _kyc_credentials():
    """
    Return google.oauth2.service_account.Credentials for the KYC bucket.

    Reads GCS_KYC_SA_KEY_JSON from env. Accepted formats:
      - A JSON string:  '{"type": "service_account", ...}'
      - A file path:    '/run/secrets/kyc-sa-key.json'

    Raises ValueError clearly if the env var is absent when KYC_BACKEND=gcs,
    so the error message is actionable rather than a cryptic SDK crash.
    """
    raw = (os.getenv("GCS_KYC_SA_KEY_JSON") or "").strip()
    if not raw:
        raise ValueError(
            "GCS_KYC_SA_KEY_JSON is not set. "
            "Provide the KYC service account key JSON (string or file path) "
            "or set KYC_BACKEND=stub for local development."
        )

    # Detect file-path vs. inline JSON
    if raw.startswith("{"):
        key_data = json.loads(raw)
    else:
        with open(raw) as fh:
            key_data = json.load(fh)

    from google.oauth2 import service_account  # lazy import
    return service_account.Credentials.from_service_account_info(
        key_data,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def signed_put_url(
    *,
    bucket: str,
    object_key: str,
    mime_type: str,
    max_bytes: int,
    ttl_seconds: int = 900,
) -> str:
    """
    Generate a v4 signed PUT URL that the browser uses to upload a KYC
    document directly to GCS — FastAPI never proxies the bytes.

    Security constraints encoded into the URL signature:
      - content_type=mime_type: GCS rejects PUTs with a different Content-Type
      - x-goog-content-length-range: GCS rejects uploads outside [1, max_bytes]
        at the TCP layer — a 5 GB upload is closed before a single byte lands

    KYC_BACKEND=stub: returns the local upload-stub URL for dev/test.
    KYC_BACKEND=gcs:  generates a real v4 signed URL using the KYC SA key.
    """
    kyc_backend = (os.getenv("KYC_BACKEND") or "stub").strip().lower()

    if kyc_backend == "stub":
        # In stub mode, the "signed URL" points to the local FastAPI endpoint.
        public_base = os.getenv("ONBOARDING_PUBLIC_URL", "http://localhost:8000/onboarding")
        api_base = public_base.replace("/onboarding", "").rstrip("/")
        return f"{api_base}/api/v1/onboarding/documents/{object_key}/upload-stub"

    if kyc_backend == "gcs":
        import datetime as dt

        # Validate credentials before importing the SDK so errors are always
        # a clear ValueError (not a cryptic ModuleNotFoundError).
        creds = _kyc_credentials()
        from google.cloud import storage as gcs_sdk  # lazy import
        client = gcs_sdk.Client(credentials=creds)
        blob = client.bucket(bucket).blob(object_key)

        url = blob.generate_signed_url(
            version="v4",
            expiration=dt.timedelta(seconds=ttl_seconds),
            method="PUT",
            content_type=mime_type,
            # x-goog-content-length-range causes GCS to reject uploads
            # below 1 byte or above max_bytes at the network level.
            headers={"x-goog-content-length-range": f"1,{max_bytes}"},
            credentials=creds,
        )
        log.info(
            "[kyc/gcs] signed PUT URL generated bucket=%s key=%s ttl=%ds",
            bucket, object_key, ttl_seconds,
        )
        return url

    raise ValueError(f"Unknown KYC_BACKEND='{kyc_backend}'")
