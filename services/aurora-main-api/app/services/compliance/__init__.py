"""
Aurora LTS — Compliance Package (Sprint 6)
================================================
Cross-cutting compliance plumbing:
  - pii_redactor.py       : sanitises PII for logs + BigQuery audit export
  - immutability.py       : SQLAlchemy event-listener guards on terminal-state rows
  - bigquery_export.py    : daily-job skeleton that ships ActionLog → BigQuery
  - dsar.py               : Data Subject Access Request bundle generator (PPL §13)

Public re-exports:
    from app.services.compliance import (
        redact_pii, mask_phone, mask_email, mask_tax_id,
        install_immutability_guards,
        export_audit_to_bigquery,
        build_dsar_bundle,
    )
"""

from app.services.compliance.pii_redactor import (
    redact_pii,
    mask_phone,
    mask_email,
    mask_tax_id,
)
from app.services.compliance.immutability import install_immutability_guards
from app.services.compliance.bigquery_export import (
    export_audit_to_bigquery,
    AUDIT_BIGQUERY_BACKEND,
)
from app.services.compliance.dsar import build_dsar_bundle

__all__ = [
    "redact_pii", "mask_phone", "mask_email", "mask_tax_id",
    "install_immutability_guards",
    "export_audit_to_bigquery",
    "AUDIT_BIGQUERY_BACKEND",
    "build_dsar_bundle",
]
