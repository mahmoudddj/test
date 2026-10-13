"""Structured, PHI-safe logging.

Only operational metadata is logged (request id, method, route template, status, duration,
pseudonymous user id). Request/response bodies, query strings, tokens and file names are never
logged; study IDs are pseudonymous and appear only as part of route paths.
"""

from __future__ import annotations

import json
import logging
import sys
import time

_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
                "logger": record.name, "msg": record.getMessage()}
        base.update(getattr(record, "fields", {}))
        return json.dumps(base, default=str)


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger("irtcri")
    root.handlers[:] = [h]
    root.setLevel(level)
    root.propagate = False
    _configured = True


def request_logger():
    log = logging.getLogger("irtcri.access")

    async def middleware(request, call_next):
        t0 = time.perf_counter()
        status = 500
        try:
            resp = await call_next(request)
            status = resp.status_code
            return resp
        finally:
            route = request.scope.get("route")
            log.info("request", extra={"fields": {
                "request_id": getattr(request.state, "request_id", None), "method": request.method,
                "route": getattr(route, "path", request.url.path), "status": status,
                "duration_ms": round((time.perf_counter() - t0) * 1000, 1)}})
    return middleware
