"""
Aurora LTS — Israel Tax Authority (רשות המיסים) Client Package
================================================================
Sprint 3 — production-ready ITA client behind a feature flag.

The legacy mock at `app/services/ita_mock_service.py` remains UNTOUCHED
so we can flip back to it instantly via `ITA_BACKEND=mock`. This package
provides the production path:

  ITA_BACKEND='mock' (default — dev + GCP-without-ITA-creds)
    → falls through to ita_mock_service.request_allocation_number

  ITA_BACKEND='production' (live ITA API)
    → JWT-signed POST against the published Allocation Number endpoint
    → idempotency via X-Request-Id (= "{invoice_id}:{retry_count}")
    → respects Retry-After on 429
    → every call writes a sanitised row to `ita_audit_log`

The public function preserves the EXACT signature of ita_mock_service:
  async request_allocation_number(seller_tax_id, buyer_tax_id, amount,
                                   invoice_date) -> dict
This means allocation_queue.py only swaps the import — no code change.

PUBLIC RE-EXPORTS:
    from app.services.ita import request_allocation_number, ITA_BACKEND
"""

from app.services.ita.client import (
    request_allocation_number,
    ITA_BACKEND,
    ITA_API_BASE,
    ITAClientError,
)

__all__ = [
    "request_allocation_number",
    "ITA_BACKEND",
    "ITA_API_BASE",
    "ITAClientError",
]
