"""The provider-neutral contract every compute adapter implements (ARCHITECTURE.md §6).

Adapters translate :class:`JobSpec` into their native submission, and native states into the
normalized :class:`JobState` (keeping the native string in :attr:`JobStatus.raw_state`).
Every model is frozen and rejects unknown fields (see ``acm_core.domain.DomainModel``).

Contract summary (the conformance suite in ``acm_providers.testing`` enforces it):

* ``submit`` is idempotent on ``JobSpec.idempotency_key``: the same key and spec returns the
  same ref and never starts a second job; the same key with a different spec raises
  ``IdempotencyConflictError``.
* ``status``/``cancel``/``signal``/``logs``/``accounting`` on a ref the provider does not know
  raise ``JobNotFoundError``.
* ``cancel`` is idempotent: cancelling a finished or already-cancelling job is a no-op.
* ``signal`` raises ``UnsupportedSignalError`` for signals outside ``Capabilities.signals`` and
  ``JobNotActiveError`` when the job is not running.
* ``logs`` is paginated by line offset; reading past the end returns an empty chunk.
* ``accounting`` works at any time; ``UsageRecord.is_final`` says whether the job has finished.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Protocol, Self, runtime_checkable

from pydantic import AwareDatetime, Field, JsonValue, StringConstraints, model_validator

from acm_core.domain import (
    AttemptStatus,
    DomainModel,
    FailureClass,
    Money,
    ProviderKind,
    ResourceRequest,
)
from acm_core.ids import CodeVersionId

__all__ = [
    "Capabilities",
    "CheckpointConfig",
    "DataMount",
    "GpuType",
    "JobSignal",
    "JobSpec",
    "JobState",
    "JobStatus",
    "LogChunk",
    "LogLine",
    "PricingModel",
    "Provider",
    "ProviderJobRef",
    "QueueSnapshot",
    "Resources",
    "TargetQueue",
    "UsageRecord",
]

_TOKEN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
IdempotencyKey = Annotated[str, StringConstraints(pattern=_TOKEN, min_length=8, max_length=128)]
"""Caller-chosen key naming one submission (e.g. ``att_<ULID>``). Retrying with it is safe."""


class JobState(StrEnum):
    """Normalized job state, the same for every provider."""

    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    """Cancel requested, the job is still shutting down (inside the grace period)."""
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PREEMPTED = "preempted"
    TIMED_OUT = "timed_out"
    """Killed for exceeding walltime."""

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATES

    @property
    def attempt_status(self) -> AttemptStatus:
        """The ``Attempt`` status this state maps to (``timed_out`` is a failure)."""
        return _ATTEMPT_STATUS[self]


_TERMINAL_STATES = frozenset(
    {
        JobState.SUCCEEDED,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.PREEMPTED,
        JobState.TIMED_OUT,
    }
)

_ATTEMPT_STATUS: dict[JobState, AttemptStatus] = {
    JobState.QUEUED: AttemptStatus.SUBMITTED,
    JobState.RUNNING: AttemptStatus.RUNNING,
    JobState.CANCELLING: AttemptStatus.CANCELLING,
    JobState.SUCCEEDED: AttemptStatus.SUCCEEDED,
    JobState.FAILED: AttemptStatus.FAILED,
    JobState.CANCELLED: AttemptStatus.CANCELLED,
    JobState.PREEMPTED: AttemptStatus.PREEMPTED,
    JobState.TIMED_OUT: AttemptStatus.FAILED,
}


class JobSignal(StrEnum):
    """Signals the platform sends to running jobs."""

    CHECKPOINT_NOW = "checkpoint-now"
    """Ask the workload to checkpoint; delivered as ``SIGUSR1`` unless the adapter does better."""
    USR1 = "USR1"
    USR2 = "USR2"
    TERM = "TERM"
    INT = "INT"
    HUP = "HUP"


class PricingModel(StrEnum):
    FREE = "free"
    """No charge (local machines, the simulator)."""
    ALLOCATION = "allocation"
    """Charged against an allocation (Slurm service units)."""
    PER_SECOND = "per_second"
    PER_HOUR = "per_hour"


class Resources(ResourceRequest):
    """Resources for one job: the shared ``ResourceRequest`` plus provider-level detail."""

    nodes: int = Field(default=1, ge=1)
    gpu_memory_gb: int | None = Field(default=None, gt=0)
    """Minimum memory per GPU; narrows the GPU choice when ``gpu_type`` is unset."""


class DataMount(DomainModel):
    """An object-store prefix made available inside the job."""

    uri: str = Field(min_length=1)
    """Object-store URI, e.g. ``s3://acm/datasets/cifar``."""
    path: str = Field(pattern=r"^/")
    """Absolute path inside the job."""
    read_only: bool = True


