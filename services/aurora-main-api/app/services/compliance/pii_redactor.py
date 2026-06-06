"""
Aurora LTS — PII Redactor (Sprint 6)
=========================================
Centralised redaction rules. Used by:
  - bigquery_export    (every row gets redacted before export)
  - dsar               (when previewing logs to a data-subject we redact other tenants' data)
  - admin compliance   (rendering audit logs in the admin UI)

The same rules are applied across the whole codebase so a single
audit definition covers every PII path. NEVER copy these patterns
into individual call-sites — call the helpers here.

REDACTION SHAPES:
  Phone (E.164):    "+972501234567"      → "+97*****567"
  Email:            "user@example.com"   → "us***@example.com"
  Tax ID (9-digit): "123456782"          → "123****82"
  Generic 9-digit:  "987654321"          → "987****21"
  Free-text:        any 9-digit run is masked + email pattern + phone pattern
"""

import re
from typing import Optional


_PHONE_RE = re.compile(r"\+\d{1,3}\d{6,12}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TAX_ID_RE = re.compile(r"(?<!\d)\d{9}(?!\d)")


def mask_phone(value: Optional[str]) -> str:
    """'+972501234567' → '+97*****567'."""
    if not value:
        return ""
    s = str(value)
    if len(s) <= 5:
        return "*" * len(s)
    return f"{s[:3]}{'*' * (len(s) - 6)}{s[-3:]}"


def mask_email(value: Optional[str]) -> str:
    """'user@example.com' → 'us***@example.com'. Domain stays intact."""
    if not value or "@" not in value:
        return value or ""
    local, _, domain = value.partition("@")
    if len(local) <= 2:
        return f"{local[0]}*@{domain}" if local else f"*@{domain}"
    return f"{local[:2]}***@{domain}"


def mask_tax_id(value: Optional[str]) -> str:
    """'123456782' → '123****82'."""
    if not value:
        return ""
    s = str(value).strip()
    if len(s) < 5:
        return "*" * len(s)
    return f"{s[:3]}{'*' * (len(s) - 5)}{s[-2:]}"


def redact_pii(text: Optional[str]) -> str:
    """
    Free-text redactor — finds and masks every PII pattern in a string.
    Use for ActionLog.detail before BigQuery export.
    """
    if not text:
        return ""
    s = str(text)
    s = _EMAIL_RE.sub(lambda m: mask_email(m.group()), s)
    s = _PHONE_RE.sub(lambda m: mask_phone(m.group()), s)
    s = _TAX_ID_RE.sub(lambda m: mask_tax_id(m.group()), s)
    return s
