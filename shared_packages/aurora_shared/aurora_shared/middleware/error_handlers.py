"""
Aurora LTS — Global Exception Handlers (P1-06)
=================================================
Catches uncaught exceptions before FastAPI's default handler returns a
naked 500 with the raw traceback. Two handlers are registered:

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

from aurora_shared.middleware.request_id import current_request_id

log = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach Aurora's global exception handlers to the given FastAPI app."""

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
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "message": "An unexpected error occurred. "
                           "Quote the request_id when contacting support.",
                "request_id": rid,
            },
            headers={"X-Request-ID": rid} if rid and rid != "-" else None,
        )


__all__ = ["register_exception_handlers"]
