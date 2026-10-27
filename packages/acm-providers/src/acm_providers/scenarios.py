"""Workload behaviours the conformance suite asks an adapter's harness to provide."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Scenario"]


class Scenario(StrEnum):
    """What the job a harness builds must do. Every scenario writes at least 30 log lines to
    stdout when it starts, one per line, before doing anything else."""

    SUCCEED = "succeed"
    """Finish after a short while with exit code 0."""
    FAIL = "fail"
    """Finish after a short while with exit code 3."""
    RUN = "run"
    """Run until cancelled or out of walltime. On ``SIGTERM`` exit promptly. For each signal it
    receives (``SIGUSR1`` for ``checkpoint-now``) print ``received signal <name>`` where
    ``<name>`` is the ``JobSignal`` value."""
    IGNORE_TERM = "ignore-term"
    """Like ``RUN`` but ignores ``SIGTERM``, so it only stops when killed after the grace
    period."""
    OVERSIZED = "oversized"
    """A spec the provider cannot satisfy (more GPUs or walltime than it offers)."""
