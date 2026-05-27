"""
Aurora LTS — Backend Selector Production Check (P1-09 / P1-10 / P1-11)
========================================================================
Cloud Run boots with whatever env vars are configured. If any of the
critical backend selectors is still on its "stub" / "mock" default in
production, the platform silently no-ops the corresponding real-world
side effect:

  ITA_BACKEND=mock              → invoices get fake allocation numbers
                                  that won't reconcile at rashut hamisim
  STORAGE_BACKEND=stub          → GCS uploads silently dropped on the floor
  AUDIT_BIGQUERY_BACKEND=stub   → audit events discarded in memory
                                  (regulator can't read what isn't there)

This module validates the env at startup. In cloud_run mode any
forbidden value raises RuntimeError before the FastAPI app marks
healthy — Cloud Run refuses to roll the deploy forward.

In local dev (AURORA_RUNTIME unset) we log a warning per misconfigured
selector and continue — the stub defaults are explicitly desirable in dev.

Other backend selectors (OCR_BACKEND, DLP_BACKEND, GEMINI_BACKEND,
PAYPLUS_BACKEND, KYC_BACKEND, OTP_BACKEND) are checked at the WARN
level only — they have legitimate "feature off" modes where stub is
acceptable in production. The hard-fail list is restricted to the
three audit-critical ones.
"""
from __future__ import annotations

import logging
import os
from typing import List, Tuple

log = logging.getLogger(__name__)


# (env_var_name, list_of_forbidden_values_in_production)
_HARD_FAIL: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("ITA_BACKEND",             ("mock", "stub", "")),
    ("STORAGE_BACKEND",         ("stub", "")),
    ("AUDIT_BIGQUERY_BACKEND",  ("stub", "")),
)

_WARN_ONLY: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("OCR_BACKEND",     ("stub", "")),
    ("DLP_BACKEND",     ("stub", "")),
    ("GEMINI_BACKEND",  ("stub", "")),
    ("KYC_BACKEND",     ("stub", "")),
    ("OTP_BACKEND",     ("stub", "")),
    ("PAYPLUS_BACKEND", ("stub", "")),
)


def _is_cloud_run() -> bool:
    return os.getenv("AURORA_RUNTIME", "").strip().lower() == "cloud_run"


def validate_backend_selectors() -> None:
    """
    Raise RuntimeError in cloud_run mode for any HARD_FAIL backend
    that's set to a forbidden value. Always log WARNs for the soft
    list when on cloud_run.

    Idempotent — safe to call multiple times.
    """
    in_prod = _is_cloud_run()
    hard_failures: List[str] = []

    for env_name, forbidden in _HARD_FAIL:
        actual = (os.getenv(env_name) or "").strip().lower()
        # If unset (""), evaluate against the empty-string forbidden marker.
        if actual in forbidden:
            msg = (
                f"{env_name}={actual!r} is forbidden in production — "
                f"set to a real backend (allowed != {sorted(set(forbidden))})"
            )
            if in_prod:
                hard_failures.append(msg)
            else:
                log.info("[backend-check] dev — %s (stub OK locally)", msg)

    for env_name, forbidden in _WARN_ONLY:
        actual = (os.getenv(env_name) or "").strip().lower()
        if actual in forbidden and in_prod:
            log.warning(
                "[backend-check] PROD WARN: %s=%s — feature is in stub mode "
                "in production; flip to a real backend when the integration "
                "is ready.", env_name, actual or "(unset)"
            )

    if hard_failures:
        # One consolidated message so operators see every problem at once.
        joined = "\n  - ".join(hard_failures)
        raise RuntimeError(
            "Aurora startup aborted — production backend misconfiguration:\n  - "
            + joined
        )


__all__ = ["validate_backend_selectors"]
