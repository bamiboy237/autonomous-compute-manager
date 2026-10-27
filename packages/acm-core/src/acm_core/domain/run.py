"""Hypotheses, experiments, runs and attempts.

A campaign's planner grows a tree of ``Hypothesis`` nodes. Each ``Experiment`` tests one
hypothesis with a configuration (code version + params + resources). A ``Run`` is one
execution of an experiment toward completion; it owns the result. An ``Attempt`` is one
placement of a run on a provider; a run has several attempts when it is recovered from a
failure or moved between providers by checkpoint.

Run lifecycle (``RUN_STATE_MACHINE``)::

    pending     -> running | failed | stopped
    running     -> recovering | succeeded | failed | stopped
    recovering  -> running | failed | stopped
    succeeded, failed, stopped: terminal

``recovering`` means the run has no active attempt and the next one is being prepared
(after a failure, or while moving providers by checkpoint). ``stopped`` is a deliberate
halt (early stop, user stop), as opposed to ``failed``.

Attempt lifecycle (``ATTEMPT_STATE_MACHINE``)::

    pending     -> submitted | failed | cancelled
    submitted   -> running | cancelling | cancelled | failed | preempted
    running     -> succeeded | failed | preempted | cancelling | cancelled
    cancelling  -> cancelled | failed | succeeded
    succeeded, failed, preempted, cancelled: terminal

``pending`` means budget is reserved but the job is not yet at the provider; ``submitted``
means the provider accepted it (it may still be queued); ``cancelling`` means a cancel was
sent with a grace period and the job may still finish or fail first.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from acm_core.domain.common import DomainModel, Entity, utcnow
from acm_core.domain.decision import EvidenceRef
from acm_core.domain.incident import FailureClass
from acm_core.domain.state_machine import StateMachine
from acm_core.ids import (
    AttemptId,
    CampaignId,
    CheckpointId,
    CodeVersionId,
    ComputeTargetId,
    ExperimentId,
    HypothesisId,
    ProviderAccountId,
    RunId,
)

__all__ = [
    "ATTEMPT_STATE_MACHINE",
    "RUN_STATE_MACHINE",
    "Attempt",
    "AttemptStatus",
    "Experiment",
    "ExperimentStatus",
    "Hypothesis",
    "HypothesisStatus",
    "ResourceRequest",
    "Run",
    "RunStatus",
]


class HypothesisStatus(StrEnum):
    OPEN = "open"
    """Proposed, not yet tested."""
    TESTING = "testing"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    ABANDONED = "abandoned"
    """Dropped without a conclusive result (cost, priority)."""


class Hypothesis(Entity):
    """A node in the campaign's hypothesis tree."""

    id: HypothesisId = Field(default_factory=HypothesisId.new)
    campaign_id: CampaignId
    parent_id: HypothesisId | None = None
    statement: str = Field(min_length=1)
    """What is believed, e.g. "a 2x larger fingerprint encoder improves MRR@25"."""
    rationale: str = Field(min_length=1)
    status: HypothesisStatus = HypothesisStatus.OPEN
    evidence: list[EvidenceRef] = Field(default_factory=list[EvidenceRef])


class ResourceRequest(DomainModel):
    """Provider-neutral resources for one attempt."""

    gpus: int = Field(default=0, ge=0)
    gpu_type: str | None = None
    """Required GPU model; ``None`` accepts any (placement picks)."""
    cpus: int = Field(default=1, ge=1)
    memory_gb: int = Field(default=4, ge=1)
    walltime: timedelta = Field(default=timedelta(hours=1), gt=timedelta(0))

    @model_validator(mode="after")
    def _check_gpu_type(self) -> Self:
        if self.gpu_type is not None and self.gpus == 0:
            raise ValueError("gpu_type is set but gpus is 0")
        return self


class ExperimentStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class Experiment(Entity):
    """A configuration testing one hypothesis."""

    id: ExperimentId = Field(default_factory=ExperimentId.new)
    campaign_id: CampaignId
    hypothesis_id: HypothesisId
    code_version_id: CodeVersionId
    name: str = Field(min_length=1, max_length=200)
    params: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    """Workload hyperparameters, passed to the entrypoint."""
    resources: ResourceRequest = Field(default_factory=ResourceRequest)
    status: ExperimentStatus = ExperimentStatus.PLANNED


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RECOVERING = "recovering"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"


RUN_STATE_MACHINE: StateMachine[RunStatus] = StateMachine(
    "run",
    RunStatus,
    initial=RunStatus.PENDING,
    transitions={
        RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.STOPPED},
        RunStatus.RUNNING: {
            RunStatus.RECOVERING,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.STOPPED,
        },
        RunStatus.RECOVERING: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.STOPPED},
        RunStatus.SUCCEEDED: set(),
        RunStatus.FAILED: set(),
        RunStatus.STOPPED: set(),
    },
)


