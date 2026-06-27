"""
Aurora LTS — Global Exception Handlers (P1-06)
=================================================
Catches uncaught exceptions before FastAPI's default handler returns a
naked 500 with the raw traceback. Three handlers are registered:

  OperationalError /   — transient DB capacity/connectivity (Cloud SQL
  pool TimeoutError      connection-slot exhaustion, pool-checkout timeout)
                         → retryable 503, so clients retry instead of
                         seeing a hard 500. (v1-19-1 native-handshake hotfix.)

  HTTPException        — keep FastAPI's behaviour but ATTACH the
                         request_id from P1-07 so support tickets can
                         be traced back to a single request.

  Exception (catch-all) — convert every other uncaught error into a
                          JSON envelope with:
                              { "error": "internal_server_error",
                                "message": "<safe-to-show string>",
                                "request_id": "<uuid>" }
                          Log the full traceback at ERROR level with the
                          request_id so it can be grepped in Cloud Logging.
                          Never leaks the traceback to the client.

The handlers are registered by register_exception_handlers(app) called
from main.py during app construction.
"""
from __future__ import annotations

import logging
import traceback

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, TimeoutError as SAPoolTimeoutError

from aurora_shared.middleware.request_id import current_request_id

log = logging.getLogger(__name__)


def _cors_error_headers(request: Request, allowed_origins: set) -> dict:
    """CORS headers for the catch-all 500 response.

    That handler runs inside Starlette's ServerErrorMiddleware, which sits
    OUTSIDE CORSMiddleware — so its response would otherwise carry NO
    Access-Control-Allow-Origin. A browser (and the AuroraMacShell WKWebView,
    whose file:// pages send ``Origin: null``) then blocks the response and the
    fetch rejects with the opaque "Load failed" instead of a readable error +
    request_id. We mirror CORSMiddleware: reflect the Origin only if allow-listed.
    """
    origin = request.headers.get("origin")
    if origin and origin in allowed_origins:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}


def register_exception_handlers(app: FastAPI, allowed_origins=None) -> None:
    """Attach Aurora's global exception handlers to the given FastAPI app.

    ``allowed_origins`` is the same allowlist handed to CORSMiddleware; it lets
    the catch-all 500 handler attach the correct Access-Control-Allow-Origin so
    server errors are legible in cross-origin clients (see _cors_error_headers).
    """
    allowed = set(allowed_origins or [])

    async def _db_unavailable_handler(request: Request, exc: Exception):
        """Transient DB capacity/connectivity → retryable 503 (not a 500).

        Covers Cloud SQL connection-slot exhaustion (psycopg.OperationalError,
        wrapped by SQLAlchemy as OperationalError) and pool-checkout timeouts
        (sqlalchemy.exc.TimeoutError). These are infra/transient, NOT logic
        bugs, so the client (e.g. the AuroraMacShell native handshake, which
        already says "Retry in a moment") should retry rather than treat it as
        a hard 500 / auth failure. This handler is registered for SPECIFIC
        exception types, so it runs inside ExceptionMiddleware (inside
        CORSMiddleware) and the CORS header is added on the way out.

        Logs class + path ONLY — never str(exc), so the DB socket/DSN can't leak.
        """
        rid = getattr(request.state, "request_id", None) or current_request_id()
        log.error(
            "[db-unavailable] request_id=%s path=%s method=%s exc=%s",
            rid, request.url.path, request.method, exc.__class__.__name__,
        )
        headers = {"Retry-After": "2"}
        if rid and rid != "-":
            headers["X-Request-ID"] = rid
        return JSONResponse(
            status_code=503,
            content={
                "error": "database_unavailable",
                "message": "The service is briefly at capacity. Please retry in a moment.",
                "request_id": rid,
            },
            headers=headers,
        )

    app.add_exception_handler(OperationalError, _db_unavailable_handler)
    app.add_exception_handler(SAPoolTimeoutError, _db_unavailable_handler)

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        rid = getattr(request.state, "request_id", None) or current_request_id()
        # Re-emit FastAPI's standard 4xx/5xx shape but include request_id.
        payload = {
            "detail": exc.detail,
            "request_id": rid,
        }
        return JSONResponse(
            status_code=exc.status_code,
            content=payload,
            headers={"X-Request-ID": rid} if rid and rid != "-" else None,
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", None) or current_request_id()
        # Full traceback to logs (operator visibility). Cloud Logging will
        # capture this with the request_id so we can trace user-reported
        # failures.
        log.error(
            "[unhandled] request_id=%s path=%s method=%s exc=%s\n%s",
            rid,
            request.url.path,
            request.method,
            exc.__class__.__name__,
            traceback.format_exc(),
        )
        headers = {}
        if rid and rid != "-":
            headers["X-Request-ID"] = rid
        # Critical: this response is generated OUTSIDE CORSMiddleware, so we add
        # the CORS header ourselves or the shell sees only "Load failed".
        headers.update(_cors_error_headers(request, allowed))
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "message": "An unexpected error occurred. "
                           "Quote the request_id when contacting support.",
                "request_id": rid,
            },
            headers=headers or None,
        )


__all__ = ["register_exception_handlers"]
