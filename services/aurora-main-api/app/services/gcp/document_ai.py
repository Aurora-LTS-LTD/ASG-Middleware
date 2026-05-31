"""
Aurora LTS — Document AI Wrapper (Expense Parser)
====================================================
Sprint 2 — turns the bytes of a receipt photo / PDF into structured
fields (supplier, total, VAT, date, etc.) plus per-field confidence.

Two backends behind one shape:

  OCR_BACKEND='stub' (default)
    - Returns deterministic synthetic results so the FSM / pipeline /
      tests work end-to-end with zero GCP cost
    - Honours a few magic-marker tokens in the input bytes so tests
      can drive low-confidence / failure / specific-amount branches
      without touching the wrapper code:
        b"FORCE_LOW_CONFIDENCE"  → confidence < 0.6 across the board
        b"FORCE_MID_CONFIDENCE"  → confidence in [0.6, 0.85)
        b"FORCE_OCR_FAILURE"     → raises OcrError
        b"FORCE_AMOUNT=123.45"   → exact total (still high confidence)
    - Synthesised values: supplier "אורורה Stub Supplier",
      currency ILS, today's date, total = ₪50–500 random

  OCR_BACKEND='documentai'
    - Real google-cloud-documentai call against the Expense Parser
      processor identified by DOCUMENT_AI_LOCATION + DOCUMENT_AI_EXPENSE_PROCESSOR_ID
    - Lazy SDK import — never loaded in stub mode
    - Maps the Document AI Expense Parser entity model to our
      ExpenseParseResult dataclass

THE EXPENSE PARSER ENTITIES WE EXTRACT:
  supplier_name          → ExpenseParseResult.supplier_name
  supplier_tax_id        → .supplier_tax_id    (Israeli ע.מ. / ח.פ.)
  total_amount           → .total_amount_minor_units (₪ in agorot)
  total_tax_amount       → .vat_amount_minor_units
  receipt_date           → .receipt_date (date)
  currency               → .currency

If a field is missing or unparseable, we leave it None — we don't
synthesize a value. Confidence is the LOWEST per-field confidence
across the critical fields (supplier, total, date), which drives the
routing decision in services/receipts/confidence.py.
"""

import datetime
import os
import random
import re
from dataclasses import dataclass, field
from typing import Optional


OCR_BACKEND = (os.getenv("OCR_BACKEND") or "stub").strip().lower()


# ─────────────────────────────────────────────────────────────
# Data shape — caller-friendly result
# ─────────────────────────────────────────────────────────────
@dataclass
class ExpenseParseResult:
    """
    Structured output of an OCR run on a receipt. All fields are
    optional — Document AI may fail to detect any of them.
    """
    supplier_name: Optional[str] = None
    supplier_tax_id: Optional[str] = None
    total_amount_minor_units: Optional[int] = None  # agorot
    vat_amount_minor_units: Optional[int] = None
    currency: str = "ILS"
    receipt_date: Optional[datetime.date] = None

    # Per-field confidence (0.0–1.0). Only populated for fields we got.
    field_confidences: dict = field(default_factory=dict)

    # Audit trail
    raw_response_json: Optional[str] = None  # JSON dump of the raw response
    document_ai_job_id: Optional[str] = None  # operation/job id (production)
    backend: str = "stub"

    @property
    def confidence_min(self) -> Optional[float]:
        """
        Lowest per-field confidence across CRITICAL fields. Used to
        drive the routing decision in services/receipts/confidence.py.

        Critical = supplier_name, total_amount, receipt_date. If a
        critical field is missing, confidence_min = 0.0 (forces heavy
        review). If field_confidences is empty (failed parse), returns
        None.
        """
        if not self.field_confidences:
            return None
        critical = ["supplier_name", "total_amount", "receipt_date"]
        worst = 1.0
        seen_any = False
        for k in critical:
            conf = self.field_confidences.get(k)
            if conf is None:
                # Missing critical field → force heavy review
                return 0.0
            seen_any = True
            if conf < worst:
                worst = conf
        return worst if seen_any else None


class OcrError(Exception):
    """Raised when OCR fails irrecoverably."""


