"""The JSON formatter and the logging setup. Output is captured with the test's own handler or
capsys, never by reconfiguring the root logger for good."""

import io
import json
import logging
from collections.abc import Iterator
from datetime import datetime

import pytest

from app.config import get_settings
from app.logging_config import (
    JsonFormatter,
    JsonStdoutHandler,
    configure_logging,
    request_id_var,
    resolve_log_level,
)

UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture
def isolated_logging() -> Iterator[None]:
    """configure_logging changes global logger state; put it back so other tests are unaffected."""
    root = logging.getLogger()
    saved_root = (root.handlers[:], root.level)
    saved_uvicorn = {
        name: (lg.handlers[:], lg.propagate, lg.level)
        for name in UVICORN_LOGGERS
        if (lg := logging.getLogger(name))
    }
    yield
    root.handlers[:] = saved_root[0]
    root.setLevel(saved_root[1])
    for name, (handlers, propagate, level) in saved_uvicorn.items():
        lg = logging.getLogger(name)
        lg.handlers[:] = handlers
        lg.propagate = propagate
        lg.setLevel(level)


def _format(message: str = "hello", *args: object, **kwargs: object) -> dict[str, object]:
    logger = logging.getLogger("civicpulse.test")
    record = logger.makeRecord(
        logger.name, logging.INFO, __file__, 1, message, args, None, **kwargs
    )
    return json.loads(JsonFormatter().format(record))


def _own_logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(name)
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def test_a_line_is_one_json_object_with_the_required_fields() -> None:
    entry = _format("created %s in %d ms", "abc", 12)

    assert set(entry) == {"timestamp", "level", "logger", "message", "request_id"}
    assert entry["level"] == "INFO"
    assert entry["logger"] == "civicpulse.test"
    assert entry["message"] == "created abc in 12 ms"


def test_the_timestamp_is_iso_8601_in_utc() -> None:
    timestamp = str(_format()["timestamp"])

    assert timestamp.endswith("Z")
    parsed = datetime.fromisoformat(timestamp)
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_request_id_is_null_outside_a_request() -> None:
    # Explicit rather than omitted, so every line has the same shape for the log shipper.
    assert request_id_var.get() is None
    assert _format()["request_id"] is None


def test_request_id_comes_from_the_context() -> None:
    token = request_id_var.set("req-123")
    try:
        assert _format()["request_id"] == "req-123"
    finally:
        request_id_var.reset(token)


def test_extra_fields_are_surfaced() -> None:
    entry = _format(
        "triage fell back",
        extra={"complaint_id": "c-1", "provider": "llm:groq", "error_class": "TriageTimeout"},
    )

    assert entry["complaint_id"] == "c-1"
    assert entry["provider"] == "llm:groq"
    assert entry["error_class"] == "TriageTimeout"


def test_extra_cannot_override_the_fields_the_formatter_owns() -> None:
    entry = _format("real", extra={"request_id": "forged", "level": "CRITICAL", "logger": "x"})

    assert entry["request_id"] is None
    assert entry["level"] == "INFO"
    assert entry["logger"] == "civicpulse.test"
    assert entry["message"] == "real"


def test_an_unserialisable_extra_does_not_break_the_line() -> None:
    entry = _format("x", extra={"thing": object()})

    assert str(entry["thing"]).startswith("<object object")


def test_uvicorns_coloured_duplicate_message_is_not_emitted() -> None:
    assert "color_message" not in _format("x", extra={"color_message": "\x1b[32mx\x1b[0m"})


def test_exception_info_becomes_a_string_field() -> None:
    logger, stream = _own_logger("civicpulse.test.exc")
    try:
        raise ValueError("bad thing")
    except ValueError:
        logger.exception("it failed")

    entry = json.loads(stream.getvalue())
    assert entry["level"] == "ERROR"
    assert isinstance(entry["exception"], str)
    assert "Traceback" in entry["exception"]
    assert "ValueError: bad thing" in entry["exception"]


def test_stack_info_becomes_a_string_field() -> None:
    logger, stream = _own_logger("civicpulse.test.stack")
    logger.warning("where am I", stack_info=True)

    assert "test_stack_info_becomes_a_string_field" in json.loads(stream.getvalue())["stack"]


def test_a_multiline_message_stays_on_one_line() -> None:
    logger, stream = _own_logger("civicpulse.test.multiline")
    logger.info("first\nsecond")

    assert len(stream.getvalue().splitlines()) == 1
    assert json.loads(stream.getvalue())["message"] == "first\nsecond"