class Run(Entity):
    """One execution of an experiment toward completion."""

    id: RunId = Field(default_factory=RunId.new)
    campaign_id: CampaignId
    experiment_id: ExperimentId
    status: RunStatus = RunStatus.PENDING
    current_attempt_id: AttemptId | None = None
    final_metrics: dict[str, float] = Field(default_factory=dict[str, float])
    """Final values reported by the workload or evaluator, keyed by metric name."""
    stop_reason: str | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None

    def transition_to(
        self, status: RunStatus, *, reason: str | None = None, at: datetime | None = None
    ) -> Run:
        """Return a copy in ``status``; raises ``InvalidTransitionError`` if not allowed.

        Sets ``started_at`` on the first move to ``running`` and ``finished_at`` on any
        terminal status. ``reason`` is recorded as ``stop_reason`` for ``stopped``/``failed``.
        """
        RUN_STATE_MACHINE.check(self.status, status)
        now = at or utcnow()
        update: dict[str, object] = {"status": status, "updated_at": now}
        if status is RunStatus.RUNNING and self.started_at is None:
            update["started_at"] = now
        if RUN_STATE_MACHINE.is_terminal(status):
            update["finished_at"] = now
            update["current_attempt_id"] = None
        if status in (RunStatus.STOPPED, RunStatus.FAILED) and reason is not None:
            update["stop_reason"] = reason
        return self.model_copy(update=update)


class AttemptStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PREEMPTED = "preempted"
    CANCELLED = "cancelled"


ATTEMPT_STATE_MACHINE: StateMachine[AttemptStatus] = StateMachine(
    "attempt",
    AttemptStatus,
    initial=AttemptStatus.PENDING,
    transitions={
        AttemptStatus.PENDING: {
            AttemptStatus.SUBMITTED,
            AttemptStatus.FAILED,
            AttemptStatus.CANCELLED,
        },
        AttemptStatus.SUBMITTED: {
            AttemptStatus.RUNNING,
            AttemptStatus.CANCELLING,
            AttemptStatus.CANCELLED,
            AttemptStatus.FAILED,
            AttemptStatus.PREEMPTED,
        },
        AttemptStatus.RUNNING: {
            AttemptStatus.SUCCEEDED,
            AttemptStatus.FAILED,
            AttemptStatus.PREEMPTED,
            AttemptStatus.CANCELLING,
            AttemptStatus.CANCELLED,
        },
        AttemptStatus.CANCELLING: {
            AttemptStatus.CANCELLED,
            AttemptStatus.FAILED,
            AttemptStatus.SUCCEEDED,
        },
        AttemptStatus.SUCCEEDED: set(),
        AttemptStatus.FAILED: set(),
        AttemptStatus.PREEMPTED: set(),
        AttemptStatus.CANCELLED: set(),
    },
)


class Attempt(Entity):
    """One placement of a run on a provider target."""

    id: AttemptId = Field(default_factory=AttemptId.new)
    run_id: RunId
    ordinal: int = Field(ge=1)
    """1 for the run's first attempt, incremented per new placement."""
    provider_account_id: ProviderAccountId
    compute_target_id: ComputeTargetId
    resources: ResourceRequest
    status: AttemptStatus = AttemptStatus.PENDING
    provider_job_ref: str | None = None
    """Provider's job handle (Slurm job id, Modal call id, ...), set on submission."""
    resumed_from_checkpoint_id: CheckpointId | None = None
    failure_class: FailureClass | None = None
    exit_code: int | None = None
    submitted_at: AwareDatetime | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None

    def transition_to(
        self,
        status: AttemptStatus,
        *,
        provider_job_ref: str | None = None,
        failure_class: FailureClass | None = None,
        exit_code: int | None = None,
        at: datetime | None = None,
    ) -> Attempt:
        """Return a copy in ``status``; raises ``InvalidTransitionError`` if not allowed.

        Stamps ``submitted_at``/``started_at``/``finished_at``. ``provider_job_ref`` is
        recorded on ``submitted``; ``failure_class`` and ``exit_code`` when given.
        """
        ATTEMPT_STATE_MACHINE.check(self.status, status)
        now = at or utcnow()
        update: dict[str, object] = {"status": status, "updated_at": now}
        if status is AttemptStatus.SUBMITTED:
            update["submitted_at"] = now
            if provider_job_ref is not None:
                update["provider_job_ref"] = provider_job_ref
        elif status is AttemptStatus.RUNNING:
            update["started_at"] = now
        if ATTEMPT_STATE_MACHINE.is_terminal(status):
            update["finished_at"] = now
        if failure_class is not None:
            update["failure_class"] = failure_class
        if exit_code is not None:
            update["exit_code"] = exit_code
        return self.model_copy(update=update)
