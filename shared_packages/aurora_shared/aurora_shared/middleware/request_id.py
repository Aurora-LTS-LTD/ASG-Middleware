"""
Aurora LTS — X-Request-ID Middleware (P1-07)
=============================================
Tags every HTTP request with a stable correlation ID so logs across
services (FastAPI, Cloud SQL slow logs, Cloud Tasks, downstream Make.com
relays) can be stitched together.

BEHAVIOR:
  - If the inbound request carries `X-Request-ID`, we trust it (Cloud
    Run / Global Load Balancer forwards its own; preserving it means
    GCP traces line up with our app logs).
  - Otherwise we generate uuid4().hex.
  - Value is stashed on `request.state.request_id` for the rest of the
    handler chain (used by P1-06 exception handler + P1-08 structured
    logger to attach it to every log line).
  - Echoed back in the response header so clients can include it when
    filing support tickets.

CONTEXT VAR:
  A contextvars.ContextVar mirrors the value so log records emitted
  by code without access to `request` (e.g. background tasks spawned
  from a handler) can still pull the current request_id.
"""
from __future__ import annotations

import contextvars
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Global access point for code that doesn't have the Request object.
# Default "-" makes the absence visible in logs.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get("x-request-id", "").strip()
        request_id = incoming if incoming else uuid.uuid4().hex

        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        response.headers["X-Request-ID"] = request_id
        return response


def current_request_id() -> str:
    """Read the active request_id from the contextvar (returns '-' if unset)."""
    return request_id_var.get()


__all__ = ["RequestIDMiddleware", "current_request_id", "request_id_var"]