def test_configure_logging_writes_json_to_stdout(
    isolated_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO")

    logging.getLogger("civicpulse.test.stdout").info("to stdout", extra={"complaint_id": "c-9"})

    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if "to stdout" in line]
    assert len(lines) == 1
    assert json.loads(lines[0])["complaint_id"] == "c-9"
    assert captured.err == ""


def test_configure_logging_is_idempotent(isolated_logging: None) -> None:
    configure_logging("INFO")
    configure_logging("INFO")

    ours = [h for h in logging.getLogger().handlers if isinstance(h, JsonStdoutHandler)]
    assert len(ours) == 1


def test_configure_logging_twice_does_not_duplicate_lines(
    isolated_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO")
    configure_logging("INFO")

    logging.getLogger("civicpulse.test.dup").info("once only")

    assert capsys.readouterr().out.count("once only") == 1


def test_no_configured_logger_writes_to_a_file(isolated_logging: None) -> None:
    # pytest puts its own (null) FileHandler on the root logger, so look at what we added. An
    # earlier create_app() may already have added ours, so start from a root without it.
    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if not isinstance(h, JsonStdoutHandler)]
    before = list(root.handlers)

    configure_logging("INFO")

    added = [h for h in logging.getLogger().handlers if h not in before]
    assert added
    assert not any(isinstance(h, logging.FileHandler) for h in added)
    for name in UVICORN_LOGGERS:
        assert logging.getLogger(name).handlers == []


def test_configure_logging_applies_the_level_and_updates_it_on_a_second_call(
    isolated_logging: None,
) -> None:
    configure_logging("warning")
    assert logging.getLogger().level == logging.WARNING

    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_an_unknown_level_falls_back_to_info_and_says_so(
    isolated_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("LOUD")

    assert logging.getLogger().level == logging.INFO
    warning = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert warning["level"] == "WARNING"
    assert warning["requested_level"] == "LOUD"


def test_uvicorn_loggers_use_the_same_handler_without_duplicates(
    isolated_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # Mimic what uvicorn installs before the app is imported.
    for name in UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        lg.handlers[:] = [logging.StreamHandler()]
        lg.propagate = False

    configure_logging("INFO")

    for name in UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        assert lg.handlers == []
        assert lg.propagate is True
        lg.info("from %s", name)

    out = capsys.readouterr().out.splitlines()
    entries = [json.loads(line) for line in out if line.startswith("{")]
    assert sorted(e["message"] for e in entries) == sorted(f"from {n}" for n in UVICORN_LOGGERS)


def test_configure_logging_leaves_other_root_handlers_alone(isolated_logging: None) -> None:
    # pytest's caplog is a handler on the root logger; removing it would break every caplog test.
    foreign = logging.NullHandler()
    logging.getLogger().addHandler(foreign)

    configure_logging("INFO")

    assert foreign in logging.getLogger().handlers


def test_the_handler_follows_a_replaced_stdout(
    isolated_logging: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_logging("INFO")
    replacement = io.StringIO()
    monkeypatch.setattr("sys.stdout", replacement)

    logging.getLogger("civicpulse.test.swap").info("after the swap")

    assert "after the swap" in replacement.getvalue()


def test_a_line_that_cannot_be_formatted_is_reported_not_raised() -> None:
    handler = JsonStdoutHandler()
    handler.setFormatter(JsonFormatter())
    failures: list[logging.LogRecord] = []
    handler.handleError = failures.append
    logger = logging.getLogger("civicpulse.test.broken")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("%d", "not a number")  # the logging module swallows it into handleError

    assert len(failures) == 1


@pytest.fixture
def fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    get_settings.cache_clear()
    yield monkeypatch
    monkeypatch.undo()
    get_settings.cache_clear()


def test_the_level_comes_from_settings(fresh_settings: pytest.MonkeyPatch) -> None:
    fresh_settings.setenv("LOG_LEVEL", "WARNING")

    assert resolve_log_level() == "WARNING"


def test_the_level_falls_back_to_the_environment_when_settings_cannot_be_built(
    fresh_settings: pytest.MonkeyPatch,
) -> None:
    fresh_settings.delenv("DATABASE_URL")
    fresh_settings.setenv("LOG_LEVEL", "DEBUG")

    assert resolve_log_level() == "DEBUG"


def test_the_level_defaults_to_info_when_nothing_is_set(
    fresh_settings: pytest.MonkeyPatch,
) -> None:
    fresh_settings.delenv("DATABASE_URL")
    fresh_settings.delenv("LOG_LEVEL", raising=False)

    assert resolve_log_level() == "INFO"
