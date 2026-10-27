import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
import structlog

from acm_core.logging import (
    TraceContext,
    configure_logging,
    current_trace_context,
    get_logger,
    set_trace_context_provider,
    trace_context,
)
from acm_core.settings import LogFormat


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    configure_logging("DEBUG", LogFormat.JSON, stream=buffer)
    yield buffer
    set_trace_context_provider(None)
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_json_lines_with_standard_fields(stream: io.StringIO) -> None:
    get_logger("acm.test", component="api").info("hello", run_id="run_x")
    [line] = _lines(stream)
    assert line["event"] == "hello"
    assert line["level"] == "info"
    assert line["logger"] == "acm.test"
    assert line["component"] == "api"
    assert line["run_id"] == "run_x"
    assert line["timestamp"].endswith("Z")
    assert "trace_id" not in line


def test_trace_context_manager(stream: io.StringIO) -> None:
    log = get_logger("acm.test")
    with trace_context("a" * 32, "b" * 16):
        log.info("inside")
    log.info("outside")
    inside, outside = _lines(stream)
    assert inside["trace_id"] == "a" * 32
    assert inside["span_id"] == "b" * 16
    assert "trace_id" not in outside
    assert current_trace_context() is None


def test_trace_provider_hook_and_precedence(stream: io.StringIO) -> None:
    set_trace_context_provider(lambda: TraceContext(trace_id="c" * 32))
    log = get_logger("acm.test")
    log.info("from provider")
    with trace_context("d" * 32):
        log.info("explicit wins")
    log.info("explicit field wins", trace_id="e" * 32)
    provider, explicit, field = _lines(stream)
    assert provider["trace_id"] == "c" * 32
    assert "span_id" not in provider
    assert explicit["trace_id"] == "d" * 32
    assert field["trace_id"] == "e" * 32


def test_stdlib_logging_is_rendered_as_json(stream: io.StringIO) -> None:
    with trace_context("f" * 32):
        logging.getLogger("temporalio").warning("from %s", "stdlib")
    [line] = _lines(stream)
    assert line["event"] == "from stdlib"
    assert line["level"] == "warning"
    assert line["logger"] == "temporalio"
    assert line["trace_id"] == "f" * 32


def test_level_filtering() -> None:
    buffer = io.StringIO()
    configure_logging("WARNING", LogFormat.JSON, stream=buffer)
    try:
        log = get_logger("acm.test")
        log.info("dropped")
        log.warning("kept")
        assert [line["event"] for line in _lines(buffer)] == ["kept"]
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()


def test_exceptions_are_structured(stream: io.StringIO) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("acm.test").exception("failed")
    [line] = _lines(stream)
    assert line["exception"][0]["exc_type"] == "ValueError"


def test_bound_contextvars(stream: io.StringIO) -> None:
    structlog.contextvars.bind_contextvars(campaign_id="cmp_x")
    get_logger("acm.test").info("bound")
    [line] = _lines(stream)
    assert line["campaign_id"] == "cmp_x"


def test_console_format() -> None:
    buffer = io.StringIO()
    configure_logging("INFO", LogFormat.CONSOLE, stream=buffer)
    try:
        get_logger("acm.test").info("pretty")
        assert "pretty" in buffer.getvalue()
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()
