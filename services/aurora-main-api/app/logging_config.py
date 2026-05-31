"""
Aurora LTS — Structured Logging (P1-08)
=========================================
Configures Python `logging` to emit JSON to stderr that Cloud Logging
auto-parses into structured fields. Replaces the previous mix of bare
`print()` + ad-hoc `logging.info()` calls.

JSON SHAPE (every record):
  {
    "severity": "INFO" | "WARNING" | "ERROR" | "CRITICAL" | "DEBUG",
    "message":  "<the formatted message>",
    "timestamp": "2026-05-27T08:42:13.456Z",
    "logger":   "app.services.invoice_service",
    "request_id": "<from P1-07 contextvar, '-' if none>",
    "module":   "invoice_service",
    "function": "finalize_invoice",
    "line":     238,
    "exception": "<traceback>"  (only on ERROR with exc_info)
  }

CLOUD LOGGING:
  Cloud Run captures stderr/stdout as plain text by default, but if
  the first byte of each line is "{" and the line parses as JSON,
  Cloud Logging promotes the JSON fields onto the LogEntry. The
  `severity` field is mapped natively to the LogEntry severity level
  (so filtering by ERROR works without text grep).

LOCAL DEV:
  Set AURORA_LOG_FORMAT=text to get the legacy human-readable format.
  Default in dev (no AURORA_RUNTIME=cloud_run) is "text" so local
  output remains readable. Default in cloud_run mode is "json".
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any, Dict

from app.middleware.request_id import current_request_id


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record to stderr."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            # ISO-8601 with millisecond precision; trailing Z = UTC.
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
            ) + f".{int(record.msecs):03d}Z",
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "request_id": current_request_id(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Include any LogRecord.extra fields that were attached by caller.
        for k, v in record.__dict__.items():
            if k in payload or k.startswith("_"):
                continue
            if k in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            }:
                continue
            try:
                json.dumps(v)  # only include JSON-serialisable extras
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """
    Idempotent. Wires the root logger's StreamHandler to either the
    JSON formatter or the legacy text formatter based on env.
    """
    fmt_choice = (
        os.getenv("AURORA_LOG_FORMAT")
        or ("json" if os.getenv("AURORA_RUNTIME") == "cloud_run" else "text")
    ).strip().lower()

    root = logging.getLogger()
    # Clear existing handlers so re-runs don't double up.
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stderr)
    if fmt_choice == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        ))

    root.addHandler(handler)
    root.setLevel(os.getenv("AURORA_LOG_LEVEL", "INFO").upper())

    # Tame noisy loggers in production.
    logging.getLogger("uvicorn.access").setLevel("WARNING")
    logging.getLogger("uvicorn.error").setLevel("INFO")
    logging.getLogger("sqlalchemy.engine").setLevel("WARNING")


__all__ = ["configure_logging", "JsonFormatter"]
