"""
Aurora LTS — GCP Service Wrappers
==================================
Sprint 2 (Document AI Receipt Pipeline) — first time the codebase
talks to Google Cloud. Each wrapper here exposes a clean Aurora-shaped
API and quietly switches between a STUB backend (default — works on
any laptop, zero GCP cost) and a PRODUCTION backend (real GCP calls).

Backend selection lives in env:
  STORAGE_BACKEND     'stub' (default) | 'gcs'
  OCR_BACKEND         'stub' (default) | 'documentai'
  DLP_BACKEND         'stub' (default) | 'gcp'

Public re-exports for ergonomic imports elsewhere:
    from app.services.gcp import (
        upload_bytes, signed_url, exists, sha256_object_key,
        parse_expense, ExpenseParseResult,
        scan_image, DlpScanResult,
    )

WHY THE BACKEND DEFAULTS ARE STUB:
  - Lets the founder boot, code, and test locally with zero GCP setup
  - The container image carries the Google SDK in case prod backends
    are flipped on, but stub mode never imports the SDK
  - When GCP infra (bucket, processor, DLP API) is provisioned per the
    deployment runbook, flip the env flag — no code redeploy needed.
"""

from app.services.gcp.storage import (
    upload_bytes,
    signed_url,
    exists,
    sha256_object_key,
    STORAGE_BACKEND,
)
from app.services.gcp.document_ai import (
    parse_expense,
    ExpenseParseResult,
    OCR_BACKEND,
)
from app.services.gcp.dlp import (
    scan_image,
    DlpScanResult,
    DLP_BACKEND,
)
from app.services.gcp.secrets import (
    get_secret,
    invalidate_secret,
    invalidate_all,
    SECRET_BACKEND,
)

__all__ = [
    "upload_bytes", "signed_url", "exists", "sha256_object_key",
    "parse_expense", "ExpenseParseResult",
    "scan_image", "DlpScanResult",
    "get_secret", "invalidate_secret", "invalidate_all",
    "STORAGE_BACKEND", "OCR_BACKEND", "DLP_BACKEND", "SECRET_BACKEND",
]
