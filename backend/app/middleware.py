"""Per-request observability: request id, access log line and request metrics.

Pure ASGI rather than BaseHTTPMiddleware on purpose. BaseHTTPMiddleware runs the app in a
separate task and buffers responses, so a ContextVar set after it starts does not reliably
reach the handler's logs, and it cannot see the matched route after the call. A pure ASGI
middleware runs in the request's own task and sees the same scope the router fills in.
"""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Callable, MutableMapping
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_config import request_id_var
from app.metrics import UNMATCHED_ROUTE, Metrics

REQUEST_ID_HEADER = "X-Request-ID"

# fullmatch, not match with $: `$` would let a trailing newline through into the logs.
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

# nginx's convention for "client closed the connection before we answered".
_CLIENT_CLOSED = 499

access_logger = logging.getLogger("civicpulse.access")
logger = logging.getLogger("civicpulse.request")


def accept_or_generate_request_id(candidate: str | None) -> str:
    """Keep a client's id only if it is short and plain; anything else could smuggle newlines
    or forged fields into the logs, so it is replaced rather than repaired."""
    if candidate is not None and _SAFE_REQUEST_ID.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


def _route_template(scope: MutableMapping[str, Any]) -> str:
    # The router stores the matched route on the scope. Logging its template
    # (/api/complaints/{complaint_id}) instead of the raw path keeps ids out of metric labels.
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else UNMATCHED_ROUTE


class ObservabilityMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        metrics: Metrics,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.app = app
        self._metrics = metrics
        self._clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = accept_or_generate_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        token = request_id_var.set(request_id)
        started = self._clock()
        status = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except asyncio.CancelledError:
            if not response_started:
                status = _CLIENT_CLOSED
            raise
        except Exception:
            # ServerErrorMiddleware sits outside this one, so it builds the 500 and uvicorn
            # logs the traceback after this context is gone. Log it here, with the request id.
            logger.exception("unhandled exception")
            raise
        finally:
            seconds = self._clock() - started
            method = scope["method"]
            route = _route_template(scope)
            self._metrics.observe_request(method, route, status, seconds)
            access_logger.info(
                "%s %s %s",
                method,
                route,
                status,
                extra={
                    "method": method,
                    "path": route,
                    "status": status,
                    "duration_ms": round(seconds * 1000, 3),
                },
            )
            request_id_var.reset(token)
