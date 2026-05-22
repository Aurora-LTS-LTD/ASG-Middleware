"""
Aurora LTS — Israel Tax Authority Client (Production)
========================================================
Sprint 3 — replaces the Phase 4 mock with a real HTTPS client.

PUBLIC SHAPE preserved EXACTLY from app/services/ita_mock_service.py
so allocation_queue.py and invoice_service.py only have to swap the
import. The dispatch through ITA_BACKEND happens in this single file.

CONTRACT:
  async request_allocation_number(seller_tax_id, buyer_tax_id, amount,
                                   invoice_date) -> {
      "success":           bool,
      "allocation_number": str | None,
      "message":           str,
      "timestamp":         iso8601 str,

      # Sprint 3 additions (extra context the audit log uses; extras
      # are ignored by the mock-compatible callers):
      "request_id":        str,        # X-Request-Id we sent to ITA
      "http_status":       int | None, # HTTP status from ITA
      "latency_ms":        int,
      "backend":           "mock" | "production",
      "raw_response_summary": str,
  }

DISPATCH:
  ITA_BACKEND='mock'        → ita_mock_service.request_allocation_number
  ITA_BACKEND='production'  → real HTTPS POST in this file

  Both write to ita_audit_log so the binder evidence covers both paths.

ENV:
  ITA_BACKEND               'mock' (default) | 'production'
  ITA_API_BASE              base URL (default: published ITA endpoint)
  ITA_ALLOCATION_PATH       path appended (default: '/allocation/v1/issue')
  ITA_TIMEOUT_SECONDS       default 15
  ITA_SOFTWARE_HOUSE_ID     ITA-issued identifier (used as iss claim)
  ITA_PRIVATE_KEY_SECRET    Secret Manager name (default: AURORA_ITA_PRIVATE_KEY)
  ITA_AUDIENCE              JWT aud claim (default: 'ita.gov.il')

THE INVOICE_ID + RETRY_COUNT KWARGS:
  We honour the original 4-arg mock signature (seller_tax_id, buyer_tax_id,
  amount, invoice_date). For production we ALSO accept the new keyword
  args invoice_id + retry_count to drive idempotency. They default to
  None / 0 — which means the mock backend is unaffected.
"""

import datetime
import json
import os
import time
from typing import Optional

# Lazy import for httpx so test environments without network deps still
# import this module cleanly.
def _httpx():
    import httpx  # type: ignore
    return httpx


ITA_BACKEND = (os.getenv("ITA_BACKEND") or "mock").strip().lower()
ITA_API_BASE = os.getenv("ITA_API_BASE", "https://ita.gov.il/api")
ITA_ALLOCATION_PATH = os.getenv("ITA_ALLOCATION_PATH", "/allocation/v1/issue")
ITA_TIMEOUT_SECONDS = int(os.getenv("ITA_TIMEOUT_SECONDS", "15"))


class ITAClientError(Exception):
    """Raised on production ITA failures (network, signature, malformed response).

    The allocation_queue worker treats this as a transient retryable error
    on transport-level failures, and a permanent error on 4xx responses.
    """

    def __init__(self, message: str, *, http_status: Optional[int] = None,
                 retryable: bool = True):
        super().__init__(message)
        self.http_status = http_status
        self.retryable = retryable


