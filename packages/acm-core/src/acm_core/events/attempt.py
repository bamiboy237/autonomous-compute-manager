"""Attempt state changes (``acm.attempt.<attempt_id>.state``, stream ``ACM_ATTEMPTS``)."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from acm_core.domain.incident import FailureClass
from acm_core.domain.provider import ProviderKind
from acm_core.domain.run import AttemptStatus
from acm_core.events import subjects
from acm_core.events.base import Event
from acm_core.events.streams import ATTEMPTS_STREAM
from acm_core.ids import AttemptId, RunId

__all__ = ["AttemptStateEvent"]


class AttemptStateEvent(Event):
    """An attempt moved to a new status, or its provider reported a new raw state.

    ``status`` follows ``ATTEMPT_STATE_MACHINE``. Provider adapters also publish when only
    ``provider_state`` changes (e.g. Slurm ``PENDING`` -> ``CONFIGURING``) with ``status``
    equal to ``previous_status``.
    """

    stream_name: ClassVar[str] = ATTEMPTS_STREAM

    v: Literal[1] = 1
    type: Literal["attempt.state"] = "attempt.state"
    attempt_id: AttemptId
    run_id: RunId
    status: AttemptStatus
    previous_status: AttemptStatus | None = None
    """``None`` for the first event of an attempt."""
    provider: ProviderKind | None = None
    provider_job_id: str | None = None
    """The provider's job reference (Slurm job id, Modal call id, ...)."""
    provider_state: str | None = None
    """Raw provider state, e.g. Slurm ``OUT_OF_MEMORY``."""
    failure_class: FailureClass | None = None
    """Set when the provider state already identifies the failure (OOM, preemption)."""
    message: str | None = Field(default=None, max_length=4096)

    def subject(self) -> str:
        return subjects.attempt_state(self.attempt_id)
