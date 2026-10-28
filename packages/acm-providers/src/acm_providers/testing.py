"""Conformance suite every provider adapter must pass.

Subclass :class:`ProviderConformance` in the adapter's test module and override two fixtures::

    class TestSlurmConformance(ProviderConformance):
        @pytest.fixture
        async def provider(self) -> AsyncIterator[Provider]: ...

        @pytest.fixture
        def harness(self) -> ProviderHarness: ...

The *harness* is the adapter's side of the deal: it builds job specs that behave as each
:class:`~acm_providers.scenarios.Scenario` describes, and lets time pass. The suite never
sleeps itself, so simulated providers run instantly and real ones poll at the harness' pace.
Adapters that cannot support a scenario leave it out of ``harness.scenarios`` (the dependent
tests skip); everything else is mandatory.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Protocol

import pytest

from acm_core.domain import AttemptStatus
from acm_core.errors import InvalidInputError
from acm_providers.base import (
    JobSignal,
    JobSpec,
    JobState,
    JobStatus,
    LogLine,
    Provider,
    ProviderJobRef,
    Resources,
)
from acm_providers.errors import (
    CapabilityMismatchError,
    IdempotencyConflictError,
    JobNotActiveError,
    JobNotFoundError,
    UnsupportedSignalError,
)
from acm_providers.fake import WORKLOAD, FakeProvider, ManualClock
from acm_providers.scenarios import Scenario

__all__ = ["FakeHarness", "ProviderConformance", "ProviderHarness"]

MAX_TICKS = 600


class ProviderHarness(Protocol):
    scenarios: frozenset[Scenario]
    """Scenarios this adapter can run; ``OVERSIZED`` and the ones used by mandatory tests
    (``SUCCEED``, ``FAIL``, ``RUN``) must be included."""
    grace: timedelta
    """Grace period the suite passes to ``cancel`` (short, e.g. one second)."""

    def spec(self, scenario: Scenario, idempotency_key: str) -> JobSpec: ...

    async def tick(self) -> None:
        """Let time pass: advance a simulated clock, or sleep a poll interval."""
        ...


class FakeHarness:
    """Harness for ``FakeProvider``: also the template for writing one."""

    scenarios = frozenset(Scenario)
    grace = timedelta(seconds=3)

    def __init__(self, clock: ManualClock) -> None:
        self._clock = clock

    def spec(self, scenario: Scenario, idempotency_key: str) -> JobSpec:
        oversized = scenario is Scenario.OVERSIZED
        return JobSpec(
            name=f"conformance-{scenario.value}",
            idempotency_key=idempotency_key,
            command=(WORKLOAD, scenario.value),
            target="default",
            resources=Resources(gpus=10_000 if oversized else 1, gpu_type="a100-80gb"),
        )

    async def tick(self) -> None:
        self._clock.advance(timedelta(seconds=1))


def make_fake(
    signals: frozenset[JobSignal] = frozenset(JobSignal),
) -> tuple[FakeProvider, FakeHarness]:
    clock = ManualClock()
    return FakeProvider(clock=clock, signals=signals), FakeHarness(clock)


class ProviderConformance:
    """Behavioural contract tests. Override the ``provider`` and ``harness`` fixtures."""

    @pytest.fixture
    def provider(self) -> Any:
        """Override: return (or yield) a ready ``Provider``; close it on teardown."""
        raise NotImplementedError("override the `provider` fixture")

    @pytest.fixture
    def harness(self) -> Any:
        """Override: return the adapter's ``ProviderHarness``."""
        raise NotImplementedError("override the `harness` fixture")

    # -- helpers ---------------------------------------------------------------------------

    @staticmethod
    def _key() -> str:
        return f"conf-{uuid.uuid4().hex}"

    @staticmethod
    def _need(harness: ProviderHarness, scenario: Scenario) -> None:
        if scenario not in harness.scenarios:
            pytest.skip(f"harness does not provide scenario {scenario.value!r}")

    @staticmethod
    async def _wait(
        provider: Provider,
        harness: ProviderHarness,
        ref: ProviderJobRef,
        states: set[JobState],
    ) -> JobStatus:
        """Poll until the job is in one of ``states``; fail if it ends elsewhere or times out."""
        status = await provider.status(ref)
        for _ in range(MAX_TICKS):
            if status.state in states:
                return status
            if status.state.is_terminal:
                pytest.fail(
                    f"job ended as {status.state.value} ({status.raw_state}), wanted {states}"
                )
            await harness.tick()
            status = await provider.status(ref)
        pytest.fail(f"timed out in {status.state.value} waiting for {states}")

    async def _start(
        self, provider: Provider, harness: ProviderHarness, scenario: Scenario
    ) -> tuple[JobSpec, ProviderJobRef]:
        self._need(harness, scenario)
        spec = harness.spec(scenario, self._key())
        return spec, await provider.submit(spec)

    async def _stop(
        self, provider: Provider, harness: ProviderHarness, ref: ProviderJobRef
    ) -> None:
        """Best-effort cleanup so tests do not leave jobs behind."""
        await provider.cancel(ref, grace=timedelta(0))
        for _ in range(MAX_TICKS):
            if (await provider.status(ref)).state.is_terminal:
                return
            await harness.tick()

    async def _read_all(self, provider: Provider, ref: ProviderJobRef, limit: int) -> list[LogLine]:
        lines: list[LogLine] = []
        offset = 0
        for _ in range(10_000):
            chunk = await provider.logs(ref, since=offset, limit=limit)
            lines.extend(chunk.lines)
            assert chunk.next_offset == offset + len(chunk.lines)
            offset = chunk.next_offset
            if not chunk.lines:
                return lines
        pytest.fail("log pagination did not terminate")

    # -- capabilities ----------------------------------------------------------------------

    async def test_capabilities_are_declared(self, provider: Provider) -> None:
        assert isinstance(provider, Provider)
        caps = await provider.capabilities()
        assert caps.kind == provider.kind

    async def test_oversized_spec_is_rejected(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        self._need(harness, Scenario.OVERSIZED)
        spec = harness.spec(Scenario.OVERSIZED, self._key())
        with pytest.raises(CapabilityMismatchError):
            await provider.submit(spec)

    # -- lifecycle -------------------------------------------------------------------------

    async def test_submit_runs_to_success(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        spec, ref = await self._start(provider, harness, Scenario.SUCCEED)
        assert ref.kind == provider.kind
        first = await provider.status(ref)
        assert first.ref == ref
        assert first.state in {JobState.QUEUED, JobState.RUNNING, JobState.SUCCEEDED}
        assert first.raw_state

        await self._wait(provider, harness, ref, {JobState.RUNNING, JobState.SUCCEEDED})
        done = await self._wait(provider, harness, ref, {JobState.SUCCEEDED})
        assert done.exit_code == 0
        assert done.raw_state
        assert done.state.attempt_status is AttemptStatus.SUCCEEDED
        assert done.finished_at is not None
        assert done.started_at is not None
        assert done.submitted_at is not None
        assert done.submitted_at <= done.started_at <= done.finished_at
        assert ProviderJobRef.parse(str(ref)) == ref
        assert spec.idempotency_key

    async def test_failing_job_reports_failure(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        _, ref = await self._start(provider, harness, Scenario.FAIL)
        failed = await self._wait(provider, harness, ref, {JobState.FAILED})
        assert failed.exit_code not in (None, 0)
        assert failed.state.attempt_status is AttemptStatus.FAILED
        assert failed.finished_at is not None

    async def test_terminal_state_is_stable(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        _, ref = await self._start(provider, harness, Scenario.SUCCEED)
        done = await self._wait(provider, harness, ref, {JobState.SUCCEEDED})
        for _ in range(3):
            await harness.tick()
        again = await provider.status(ref)
        assert (again.state, again.exit_code, again.finished_at) == (
            done.state,
            done.exit_code,
            done.finished_at,
        )

    # -- idempotent submit -----------------------------------------------------------------

    async def test_submit_is_idempotent(self, provider: Provider, harness: ProviderHarness) -> None:
        self._need(harness, Scenario.RUN)

        async def active() -> int:
            snapshot = await provider.queue_snapshot()
            return sum(t.pending_jobs + t.running_jobs for t in snapshot.targets)

        before = await active()
        spec = harness.spec(Scenario.RUN, self._key())
        first = await provider.submit(spec)
        try:
            assert await provider.submit(spec) == first
            assert await provider.submit(spec.model_copy()) == first
            assert await active() - before <= 1
        finally:
            await self._stop(provider, harness, first)

    async def test_key_reuse_with_different_spec_conflicts(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        self._need(harness, Scenario.RUN)
        spec = harness.spec(Scenario.RUN, self._key())
        ref = await provider.submit(spec)
        try:
            with pytest.raises(IdempotencyConflictError):
                await provider.submit(spec.model_copy(update={"name": spec.name + "-other"}))
        finally:
            await self._stop(provider, harness, ref)

    # -- cancellation ----------------------------------------------------------------------

    async def test_cancel_running_job(self, provider: Provider, harness: ProviderHarness) -> None:
        _, ref = await self._start(provider, harness, Scenario.RUN)
        await self._wait(provider, harness, ref, {JobState.RUNNING})
        await provider.cancel(ref, grace=harness.grace)
        cancelled = await self._wait(provider, harness, ref, {JobState.CANCELLED})
        assert cancelled.state.attempt_status is AttemptStatus.CANCELLED
        assert cancelled.finished_at is not None

    async def test_cancel_escalates_after_grace(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        _, ref = await self._start(provider, harness, Scenario.IGNORE_TERM)
        await self._wait(provider, harness, ref, {JobState.RUNNING})
        await provider.cancel(ref, grace=harness.grace)
        right_after = await provider.status(ref)
        assert right_after.state in {JobState.CANCELLING, JobState.CANCELLED}
        await self._wait(provider, harness, ref, {JobState.CANCELLED})

    async def test_cancel_is_idempotent(self, provider: Provider, harness: ProviderHarness) -> None:
        _, ref = await self._start(provider, harness, Scenario.RUN)
        await self._wait(provider, harness, ref, {JobState.RUNNING})
        await provider.cancel(ref, grace=harness.grace)
        await provider.cancel(ref, grace=harness.grace)
        await self._wait(provider, harness, ref, {JobState.CANCELLED})
        await provider.cancel(ref, grace=harness.grace)
        assert (await provider.status(ref)).state is JobState.CANCELLED

    async def test_cancel_after_completion_is_noop(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        _, ref = await self._start(provider, harness, Scenario.SUCCEED)
        await self._wait(provider, harness, ref, {JobState.SUCCEEDED})
        await provider.cancel(ref, grace=harness.grace)
        assert (await provider.status(ref)).state is JobState.SUCCEEDED

    # -- signals ---------------------------------------------------------------------------

    async def test_signal_is_delivered(self, provider: Provider, harness: ProviderHarness) -> None:
        caps = await provider.capabilities()
        if JobSignal.CHECKPOINT_NOW not in caps.signals:
            pytest.skip("checkpoint-now is not a supported signal")
        _, ref = await self._start(provider, harness, Scenario.RUN)
        try:
            await self._wait(provider, harness, ref, {JobState.RUNNING})
            await provider.signal(ref, JobSignal.CHECKPOINT_NOW)
            needle = f"received signal {JobSignal.CHECKPOINT_NOW.value}"
            for _ in range(MAX_TICKS):
                if any(needle in line.text for line in await self._read_all(provider, ref, 100)):
                    return
                await harness.tick()
            pytest.fail("the workload never reported receiving the signal")
        finally:
            await self._stop(provider, harness, ref)

    async def test_unsupported_signal_is_rejected(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        caps = await provider.capabilities()
        unsupported = [sig for sig in JobSignal if sig not in caps.signals]
        if not unsupported:
            pytest.skip("every signal is supported")
        _, ref = await self._start(provider, harness, Scenario.RUN)
        try:
            await self._wait(provider, harness, ref, {JobState.RUNNING})
            with pytest.raises(UnsupportedSignalError):
                await provider.signal(ref, unsupported[0])
        finally:
            await self._stop(provider, harness, ref)

    async def test_signal_to_finished_job_is_rejected(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        caps = await provider.capabilities()
        if not caps.signals:
            pytest.skip("provider supports no signals")
        _, ref = await self._start(provider, harness, Scenario.SUCCEED)
        await self._wait(provider, harness, ref, {JobState.SUCCEEDED})
        with pytest.raises(JobNotActiveError):
            await provider.signal(ref, sorted(caps.signals)[0])

    # -- logs ------------------------------------------------------------------------------

    async def test_logs_paginate(self, provider: Provider, harness: ProviderHarness) -> None:
        _, ref = await self._start(provider, harness, Scenario.SUCCEED)
        await self._wait(provider, harness, ref, {JobState.SUCCEEDED})

        everything = await self._read_all(provider, ref, 1000)
        assert len(everything) >= 30
        assert [line.offset for line in everything] == list(range(len(everything)))

        paged = await self._read_all(provider, ref, 7)
        assert [line.text for line in paged] == [line.text for line in everything]

        first = await provider.logs(ref, since=0, limit=5)
        assert [line.offset for line in first.lines] == [0, 1, 2, 3, 4]
        assert first.next_offset == 5
        assert not first.complete
        second = await provider.logs(ref, since=first.next_offset, limit=5)
        assert second.lines[0].offset == 5

        tail = await provider.logs(ref, since=len(everything), limit=5)
        assert tail.lines == ()
        assert tail.next_offset == len(everything)
        assert tail.complete

        beyond = await provider.logs(ref, since=len(everything) + 100, limit=5)
        assert beyond.lines == ()

    async def test_logs_of_running_job_are_incomplete(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        _, ref = await self._start(provider, harness, Scenario.RUN)
        try:
            await self._wait(provider, harness, ref, {JobState.RUNNING})
            chunk = await provider.logs(ref, since=0, limit=1000)
            assert not chunk.complete
        finally:
            await self._stop(provider, harness, ref)

    async def test_logs_reject_bad_arguments(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        _, ref = await self._start(provider, harness, Scenario.SUCCEED)
        with pytest.raises(InvalidInputError):
            await provider.logs(ref, since=-1)
        with pytest.raises(InvalidInputError):
            await provider.logs(ref, since=0, limit=0)

    # -- accounting and queue --------------------------------------------------------------

    async def test_accounting_after_completion(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        spec, ref = await self._start(provider, harness, Scenario.SUCCEED)
        await self._wait(provider, harness, ref, {JobState.SUCCEEDED})
        usage = await provider.accounting(ref)
        assert usage.ref == ref
        assert usage.is_final
        assert usage.wall_time > timedelta(0)
        assert usage.gpus == spec.resources.gpus
        assert usage.gpu_hours > 0 if spec.resources.gpus else usage.gpu_hours == 0
        assert usage.gpu_hour_norm >= 0

    async def test_accounting_while_running_is_provisional(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        _, ref = await self._start(provider, harness, Scenario.RUN)
        try:
            await self._wait(provider, harness, ref, {JobState.RUNNING})
            assert not (await provider.accounting(ref)).is_final
        finally:
            await self._stop(provider, harness, ref)
        assert (await provider.accounting(ref)).is_final

    async def test_queue_snapshot_counts_active_jobs(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        _, ref = await self._start(provider, harness, Scenario.RUN)
        try:
            snapshot = await provider.queue_snapshot()
            assert snapshot.taken_at.tzinfo is not None
            if not snapshot.targets:
                pytest.skip("provider reports no per-target queue")
            assert sum(t.pending_jobs + t.running_jobs for t in snapshot.targets) >= 1
        finally:
            await self._stop(provider, harness, ref)

    # -- unknown refs ----------------------------------------------------------------------

    async def test_unknown_ref_raises_not_found(
        self, provider: Provider, harness: ProviderHarness
    ) -> None:
        _, real = await self._start(provider, harness, Scenario.SUCCEED)
        unknown = real.model_copy(update={"job_id": f"unknown-{uuid.uuid4().hex}"})
        with pytest.raises(JobNotFoundError):
            await provider.status(unknown)
        with pytest.raises(JobNotFoundError):
            await provider.cancel(unknown, grace=harness.grace)
        with pytest.raises(JobNotFoundError):
            await provider.signal(unknown, JobSignal.CHECKPOINT_NOW)
        with pytest.raises(JobNotFoundError):
            await provider.logs(unknown)
        with pytest.raises(JobNotFoundError):
            await provider.accounting(unknown)

    # -- lifecycle of the provider itself --------------------------------------------------

    async def test_close_is_idempotent(self, provider: Provider) -> None:
        await provider.close()
        await provider.close()
