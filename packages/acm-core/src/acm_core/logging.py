"""Structured logging (structlog) with trace-id correlation.

Call ``configure_logging()`` once at process start (API, worker, CLI). It routes both
structlog loggers and the standard library ``logging`` module (temporalio, uvicorn,
SQLAlchemy, ...) through the same processor chain, so every line is one JSON object with
``timestamp``, ``level``, ``logger``, ``event`` and any bound fields.

Trace correlation: when a trace context is current, every log line gets ``trace_id`` and
``span_id``. The context comes from, in order:

1. ``trace_context(trace_id, span_id)`` — a context manager for code that receives a trace
   id explicitly (e.g. from a NATS message header or a Temporal header).
2. A provider registered with ``set_trace_context_provider()``. The telemetry package
   registers one that reads the active OpenTelemetry span, so ``acm_core`` does not depend
   on OpenTelemetry.

Bind request-scoped fields with ``structlog.contextvars.bind_contextvars(run_id=...)``.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Generator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import structlog
from structlog.typing import Processor

from acm_core.settings import LogFormat, LogLevel

__all__ = [
    "TraceContext",
    "TraceContextProvider",
    "add_trace_context",
    "configure_logging",
    "current_trace_context",
    "get_logger",
    "set_trace_context_provider",
    "trace_context",
]


@dataclass(frozen=True, slots=True)
class TraceContext:
    """W3C trace identifiers as lowercase hex (32 chars trace, 16 chars span)."""

    trace_id: str
    span_id: str | None = None


TraceContextProvider = Callable[[], TraceContext | None]
"""Returns the current trace context, or ``None`` when there is none."""

_explicit_trace: ContextVar[TraceContext | None] = ContextVar("acm_trace", default=None)
_provider: TraceContextProvider | None = None


def set_trace_context_provider(provider: TraceContextProvider | None) -> None:
    """Register (or with ``None``, remove) the fallback trace context provider."""
    global _provider
    _provider = provider


@contextmanager
def trace_context(trace_id: str, span_id: str | None = None) -> Generator[TraceContext]:
    """Make ``trace_id``/``span_id`` current for log lines emitted inside the block."""
    ctx = TraceContext(trace_id=trace_id, span_id=span_id)
    token = _explicit_trace.set(ctx)
    try:
        yield ctx
    finally:
        _explicit_trace.reset(token)


def current_trace_context() -> TraceContext | None:
    """The explicit trace context if set, else the provider's, else ``None``."""
    ctx = _explicit_trace.get()
    if ctx is not None:
        return ctx
    if _provider is not None:
        return _provider()
    return None


def add_trace_context(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor adding ``trace_id``/``span_id``; explicit fields win."""
    ctx = current_trace_context()
    if ctx is not None:
        event_dict.setdefault("trace_id", ctx.trace_id)
        if ctx.span_id is not None:
            event_dict.setdefault("span_id", ctx.span_id)
    return event_dict


def configure_logging(
    level: LogLevel = "INFO", fmt: LogFormat = LogFormat.JSON, *, stream: Any = None
) -> None:
    """Configure structlog and stdlib logging. Safe to call more than once.

    ``stream`` defaults to stderr; tests pass a ``StringIO``.
    """
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_trace_context,
        structlog.processors.StackInfoRenderer(),
    ]
    renderer: Processor
    if fmt is LogFormat.JSON:
        shared.append(structlog.processors.dict_tracebacks)
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str | None = None, **initial_values: Any) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger, optionally with fields bound to every line."""
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name, **initial_values)
    return logger