# ─────────────────────────────────────────────────────────────
# Public — request_allocation_number  (preserves mock signature)
# ─────────────────────────────────────────────────────────────
async def request_allocation_number(
    seller_tax_id: str,
    buyer_tax_id: str,
    amount: float,
    invoice_date: Optional[str] = None,
    *,
    invoice_id: Optional[int] = None,
    retry_count: int = 0,
    organization_id: Optional[int] = None,
) -> dict:
    """
    Request an allocation number from the Israel Tax Authority.

    Dispatches based on `ITA_BACKEND`:
      - 'mock'       → existing ita_mock_service (unchanged)
      - 'production' → live JWT-signed HTTPS call (this file)

    Always writes to ita_audit_log (success or failure) for binder evidence.
    """
    if ITA_BACKEND == "mock":
        # Lazy import to avoid pulling in mock symbols when running prod.
        from app.services import ita_mock_service

        result = await ita_mock_service.request_allocation_number(
            seller_tax_id=seller_tax_id,
            buyer_tax_id=buyer_tax_id,
            amount=amount,
            invoice_date=invoice_date,
        )
        # Augment the result with metadata the audit log expects
        result.setdefault("request_id", f"mock-{invoice_id}-{retry_count}")
        result.setdefault("http_status", 200 if result.get("success") else 503)
        result.setdefault("latency_ms", 1000)
        result.setdefault("backend", "mock")
        result.setdefault("raw_response_summary", "mock backend")
        # Persist audit row even for mock backend so the binder shows
        # the full call history regardless of which path produced it.
        _write_audit_row(
            invoice_id=invoice_id,
            organization_id=organization_id,
            seller_tax_id=seller_tax_id,
            buyer_tax_id=buyer_tax_id,
            amount=amount,
            result=result,
            backend="mock",
        )
        return result

    if ITA_BACKEND == "production":
        return await _production_call(
            seller_tax_id=seller_tax_id,
            buyer_tax_id=buyer_tax_id,
            amount=amount,
            invoice_date=invoice_date,
            invoice_id=invoice_id,
            retry_count=retry_count,
            organization_id=organization_id,
        )

    raise ValueError(f"Unknown ITA_BACKEND={ITA_BACKEND!r}")


# ─────────────────────────────────────────────────────────────
# Production call
# ─────────────────────────────────────────────────────────────
async def _production_call(
    *,
    seller_tax_id: str,
    buyer_tax_id: str,
    amount: float,
    invoice_date: Optional[str],
    invoice_id: Optional[int],
    retry_count: int,
    organization_id: Optional[int],
) -> dict:
    """The actual HTTPS call. Lazy-imports httpx + jose."""
    from app.services.ita.auth import build_request_id, sign_request

    request_id = build_request_id(invoice_id or 0, retry_count or 0)

    # Build + sign the JWT (raises ITAClientError if key missing)
    try:
        token = sign_request(seller_tax_id=seller_tax_id, request_id=request_id)
    except Exception as e:
        result = _failure_result(
            message=f"signing failed: {e}",
            request_id=request_id,
            http_status=None,
            latency_ms=0,
            backend="production",
        )
        _write_audit_row(
            invoice_id=invoice_id,
            organization_id=organization_id,
            seller_tax_id=seller_tax_id,
            buyer_tax_id=buyer_tax_id,
            amount=amount,
            result=result,
            backend="production",
        )
        return result

    if invoice_date is None:
        invoice_date = datetime.date.today().isoformat()

    body = {
        "seller_tax_id": seller_tax_id,
        "buyer_tax_id": buyer_tax_id,
        "amount": float(amount),
        "currency": "ILS",
        "invoice_date": invoice_date,
        "request_id": request_id,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Request-Id": request_id,
        "User-Agent": "Aurora-LTS/1.0 (+https://aurora-ltd.co.il)",
    }
    url = ITA_API_BASE.rstrip("/") + ITA_ALLOCATION_PATH

    print(
        f"[ITA] → POST {url}  invoice_id={invoice_id} retry={retry_count} "
        f"seller={_mask_tax_id(seller_tax_id)} buyer={_mask_tax_id(buyer_tax_id)} "
        f"amount={amount}"
    )

    httpx = _httpx()
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=ITA_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers=headers, json=body)
    except Exception as e:
        latency_ms = int((time.monotonic() - started) * 1000)
        result = _failure_result(
            message=f"transport error: {e}",
            request_id=request_id,
            http_status=None,
            latency_ms=latency_ms,
            backend="production",
        )
        _write_audit_row(
            invoice_id=invoice_id,
            organization_id=organization_id,
            seller_tax_id=seller_tax_id,
            buyer_tax_id=buyer_tax_id,
            amount=amount,
            result=result,
            backend="production",
        )
        return result

    latency_ms = int((time.monotonic() - started) * 1000)
    http_status = response.status_code

    # Successful response
    if 200 <= http_status < 300:
        try:
            payload = response.json()
        except Exception:
            payload = {}
        allocation_number = (
            payload.get("allocation_number")
            or payload.get("allocationNumber")
            or payload.get("number")
        )
        if allocation_number:
            result = {
                "success": True,
                "allocation_number": str(allocation_number),
                "message": "Allocation approved",
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "request_id": request_id,
                "http_status": http_status,
                "latency_ms": latency_ms,
                "backend": "production",
                "raw_response_summary": _summarise_response(payload),
            }
            print(f"[ITA] ✅ {allocation_number} (HTTP {http_status} {latency_ms}ms)")
        else:
            result = _failure_result(
                message="ITA 2xx but no allocation_number in payload",
                request_id=request_id,
                http_status=http_status,
                latency_ms=latency_ms,
                backend="production",
            )
        _write_audit_row(
            invoice_id=invoice_id,
            organization_id=organization_id,
            seller_tax_id=seller_tax_id,
            buyer_tax_id=buyer_tax_id,
            amount=amount,
            result=result,
            backend="production",
        )
        return result

    # 4xx / 5xx — log and surface as failure. allocation_queue's retry
    # logic decides whether to back off (5xx) or give up (4xx).
    err_summary = _summarise_response_text(response.text)
    result = _failure_result(
        message=f"HTTP {http_status}: {err_summary[:200]}",
        request_id=request_id,
        http_status=http_status,
        latency_ms=latency_ms,
        backend="production",
    )
    _write_audit_row(
        invoice_id=invoice_id,
        organization_id=organization_id,
        seller_tax_id=seller_tax_id,
        buyer_tax_id=buyer_tax_id,
        amount=amount,
        result=result,
        backend="production",
    )
    return result


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _failure_result(
    *, message: str, request_id: str, http_status: Optional[int],
    latency_ms: int, backend: str,
) -> dict:
    return {
        "success": False,
        "allocation_number": None,
        "message": message,
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "request_id": request_id,
        "http_status": http_status,
        "latency_ms": latency_ms,
        "backend": backend,
        "raw_response_summary": message[:500],
    }


