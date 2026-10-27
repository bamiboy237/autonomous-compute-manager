"""In-memory provider driven by an injectable clock; the reference implementation of the contract.

Time only moves when the clock does, so tests are deterministic. A job's behaviour comes from
its command: ``("fake-workload", <Scenario value>)``. Use it in unit tests of code that needs a
provider, and as a model when writing an adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from acm_core.domain import Currency, FailureClass, Money, ProviderKind, utcnow
from acm_core.errors import InvalidInputError
from acm_providers.base import (
    Capabilities,
    GpuType,
    JobSignal,
    JobSpec,
    JobState,
    JobStatus,
    LogChunk,
    LogLine,
    PricingModel,
    ProviderJobRef,
    QueueSnapshot,
    TargetQueue,
    UsageRecord,
)
from acm_providers.errors import (
    CapabilityMismatchError,
    IdempotencyConflictError,
    JobNotActiveError,
    JobNotFoundError,
    UnsupportedSignalError,
)
from acm_providers.registry import BuildContext, ProviderRegistry
from acm_providers.scenarios import Scenario

__all__ = ["FakeProvider", "FakeSettings", "ManualClock", "register_fake"]

WORKLOAD = "fake-workload"
_STARTUP_LINES = 30


class ManualClock:
    """A clock that only moves when told to."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or utcnow()

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class FakeSettings(BaseModel):
    """``ProviderAccount.config`` for the fake provider."""

    model_config = ConfigDict(extra="forbid")

    total_gpus: int = Field(default=8, ge=0)
    queue_delay_seconds: float = Field(default=2, ge=0)
    run_seconds: float = Field(default=5, gt=0)
    price_per_gpu_hour: Decimal = Field(default=Decimal(2), ge=0)


@dataclass(slots=True)
class _Job:
    ref: ProviderJobRef
    spec: JobSpec
    fingerprint: str
    scenario: Scenario
    submitted_at: datetime
    state: JobState = JobState.QUEUED
    raw_state: str = "PENDING"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    failure_class: FailureClass | None = None
    kill_at: datetime | None = None
    lines: list[tuple[datetime, str]] = field(default_factory=list[tuple[datetime, str]])

    def finish(
        self,
        state: JobState,
        raw: str,
        at: datetime,
        *,
        exit_code: int | None = None,
        failure_class: FailureClass | None = None,
    ) -> None:
        self.state, self.raw_state, self.finished_at = state, raw, at
        self.exit_code, self.failure_class = exit_code, failure_class
        self.kill_at = None