# ─────────────────────────────────────────────────────────────
# Public API — parse_expense
# ─────────────────────────────────────────────────────────────
def parse_expense(
    *,
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    gcs_uri: Optional[str] = None,
    language_hint: str = "auto",
) -> ExpenseParseResult:
    """
    Run OCR on `image_bytes` (preferred) or `gcs_uri` (production-only).

    The pipeline almost always passes BOTH — we hash the bytes for dedup
    + store at gcs_uri, then parse. The Document AI client can take
    either inline content or a GCS URI.

    Stub backend ignores gcs_uri and works directly off the bytes (so
    tests don't need a configured bucket).

    P2-03 — language_hint:
      "he"   → route to DOCUMENT_AI_HEBREW_PROCESSOR_ID if configured;
               else fall back to the default expense processor.
      "en"   → use DOCUMENT_AI_EXPENSE_PROCESSOR_ID (the original
               Expense Parser, multi-language but English-default).
      "auto" → same as "en" today; in the future we may sniff the
               first 4 KB for Hebrew Unicode (U+0590–U+05FF) and
               route automatically.

      The Hebrew-tuned processor needs to be trained by hand in the
      Document AI Workbench — upload 30–50 sample Hebrew invoices,
      label supplier_name / total_amount / receipt_date / vat fields,
      train, deploy. See docs/document-ai-hebrew-training.md for the
      step-by-step (operator work, out of this codebase's scope).
    """
    if not image_bytes and not gcs_uri:
        raise OcrError("parse_expense requires image_bytes or gcs_uri")

    if OCR_BACKEND == "stub":
        return _stub_parse(image_bytes or b"", mime_type)

    if OCR_BACKEND == "documentai":
        return _documentai_parse(image_bytes, mime_type, gcs_uri, language_hint)

    raise ValueError(f"Unknown OCR_BACKEND='{OCR_BACKEND}'")


def _select_processor_id(language_hint: str) -> tuple[str, str]:
    """
    Decide which Document AI processor to call.
    Returns (processor_id, processor_kind) where kind ∈ {"hebrew", "default"}.
    """
    default_id = (os.getenv("DOCUMENT_AI_EXPENSE_PROCESSOR_ID") or "").strip()
    hebrew_id = (os.getenv("DOCUMENT_AI_HEBREW_PROCESSOR_ID") or "").strip()

    if language_hint == "he" and hebrew_id:
        return hebrew_id, "hebrew"
    if not default_id:
        # Last-resort: if the Hebrew processor is set and the default
        # isn't, use Hebrew. Avoids hard-fail when only one is configured.
        if hebrew_id:
            return hebrew_id, "hebrew"
        raise OcrError(
            "Neither DOCUMENT_AI_EXPENSE_PROCESSOR_ID nor "
            "DOCUMENT_AI_HEBREW_PROCESSOR_ID is set"
        )
    return default_id, "default"


# ─────────────────────────────────────────────────────────────
# Stub backend — deterministic, magic-token-aware
# ─────────────────────────────────────────────────────────────
def _stub_parse(image_bytes: bytes, mime_type: str) -> ExpenseParseResult:
    """
    Return a synthetic ExpenseParseResult. Honours these magic markers
    in the input bytes (UTF-8 decoded if possible) for test control:
      FORCE_OCR_FAILURE        → raises OcrError
      FORCE_LOW_CONFIDENCE     → all confidences set to 0.40
      FORCE_MID_CONFIDENCE     → all confidences set to 0.70
      FORCE_AMOUNT=<float>     → exact total (still high confidence)
      FORCE_NO_SUPPLIER        → drops supplier_name (forces heavy review
                                 via confidence_min=0.0 path)
    """
    try:
        snippet = image_bytes[:4096].decode("utf-8", errors="ignore")
    except Exception:
        snippet = ""

    if "FORCE_OCR_FAILURE" in snippet:
        raise OcrError("Stub-forced OCR failure")

    # Confidence preset
    if "FORCE_LOW_CONFIDENCE" in snippet:
        conf = 0.40
    elif "FORCE_MID_CONFIDENCE" in snippet:
        conf = 0.70
    else:
        conf = 0.92

    # Total amount — random unless forced
    forced_amount_match = re.search(r"FORCE_AMOUNT=(\d+(?:\.\d+)?)", snippet)
    if forced_amount_match:
        total_minor = int(round(float(forced_amount_match.group(1)) * 100))
    else:
        # Stable-ish randomness via length-of-bytes seed so the same image
        # → the same stub total (handy for dedup tests)
        rng = random.Random(len(image_bytes))
        total_minor = rng.randint(5_000, 50_000)  # ₪50 – ₪500
    vat_minor = int(round(total_minor * 0.18 / 1.18))  # back-out 18% VAT

    # Supplier — typically present, occasionally suppressed
    supplier_name = None if "FORCE_NO_SUPPLIER" in snippet else "Aurora Stub Supplier"

    field_confidences = {
        "supplier_name": conf if supplier_name else None,
        "supplier_tax_id": conf,
        "total_amount": conf,
        "total_tax_amount": conf,
        "receipt_date": conf,
        "currency": 1.0,  # we always know the currency
    }
    # Strip None values — the dataclass treats absence as "not detected"
    field_confidences = {k: v for k, v in field_confidences.items() if v is not None}

    return ExpenseParseResult(
        supplier_name=supplier_name,
        supplier_tax_id="123456782" if conf >= 0.6 else None,  # valid checksum
        total_amount_minor_units=total_minor,
        vat_amount_minor_units=vat_minor,
        currency="ILS",
        receipt_date=datetime.date.today() if conf >= 0.6 else None,
        field_confidences=field_confidences,
        raw_response_json='{"backend":"stub","note":"synthetic"}',
        document_ai_job_id=None,
        backend="stub",
    )