class CheckpointConfig(DomainModel):
    """Where and how the workload checkpoints, and what to resume from."""

    uri: str = Field(min_length=1)
    """Object-store prefix the workload writes checkpoints to."""
    interval: timedelta | None = Field(default=None, gt=timedelta(0))
    signal: JobSignal = JobSignal.CHECKPOINT_NOW
    """Signal that asks the workload to checkpoint now."""
    resume_from: str | None = None
    """Checkpoint manifest URI to resume from, if any."""
    walltime_warning: timedelta | None = Field(default=None, gt=timedelta(0))
    """Send ``signal`` this long before walltime (Slurm: ``--signal=USR1@<s>``)."""


class JobSpec(DomainModel):
    """Everything an adapter needs to start one job. Provider-neutral."""

    name: str = Field(min_length=1, max_length=200)
    idempotency_key: IdempotencyKey
    command: tuple[str, ...] = Field(min_length=1)
    image: str | None = None
    """Container image; ``None`` runs in the target's default environment."""
    code_version_id: CodeVersionId | None = None
    resources: Resources = Field(default_factory=Resources)
    target: str | None = None
    """Name of the ``ComputeTarget`` (partition, GPU pool) to run on; ``None`` lets the
    adapter choose."""
    mounts: tuple[DataMount, ...] = ()
    checkpoint: CheckpointConfig | None = None
    env: dict[str, str] = Field(default_factory=dict[str, str])
    labels: dict[str, str] = Field(default_factory=dict[str, str])
    """Free-form tags the adapter attaches to the native job (run id, campaign id, ...)."""

    @model_validator(mode="after")
    def _unique_mount_paths(self) -> Self:
        paths = [mount.path for mount in self.mounts]
        if len(set(paths)) != len(paths):
            raise ValueError("data mounts must have distinct paths")
        return self

    def fingerprint(self) -> str:
        """Hash of everything except the idempotency key.

        Adapters compare it to detect a reused key with a different spec.
        """
        body = self.model_dump_json(exclude={"idempotency_key"}, round_trip=True)
        return "sha256:" + hashlib.sha256(body.encode()).hexdigest()

    def runtime_env(self) -> dict[str, str]:
        """``env`` plus the ``ACM_*`` variables every adapter must set in the job.

        ``ACM_IDEMPOTENCY_KEY`` always; ``ACM_WALLTIME_SECONDS`` always; with a checkpoint
        config also ``ACM_CHECKPOINT_URI``, ``ACM_CHECKPOINT_SIGNAL`` and, when resuming,
        ``ACM_RESUME_FROM``. These override same-named entries in ``env``.
        """
        env = dict(self.env)
        env["ACM_IDEMPOTENCY_KEY"] = self.idempotency_key
        env["ACM_WALLTIME_SECONDS"] = str(int(self.resources.walltime.total_seconds()))
        if self.checkpoint is not None:
            env["ACM_CHECKPOINT_URI"] = self.checkpoint.uri
            env["ACM_CHECKPOINT_SIGNAL"] = self.checkpoint.signal.value
            if self.checkpoint.resume_from is not None:
                env["ACM_RESUME_FROM"] = self.checkpoint.resume_from
        return env