class FakeProvider:
    kind = ProviderKind.SIMULATOR

    def __init__(
        self,
        settings: FakeSettings | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        account: str = "fake",
        signals: frozenset[JobSignal] = frozenset(JobSignal),
    ) -> None:
        self._settings = settings or FakeSettings()
        self._clock: Callable[[], datetime] = clock or utcnow
        self._account = account
        self._jobs: dict[str, _Job] = {}
        self._by_key: dict[str, _Job] = {}
        self._capabilities = Capabilities(
            kind=self.kind,
            gpu_types=(
                GpuType(name="a100-80gb", memory_gb=80, max_count=self._settings.total_gpus),
            ),
            max_walltime=timedelta(hours=24),
            targets=("default",),
            preemptible=True,
            pricing_model=PricingModel.PER_HOUR,
            signals=signals,
        )

    async def capabilities(self) -> Capabilities:
        return self._capabilities

    async def submit(self, spec: JobSpec) -> ProviderJobRef:
        fingerprint = spec.fingerprint()
        existing = self._by_key.get(spec.idempotency_key)
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise IdempotencyConflictError(
                    "idempotency key was used with a different spec",
                    details={"idempotency_key": spec.idempotency_key},
                )
            return existing.ref
        problems = self._capabilities.mismatches(spec)
        if len(spec.command) != 2 or spec.command[0] != WORKLOAD:
            problems.append(f"command must be ({WORKLOAD!r}, <scenario>)")
        scenario = self._scenario(spec)
        if scenario is None:
            problems.append("unknown scenario")
        if problems or scenario is None:
            raise CapabilityMismatchError(
                "; ".join(problems), details={"problems": list[JsonValue](problems)}
            )
        ref = ProviderJobRef(
            kind=self.kind, account=self._account, job_id=f"job-{len(self._jobs) + 1}"
        )
        job = _Job(ref, spec, fingerprint, scenario, self._clock())
        self._jobs[ref.job_id] = job
        self._by_key[spec.idempotency_key] = job
        return ref

    async def status(self, ref: ProviderJobRef) -> JobStatus:
        job = self._advance(ref)
        return JobStatus(
            ref=job.ref,
            state=job.state,
            raw_state=job.raw_state,
            exit_code=job.exit_code,
            failure_class=job.failure_class,
            node="fake-node-1" if job.started_at else None,
            submitted_at=job.submitted_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )

    async def cancel(self, ref: ProviderJobRef, *, grace: timedelta) -> None:
        job = self._advance(ref)
        if job.state.is_terminal or job.state is JobState.CANCELLING:
            return
        now = self._clock()
        if job.state is JobState.QUEUED:
            job.finish(JobState.CANCELLED, "CANCELLED", now)
        elif job.scenario is Scenario.IGNORE_TERM and grace > timedelta(0):
            self._log(job, now, "ignoring SIGTERM")
            job.state, job.raw_state, job.kill_at = JobState.CANCELLING, "COMPLETING", now + grace
        else:
            self._log(job, now, "received SIGTERM, exiting")
            job.finish(JobState.CANCELLED, "CANCELLED", now, exit_code=143)

    async def signal(self, ref: ProviderJobRef, sig: JobSignal) -> None:
        job = self._advance(ref)
        if sig not in self._capabilities.signals:
            raise UnsupportedSignalError(f"signal {sig.value!r} is not supported")
        if job.state is not JobState.RUNNING:
            raise JobNotActiveError(f"job is {job.state.value}, not running")
        self._log(job, self._clock(), f"received signal {sig.value}")

    async def logs(self, ref: ProviderJobRef, *, since: int = 0, limit: int = 1000) -> LogChunk:
        if since < 0 or limit < 1:
            raise InvalidInputError("since must be >= 0 and limit >= 1")
        job = self._advance(ref)
        total = len(job.lines)
        start = min(since, total)
        page = job.lines[start : start + limit]
        lines = tuple(
            LogLine(offset=start + i, text=text, timestamp=at) for i, (at, text) in enumerate(page)
        )
        next_offset = start + len(page)
        return LogChunk(
            lines=lines,
            next_offset=next_offset,
            complete=job.state.is_terminal and next_offset >= total,
        )

    async def accounting(self, ref: ProviderJobRef) -> UsageRecord:
        job = self._advance(ref)
        wall = timedelta(0)
        if job.started_at is not None:
            wall = (job.finished_at or self._clock()) - job.started_at
        gpus = job.spec.resources.gpus
        gpu_hours = Decimal(gpus) * Decimal(wall.total_seconds()) / Decimal(3600)
        return UsageRecord(
            ref=job.ref,
            is_final=job.state.is_terminal,
            wall_time=wall,
            gpus=gpus,
            gpu_hours=gpu_hours,
            gpu_hour_norm=gpu_hours,
            cost=Money(currency=Currency.USD, amount=gpu_hours * self._settings.price_per_gpu_hour),
            raw={"state": job.raw_state},
        )

    async def queue_snapshot(self) -> QueueSnapshot:
        jobs = [self._advance(j.ref) for j in self._jobs.values()]
        active = [j for j in jobs if not j.state.is_terminal]
        in_use = sum(j.spec.resources.gpus for j in active if j.state is not JobState.QUEUED)
        return QueueSnapshot(
            taken_at=self._clock(),
            targets=(
                TargetQueue(
                    target="default",
                    pending_jobs=sum(j.state is JobState.QUEUED for j in active),
                    running_jobs=sum(j.state is not JobState.QUEUED for j in active),
                    free_gpus=max(self._settings.total_gpus - in_use, 0),
                    total_gpus=self._settings.total_gpus,
                ),
            ),
        )

    async def close(self) -> None:
        return None

    def preempt(self, ref: ProviderJobRef) -> None:
        """Test hook: the provider reclaims the node under a running job."""
        job = self._advance(ref)
        if not job.state.is_terminal:
            job.finish(
                JobState.PREEMPTED, "PREEMPTED", self._clock(), failure_class=FailureClass.PREEMPTED
            )

    @staticmethod
    def _scenario(spec: JobSpec) -> Scenario | None:
        try:
            return Scenario(spec.command[1])
        except ValueError:
            return None

    @staticmethod
    def _log(job: _Job, at: datetime, text: str) -> None:
        job.lines.append((at, text))

    def _job(self, ref: ProviderJobRef) -> _Job:
        job = self._jobs.get(ref.job_id)
        if job is None or job.ref != ref:
            raise JobNotFoundError(f"unknown job {ref}", details={"ref": str(ref)})
        return job

    def _advance(self, ref: ProviderJobRef) -> _Job:
        """Bring the job's state up to the clock's now."""
        job = self._job(ref)
        now = self._clock()
        if job.state is JobState.QUEUED:
            start = job.submitted_at + timedelta(seconds=self._settings.queue_delay_seconds)
            if now < start:
                return job
            job.state, job.raw_state, job.started_at = JobState.RUNNING, "RUNNING", start
            for i in range(_STARTUP_LINES):
                self._log(job, start, f"step {i}")
        if job.state is JobState.CANCELLING and job.kill_at is not None and now >= job.kill_at:
            job.finish(JobState.CANCELLED, "CANCELLED", job.kill_at, exit_code=137)
        if job.state is JobState.RUNNING and job.started_at is not None:
            walltime_at = job.started_at + job.spec.resources.walltime
            done_at = job.started_at + timedelta(seconds=self._settings.run_seconds)
            ends = job.scenario in (Scenario.SUCCEED, Scenario.FAIL)
            if ends and done_at <= min(now, walltime_at):
                if job.scenario is Scenario.SUCCEED:
                    job.finish(JobState.SUCCEEDED, "COMPLETED", done_at, exit_code=0)
                else:
                    job.finish(JobState.FAILED, "FAILED", done_at, exit_code=3)
            elif walltime_at <= now:
                job.finish(
                    JobState.TIMED_OUT,
                    "TIMEOUT",
                    walltime_at,
                    failure_class=FailureClass.WALLTIME_EXCEEDED,
                )
        return job


def register_fake(registry: ProviderRegistry) -> None:
    """Register ``FakeProvider`` as the ``simulator`` kind (tests; the real simulator adapter
    replaces it)."""

    def build(ctx: BuildContext[FakeSettings]) -> FakeProvider:
        return FakeProvider(ctx.settings, account=str(ctx.account.id))

    registry.register(ProviderKind.SIMULATOR, FakeSettings, build)