# ─────────────────────────────────────────────────────────────
# Production backend — real Document AI Expense Parser
# ─────────────────────────────────────────────────────────────
def _documentai_parse(
    image_bytes: Optional[bytes],
    mime_type: str,
    gcs_uri: Optional[str],
    language_hint: str = "auto",
) -> ExpenseParseResult:
    """
    Real Document AI call. Lazy SDK import.

    Required env:
      GOOGLE_CLOUD_PROJECT             (set by Cloud Run automatically)
      DOCUMENT_AI_LOCATION             e.g. "me-west1" or "us"
      DOCUMENT_AI_EXPENSE_PROCESSOR_ID default Expense Parser id
      DOCUMENT_AI_HEBREW_PROCESSOR_ID  (P2-03) Hebrew-tuned custom
                                        processor id; used when
                                        language_hint="he".
    """
    from google.cloud import documentai  # type: ignore  # noqa: I001
    import json

    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("DOCUMENT_AI_LOCATION", "us")
    processor_id, processor_kind = _select_processor_id(language_hint)

    if not project:
        raise OcrError("OCR_BACKEND=documentai but GOOGLE_CLOUD_PROJECT is unset")

    client_options = {"api_endpoint": f"{location}-documentai.googleapis.com"}
    client = documentai.DocumentProcessorServiceClient(client_options=client_options)
    name = client.processor_path(project, location, processor_id)

    # Build the request — prefer inline bytes (avoids round-trip to GCS)
    if image_bytes:
        raw_document = documentai.RawDocument(content=image_bytes, mime_type=mime_type)
        request = documentai.ProcessRequest(name=name, raw_document=raw_document)
    elif gcs_uri:
        gcs_doc = documentai.GcsDocument(gcs_uri=gcs_uri, mime_type=mime_type)
        request = documentai.ProcessRequest(
            name=name,
            gcs_document=gcs_doc,
        )
    else:
        raise OcrError("No image_bytes and no gcs_uri")

    response = client.process_document(request=request)
    document = response.document

    # ── Map Document AI's expense-parser entities to our shape ──
    # Stamp which processor variant produced this — useful when grading
    # confidence (the Hebrew-tuned model should outperform the default
    # on Israeli invoices; if it doesn't, that's a training signal).
    parsed = ExpenseParseResult(backend=f"documentai/{processor_kind}")
    field_confidences: dict = {}

    for entity in document.entities or []:
        kind = entity.type_ or ""
        text = (entity.mention_text or "").strip()
        confidence = float(entity.confidence) if entity.confidence is not None else 0.0
        normalized = entity.normalized_value
        # Document AI's normalized_value is a oneof — Money / Date / Text

        if kind == "supplier_name":
            parsed.supplier_name = text or None
            field_confidences["supplier_name"] = confidence
        elif kind == "supplier_tax_id":
            parsed.supplier_tax_id = text or None
            field_confidences["supplier_tax_id"] = confidence
        elif kind == "total_amount" and normalized and normalized.money_value:
            money = normalized.money_value
            # money_value: {currency_code, units, nanos}
            units = int(money.units or 0)
            nanos = int(money.nanos or 0)
            # Convert to minor units. ILS has 2 fractional digits.
            major_total = units + (nanos / 1_000_000_000)
            parsed.total_amount_minor_units = int(round(major_total * 100))
            parsed.currency = (money.currency_code or "ILS").upper()
            field_confidences["total_amount"] = confidence
        elif kind == "total_tax_amount" and normalized and normalized.money_value:
            money = normalized.money_value
            major = int(money.units or 0) + (int(money.nanos or 0) / 1_000_000_000)
            parsed.vat_amount_minor_units = int(round(major * 100))
            field_confidences["total_tax_amount"] = confidence
        elif kind == "receipt_date" and normalized and normalized.date_value:
            d = normalized.date_value
            try:
                parsed.receipt_date = datetime.date(d.year, d.month, d.day)
            except Exception:
                pass
            field_confidences["receipt_date"] = confidence
        elif kind == "currency":
            parsed.currency = (text or "ILS").upper()
            field_confidences["currency"] = confidence

    parsed.field_confidences = field_confidences

    # Stash the raw response for audit (sanitised — no bytes, only entities)
    raw_serialisable = {
        "entity_count": len(document.entities or []),
        "entities": [
            {
                "type": e.type_,
                "text": e.mention_text,
                "confidence": float(e.confidence or 0),
            }
            for e in (document.entities or [])
        ],
    }
    parsed.raw_response_json = json.dumps(raw_serialisable, ensure_ascii=False)

    # Job id (operation name) for trace correlation
    parsed.document_ai_job_id = getattr(response, "operation_id", None) or None

    return parsed