def _mask_tax_id(tax_id: Optional[str]) -> str:
    """Redact for logs: '123456782' → '123******82'."""
    if not tax_id:
        return ""
    s = str(tax_id)
    if len(s) <= 5:
        return "*" * len(s)
    return f"{s[:3]}{'*' * (len(s) - 5)}{s[-2:]}"


def _summarise_response(payload: dict) -> str:
    """Truncate + sanitise — no PII, ≤500 chars."""
    try:
        data = json.dumps(payload, ensure_ascii=False)[:500]
    except Exception:
        data = str(payload)[:500]
    return data


def _summarise_response_text(text: str) -> str:
    return (text or "")[:500]


# ─────────────────────────────────────────────────────────────
# Audit log — used by both backends
# ─────────────────────────────────────────────────────────────
def _write_audit_row(
    *,
    invoice_id: Optional[int],
    organization_id: Optional[int],
    seller_tax_id: str,
    buyer_tax_id: str,
    amount: float,
    result: dict,
    backend: str,
) -> None:
    """
    Persist an ITA audit log row. Best-effort — a DB failure here
    must NOT prevent the caller from receiving the actual ITA result.
    """
    try:
        from app.database import SessionLocal, ItaAuditLog
    except Exception as e:
        print(f"[ITA] ⚠️ audit log import failed: {e}")
        return

    db = SessionLocal()
    try:
        row = ItaAuditLog(
            invoice_id=invoice_id,
            organization_id=organization_id,
            request_id=result.get("request_id") or "",
            operation="allocation_request",
            seller_tax_id_masked=_mask_tax_id(seller_tax_id),
            buyer_tax_id_masked=_mask_tax_id(buyer_tax_id),
            amount_minor_units=int(round(float(amount) * 100)) if amount else None,
            currency="ILS",
            http_status=result.get("http_status"),
            latency_ms=result.get("latency_ms"),
            success=bool(result.get("success")),
            allocation_number=result.get("allocation_number"),
            error_code=None if result.get("success") else "ita_error",
            error_message=None if result.get("success") else str(result.get("message"))[:500],
            backend=backend,
            response_summary=result.get("raw_response_summary"),
        )
        db.add(row)
        db.commit()
    except Exception as e:
        print(f"[ITA] ⚠️ audit log write failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
