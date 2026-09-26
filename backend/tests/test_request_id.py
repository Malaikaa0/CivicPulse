"""X-Request-ID handling and the access-log line, through the real ASGI middleware."""

import asyncio
import io
import json
import logging
import re
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from app.logging_config import JsonFormatter, request_id_var
from app.main import create_app
from app.metrics import Metrics
from app.middleware import ObservabilityMiddleware, accept_or_generate_request_id

GENERATED = re.compile(r"[0-9a-f]{32}")
test_logger = logging.getLogger("civicpulse.test.request")


class LogCapture:
    def __init__(self, stream: io.StringIO) -> None:
        self._stream = stream

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self._stream.getvalue().splitlines()]

    def named(self, logger: str) -> list[dict[str, Any]]:
        return [line for line in self.lines() if line["logger"] == logger]


@pytest.fixture
def logs() -> Iterator[LogCapture]:
    """A private JSON handler on the root logger, removed afterwards."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield LogCapture(stream)
    root.removeHandler(handler)
    root.setLevel(previous_level)


def _app() -> FastAPI:
    app = create_app()

    @app.get("/async-log")
    async def async_log() -> dict[str, str | None]:
        test_logger.info("from async handler")
        return {"seen": request_id_var.get()}

    @app.get("/sync-log")
    def sync_log() -> dict[str, str | None]:
        # Sync handlers run in Starlette's threadpool; the request id must follow them there.
        test_logger.info("from sync handler")
        return {"seen": request_id_var.get()}

    @app.get("/api/complaints/{complaint_id}")
    def one_complaint(complaint_id: uuid.UUID) -> dict[str, str]:
        return {"id": str(complaint_id)}

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


def test_a_request_id_is_generated_when_absent(client: TestClient) -> None:
    response = client.get("/health")

    assert GENERATED.fullmatch(response.headers["X-Request-ID"])


def test_a_different_id_is_generated_for_each_request(client: TestClient) -> None:
    first = client.get("/health").headers["X-Request-ID"]
    second = client.get("/health").headers["X-Request-ID"]

    assert first != second


@pytest.mark.parametrize("supplied", ["abc-123", "trace.id_42", "A" * 64, "7"])
def test_a_valid_request_id_is_kept_and_echoed(client: TestClient, supplied: str) -> None:
    response = client.get("/health", headers={"X-Request-ID": supplied})

    assert response.headers["X-Request-ID"] == supplied


@pytest.mark.parametrize(
    "supplied",
    [
        "A" * 65,  # too long
        "has space",
        'x","level":"CRITICAL","forged":"1',  # tries to close the JSON string
        "semi;colon",
        "",
    ],
)
def test_an_unsafe_request_id_is_replaced(client: TestClient, supplied: str) -> None:
    response = client.get("/health", headers={"X-Request-ID": supplied})

    assert GENERATED.fullmatch(response.headers["X-Request-ID"])


@pytest.mark.parametrize(
    "supplied", ["abc\nforged", "abc\n", "\nabc", "abc\r\nSet-Cookie: x=1", "unicode-é"]
)
def test_a_request_id_with_a_line_break_or_non_ascii_is_replaced(supplied: str) -> None:
    # httpx will not send these in a header, so test the rule itself; the next test then
    # sends a newline through the real middleware.
    assert GENERATED.fullmatch(accept_or_generate_request_id(supplied))


def test_a_newline_in_the_header_never_reaches_the_logs(logs: LogCapture) -> None:
    app = _app()
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope: Scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/async-log",
        "raw_path": b"/async-log",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": [(b"x-request-id", b"abc\nforged")],
        "server": ("test", 80),
        "client": ("test", 1),
    }
    asyncio.run(app(scope, receive, send))

    start = next(m for m in sent if m["type"] == "http.response.start")
    echoed = dict(start["headers"])[b"x-request-id"].decode()
    assert GENERATED.fullmatch(echoed)
    assert all(line["request_id"] == echoed for line in logs.lines())
    assert "forged" not in json.dumps(logs.lines())


def test_the_request_id_is_echoed_on_error_responses(client: TestClient) -> None:
    missing = client.get("/no/such/route", headers={"X-Request-ID": "trace-1"})
    invalid = client.get("/api/complaints/not-a-uuid", headers={"X-Request-ID": "trace-2"})

    assert (missing.status_code, missing.headers["X-Request-ID"]) == (404, "trace-1")
    assert (invalid.status_code, invalid.headers["X-Request-ID"]) == (422, "trace-2")


def test_log_lines_from_an_async_handler_carry_the_request_id(
    client: TestClient, logs: LogCapture
) -> None:
    response = client.get("/async-log", headers={"X-Request-ID": "async-1"})

    assert response.json() == {"seen": "async-1"}
    (line,) = logs.named("civicpulse.test.request")
    assert line["request_id"] == "async-1"


def test_log_lines_from_a_sync_handler_carry_the_request_id(
    client: TestClient, logs: LogCapture
) -> None:
    response = client.get("/sync-log", headers={"X-Request-ID": "sync-1"})

    assert response.json() == {"seen": "sync-1"}
    (line,) = logs.named("civicpulse.test.request")
    assert line["request_id"] == "sync-1"
    assert line["message"] == "from sync handler"


def test_concurrent_requests_each_keep_their_own_request_id(logs: LogCapture) -> None:
    app = _app()
    barrier = asyncio.Barrier(2)

    @app.get("/rendezvous")
    async def rendezvous() -> dict[str, str | None]:
        # Both requests must be inside the handler at once before either logs.
        await barrier.wait()
        test_logger.info("both in flight")
        return {"seen": request_id_var.get()}

    async def run() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            return await asyncio.gather(
                http.get("/rendezvous", headers={"X-Request-ID": "one"}),
                http.get("/rendezvous", headers={"X-Request-ID": "two"}),
            )

    first, second = asyncio.run(run())

    assert first.json() == {"seen": "one"}
    assert second.json() == {"seen": "two"}
    assert first.headers["X-Request-ID"] == "one"
    assert second.headers["X-Request-ID"] == "two"
    ids = sorted(line["request_id"] for line in logs.named("civicpulse.test.request"))
    assert ids == ["one", "two"]


def test_there_is_no_request_id_outside_a_request(client: TestClient, logs: LogCapture) -> None:
    client.get("/sync-log")
    test_logger.info("after the request")

    assert request_id_var.get() is None
    (outside,) = [line for line in logs.lines() if line["message"] == "after the request"]
    assert outside["request_id"] is None


def test_one_access_log_line_per_request_with_the_template_path(
    client: TestClient, logs: LogCapture
) -> None:
    complaint_id = uuid.uuid4()

    client.get(f"/api/complaints/{complaint_id}", headers={"X-Request-ID": "acc-1"})

    (line,) = logs.named("civicpulse.access")
    assert line["level"] == "INFO"
    assert line["method"] == "GET"
    assert line["path"] == "/api/complaints/{complaint_id}"
    assert line["status"] == 200
    assert isinstance(line["duration_ms"], float)
    assert line["duration_ms"] >= 0
    assert line["request_id"] == "acc-1"
    # The raw id never appears, so ids do not leak into log-based dashboards either.
    assert str(complaint_id) not in json.dumps(line)


def test_the_access_line_for_an_unmatched_route(client: TestClient, logs: LogCapture) -> None:
    client.get("/wp-login.php")

    (line,) = logs.named("civicpulse.access")
    assert (line["path"], line["status"]) == ("unmatched", 404)


def test_the_access_line_duration_uses_the_injected_clock(logs: LogCapture) -> None:
    ticks = iter([100.0, 100.25])
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware, metrics=Metrics(), clock=lambda: next(ticks))

    @app.get("/x")
    def x() -> dict[str, str]:
        return {}

    TestClient(app).get("/x")

    (line,) = logs.named("civicpulse.access")
    assert line["duration_ms"] == 250.0


def test_an_unhandled_exception_is_logged_with_the_request_id(
    client: TestClient, logs: LogCapture
) -> None:
    response = client.get("/boom", headers={"X-Request-ID": "boom-1"})

    assert response.status_code == 500
    (error,) = logs.named("civicpulse.request")
    assert error["level"] == "ERROR"
    assert error["request_id"] == "boom-1"
    assert "RuntimeError: kaboom" in error["exception"]
    (access,) = logs.named("civicpulse.access")
    assert (access["status"], access["request_id"]) == (500, "boom-1")


def test_a_client_that_disconnects_is_logged_as_499_not_500(logs: LogCapture) -> None:
    app = _app()
    entered = asyncio.Event()

    @app.get("/hang")
    async def hang() -> None:
        entered.set()
        await asyncio.sleep(3600)

    async def run() -> None:
        async def receive() -> Message:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

        async def send(message: Message) -> None:
            raise AssertionError("no response expected")

        scope: Scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": "/hang",
            "raw_path": b"/hang",
            "query_string": b"",
            "root_path": "",
            "scheme": "http",
            "headers": [],
            "server": ("test", 80),
            "client": ("test", 1),
        }
        task = asyncio.create_task(app(scope, receive, send))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())

    (line,) = logs.named("civicpulse.access")
    assert (line["path"], line["status"]) == ("/hang", 499)


def test_lifespan_and_other_non_http_scopes_pass_straight_through() -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    middleware = ObservabilityMiddleware(inner, metrics=Metrics())

    async def noop_receive() -> Message:
        return {"type": "noop"}

    async def noop_send(message: Message) -> None:
        return None

    asyncio.run(middleware({"type": "lifespan"}, noop_receive, noop_send))
    asyncio.run(middleware({"type": "websocket"}, noop_receive, noop_send))

    assert seen == ["lifespan", "websocket"]