class ProviderJobRef(DomainModel):
    """Handle to a submitted job. Stored on ``Attempt.provider_job_ref`` as ``str(ref)``."""

    kind: ProviderKind
    account: str = Field(pattern=_TOKEN, max_length=200)
    """Provider account name or id; keeps refs from different accounts apart."""
    job_id: str = Field(min_length=1, max_length=500)
    """The provider's own id (Slurm job id, Modal call id, pod name, pid)."""

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.account}:{self.job_id}"

    @classmethod
    def parse(cls, value: str) -> Self:
        """Inverse of ``str(ref)``; raises ``ValueError`` on a malformed value."""
        kind, sep1, rest = value.partition(":")
        account, sep2, job_id = rest.partition(":")
        if not (sep1 and sep2):
            raise ValueError(f"malformed provider job ref: {value!r}")
        return cls(kind=ProviderKind(kind), account=account, job_id=job_id)


class JobStatus(DomainModel):
    """Point-in-time status of a job."""

    ref: ProviderJobRef
    state: JobState
    raw_state: str
    """The provider's native state, verbatim (``PENDING``, ``OOMKilled``, ...)."""
    exit_code: int | None = None
    failure_class: FailureClass | None = None
    """Set when the provider knows why (OOM, node failure, walltime, preemption)."""
    message: str | None = None
    node: str | None = None
    submitted_at: AwareDatetime | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _finished_only_when_terminal(self) -> Self:
        if self.finished_at is not None and not self.state.is_terminal:
            raise ValueError(f"finished_at is set but state is {self.state.value}")
        return self


class LogLine(DomainModel):
    offset: int = Field(ge=0)
    """Zero-based line number in the job's combined log."""
    text: str
    stream: str = "stdout"
    timestamp: AwareDatetime | None = None


class LogChunk(DomainModel):
    """A page of log lines starting at the requested offset."""

    lines: tuple[LogLine, ...] = ()
    next_offset: int = Field(ge=0)
    """Pass as ``since`` to get the following lines."""
    complete: bool = False
    """True when the job is finished and no lines remain beyond ``next_offset``."""

    @model_validator(mode="after")
    def _contiguous(self) -> Self:
        for expected, line in enumerate(
            self.lines, start=self.lines[0].offset if self.lines else 0
        ):
            if line.offset != expected:
                raise ValueError("log lines must have consecutive offsets")
        if self.lines and self.next_offset != self.lines[-1].offset + 1:
            raise ValueError("next_offset must follow the last line")
        return self


class UsageRecord(DomainModel):
    """What a job consumed. Provisional until ``is_final``."""

    ref: ProviderJobRef
    is_final: bool
    wall_time: timedelta = timedelta(0)
    gpus: int = Field(default=0, ge=0)
    gpu_hours: Decimal = Field(default=Decimal(0), ge=0)
    gpu_hour_norm: Decimal = Field(default=Decimal(0), ge=0)
    """GPU-hours normalized to an A100-80G = 1.0 reference."""
    cost: Money | None = None
    """Cash or allocation cost; ``None`` when the provider cannot price the job."""
    raw: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    """Provider accounting fields, verbatim (``sacct`` columns, Modal billing)."""


class TargetQueue(DomainModel):
    """Load on one target (partition, GPU pool)."""

    target: str
    pending_jobs: int = Field(ge=0)
    running_jobs: int = Field(ge=0)
    free_gpus: int | None = Field(default=None, ge=0)
    total_gpus: int | None = Field(default=None, ge=0)
    median_wait: timedelta | None = None
    """Observed median queue wait, if the provider reports it."""


class QueueSnapshot(DomainModel):
    """Input to queue-wait prediction (placement engine)."""

    taken_at: AwareDatetime
    targets: tuple[TargetQueue, ...] = ()


