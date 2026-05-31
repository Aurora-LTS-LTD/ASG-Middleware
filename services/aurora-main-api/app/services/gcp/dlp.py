"""
Aurora LTS — Cloud DLP Wrapper
================================
Sprint 2 — scans every uploaded receipt for accidentally-shared PII
(Israeli ID cards, credit card numbers, passports). When DLP finds
a match we DON'T persist the bytes as a normal Receipt — we route
them into a quarantine state and tell the user "this looks like an
ID card, not a receipt; please re-take the photo".

Why this matters:
  Field workers occasionally photograph their wallet contents and
  send the entire stack. Without DLP, those identity documents would
  pile up in our receipts bucket — bad PII posture for the ITA Software
  House binder and a real liability under Israeli Protection of Privacy
  Law if the bucket ever leaked.

Two backends behind one shape:

  DLP_BACKEND='stub' (default)
    - Always returns clean=True (no PII detected)
    - Honours the b"FORCE_DLP_POSITIVE" magic marker so tests can
      drive the quarantine branch deterministically

  DLP_BACKEND='gcp'
    - Real google-cloud-dlp inspectContent call
    - Configured with Israeli-specific info types:
        ISRAEL_IDENTITY_CARD_NUMBER
        CREDIT_CARD_NUMBER
        PASSPORT
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional


DLP_BACKEND = (os.getenv("DLP_BACKEND") or "stub").strip().lower()


# ─────────────────────────────────────────────────────────────
# Caller-friendly result shape
# ─────────────────────────────────────────────────────────────
@dataclass
class DlpFinding:
    info_type: str           # e.g. "ISRAEL_IDENTITY_CARD_NUMBER"
    likelihood: str          # "LIKELY" | "VERY_LIKELY" | …
    quote: Optional[str] = None  # short snippet (may be None — we redact in logs anyway)


@dataclass
class DlpScanResult:
    clean: bool                                # True iff no findings above threshold
    findings: List[DlpFinding] = field(default_factory=list)
    backend: str = "stub"

    def quarantine_reason(self) -> Optional[str]:
        """A short, user-facing reason string for quarantine messages."""
        if self.clean:
            return None
        types = sorted({f.info_type for f in self.findings})
        return f"PII detected: {', '.join(types)}"


# ─────────────────────────────────────────────────────────────
# Public API — scan_image
# ─────────────────────────────────────────────────────────────
def scan_image(*, image_bytes: bytes, mime_type: str = "image/jpeg") -> DlpScanResult:
    """
    Inspect `image_bytes` for PII. Cheap call — Cloud DLP runs OCR
    over the image internally and only returns matches above a
    likelihood threshold (we use LIKELY+).
    """
    if DLP_BACKEND == "stub":
        return _stub_scan(image_bytes)

    if DLP_BACKEND == "gcp":
        return _gcp_scan(image_bytes, mime_type)

    raise ValueError(f"Unknown DLP_BACKEND='{DLP_BACKEND}'")


# ─────────────────────────────────────────────────────────────
# Stub backend
# ─────────────────────────────────────────────────────────────
def _stub_scan(image_bytes: bytes) -> DlpScanResult:
    try:
        snippet = image_bytes[:4096].decode("utf-8", errors="ignore")
    except Exception:
        snippet = ""

    if "FORCE_DLP_POSITIVE" in snippet:
        return DlpScanResult(
            clean=False,
            findings=[
                DlpFinding(
                    info_type="ISRAEL_IDENTITY_CARD_NUMBER",
                    likelihood="VERY_LIKELY",
                    quote=None,  # never echo the supposed PII
                )
            ],
            backend="stub",
        )
    return DlpScanResult(clean=True, findings=[], backend="stub")


# ─────────────────────────────────────────────────────────────
# Production backend
# ─────────────────────────────────────────────────────────────
def _gcp_scan(image_bytes: bytes, mime_type: str) -> DlpScanResult:
    """Real Cloud DLP call. Lazy SDK import."""
    from google.cloud import dlp_v2  # type: ignore

    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("DLP_BACKEND=gcp but GOOGLE_CLOUD_PROJECT is unset")

    client = dlp_v2.DlpServiceClient()
    parent = f"projects/{project}/locations/global"

    # Israeli-specific info types we care about for receipt uploads.
    # CREDIT_CARD_NUMBER and PASSPORT are global; ISRAEL_IDENTITY_CARD_NUMBER
    # is the Cloud DLP detector for Israeli ID numbers (תעודת זהות).
    info_types = [
        {"name": "ISRAEL_IDENTITY_CARD_NUMBER"},
        {"name": "CREDIT_CARD_NUMBER"},
        {"name": "PASSPORT"},
    ]

    # Translate Aurora MIME types to DLP byte-content types
    bct_map = {
        "image/jpeg": dlp_v2.ByteContentItem.BytesType.IMAGE_JPEG,
        "image/png": dlp_v2.ByteContentItem.BytesType.IMAGE_PNG,
        "image/heic": dlp_v2.ByteContentItem.BytesType.IMAGE,
        "image/heif": dlp_v2.ByteContentItem.BytesType.IMAGE,
        "application/pdf": dlp_v2.ByteContentItem.BytesType.BYTES_TYPE_UNSPECIFIED,
    }
    byte_type = bct_map.get(mime_type, dlp_v2.ByteContentItem.BytesType.IMAGE)

    item = {
        "byte_item": {
            "type_": byte_type,
            "data": image_bytes,
        }
    }
    inspect_config = {
        "info_types": info_types,
        "min_likelihood": dlp_v2.Likelihood.LIKELY,
        "include_quote": False,  # don't pull PII text into our process
        "limits": {"max_findings_per_request": 10},
    }

    response = client.inspect_content(
        request={
            "parent": parent,
            "inspect_config": inspect_config,
            "item": item,
        }
    )

    findings = []
    for f in response.result.findings:
        findings.append(
            DlpFinding(
                info_type=f.info_type.name,
                likelihood=dlp_v2.Likelihood(f.likelihood).name,
                quote=None,
            )
        )

    return DlpScanResult(
        clean=len(findings) == 0,
        findings=findings,
        backend="gcp",
    )