class GpuType(DomainModel):
    name: str = Field(min_length=1)
    """Normalized model name, e.g. ``a100-80gb``."""
    memory_gb: int = Field(gt=0)
    max_count: int = Field(default=1, ge=1)
    """Most GPUs of this type one job may use."""


class Capabilities(DomainModel):
    """What a provider can do. Declarative: the placement engine reads it, adapters never branch
    on it at call time except to reject unsupported requests."""

    kind: ProviderKind
    gpu_types: tuple[GpuType, ...] = ()
    max_walltime: timedelta | None = None
    max_cpus: int | None = Field(default=None, ge=1)
    max_memory_gb: int | None = Field(default=None, ge=1)
    targets: tuple[str, ...] = ()
    """Target names ``JobSpec.target`` may name; empty when the provider has one pool."""
    preemptible: bool = False
    shared_filesystem: bool = False
    network_egress: bool = True
    pricing_model: PricingModel = PricingModel.FREE
    signals: frozenset[JobSignal] = frozenset()
    """Signals ``signal()`` can deliver."""
    supports_images: bool = False
    """Whether ``JobSpec.image`` is honored."""

    def mismatches(self, spec: JobSpec) -> list[str]:
        """Reasons ``spec`` cannot run here; empty when it fits."""
        problems: list[str] = []
        res = spec.resources
        if spec.target is not None and self.targets and spec.target not in self.targets:
            problems.append(f"unknown target {spec.target!r}")
        if spec.image is not None and not self.supports_images:
            problems.append("container images are not supported")
        if self.max_walltime is not None and res.walltime > self.max_walltime:
            problems.append(f"walltime {res.walltime} exceeds maximum {self.max_walltime}")
        if self.max_cpus is not None and res.cpus > self.max_cpus:
            problems.append(f"{res.cpus} cpus exceeds maximum {self.max_cpus}")
        if self.max_memory_gb is not None and res.memory_gb > self.max_memory_gb:
            problems.append(f"{res.memory_gb} GB memory exceeds maximum {self.max_memory_gb}")
        if res.gpus > 0 and not self._gpu_fits(res):
            problems.append(
                f"no GPU type fits {res.gpus}x {res.gpu_type or 'any'}"
                + (f" with {res.gpu_memory_gb} GB" if res.gpu_memory_gb else "")
            )
        return problems

    def fits(self, spec: JobSpec) -> bool:
        return not self.mismatches(spec)

    def _gpu_fits(self, res: Resources) -> bool:
        return any(
            (res.gpu_type is None or gpu.name == res.gpu_type)
            and (res.gpu_memory_gb is None or gpu.memory_gb >= res.gpu_memory_gb)
            and gpu.max_count >= res.gpus
            for gpu in self.gpu_types
        )


@runtime_checkable
class Provider(Protocol):
    """A compute backend. All methods are async; see the module docstring for the contract."""

    kind: ProviderKind

    async def capabilities(self) -> Capabilities: ...

    async def submit(self, spec: JobSpec) -> ProviderJobRef:
        """Start ``spec``. Idempotent on ``spec.idempotency_key``.

        Raises ``CapabilityMismatchError`` if the spec does not fit ``capabilities()``.
        """
        ...

    async def status(self, ref: ProviderJobRef) -> JobStatus: ...

    async def cancel(self, ref: ProviderJobRef, *, grace: timedelta) -> None:
        """Ask the job to stop: ``TERM`` now, kill after ``grace``. Idempotent."""
        ...

    async def signal(self, ref: ProviderJobRef, sig: JobSignal) -> None: ...

    async def logs(self, ref: ProviderJobRef, *, since: int = 0, limit: int = 1000) -> LogChunk:
        """Lines from line offset ``since`` (at most ``limit``). Raises ``InvalidInputError``
        for a negative ``since`` or a non-positive ``limit``."""
        ...

    async def accounting(self, ref: ProviderJobRef) -> UsageRecord: ...

    async def queue_snapshot(self) -> QueueSnapshot: ...

    async def close(self) -> None:
        """Release connections. Safe to call more than once."""
        ...
