"""FakeProvider passes the conformance suite and behaves as documented."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from acm_core.domain import ComputeTarget, FailureClass, ProviderAccount, ProviderKind
from acm_core.errors import ConfigurationError
from acm_providers.base import JobSignal, JobState, Provider, Resources
from acm_providers.fake import FakeProvider, FakeSettings, register_fake
from acm_providers.registry import ProviderRegistry
from acm_providers.scenarios import Scenario
from acm_providers.testing import FakeHarness, ProviderConformance, ProviderHarness, make_fake


class TestFakeProviderConformance(ProviderConformance):
    @pytest.fixture
    async def provider(self, _pair: tuple[FakeProvider, FakeHarness]) -> AsyncIterator[Provider]:
        yield _pair[0]
        await _pair[0].close()

    @pytest.fixture
    def harness(self, _pair: tuple[FakeProvider, FakeHarness]) -> ProviderHarness:
        return _pair[1]

    @pytest.fixture
    def _pair(self) -> tuple[FakeProvider, FakeHarness]:
        return make_fake()


class TestFakeWithRestrictedSignals(TestFakeProviderConformance):
    """Exercises the unsupported-signal path of the suite."""

    @pytest.fixture
    def _pair(self) -> tuple[FakeProvider, FakeHarness]:
        return make_fake(frozenset({JobSignal.CHECKPOINT_NOW, JobSignal.TERM}))


@pytest.fixture
def pair() -> tuple[FakeProvider, FakeHarness]:
    return make_fake()


async def test_walltime_kills_the_job(pair: tuple[FakeProvider, FakeHarness]) -> None:
    provider, harness = pair
    spec = harness.spec(Scenario.RUN, "walltime-test-1").model_copy(
        update={
            "resources": Resources(gpus=1, gpu_type="a100-80gb", walltime=timedelta(seconds=30))
        }
    )
    ref = await provider.submit(spec)
    for _ in range(40):
        await harness.tick()
    status = await provider.status(ref)
    assert status.state is JobState.TIMED_OUT
    assert status.raw_state == "TIMEOUT"
    assert status.failure_class is FailureClass.WALLTIME_EXCEEDED
    assert status.state.attempt_status.value == "failed"


async def test_preemption_is_reported(pair: tuple[FakeProvider, FakeHarness]) -> None:
    provider, harness = pair
    ref = await provider.submit(harness.spec(Scenario.RUN, "preempt-test-1"))
    for _ in range(3):
        await harness.tick()
    provider.preempt(ref)
    status = await provider.status(ref)
    assert status.state is JobState.PREEMPTED
    assert status.failure_class is FailureClass.PREEMPTED


async def test_gpu_accounting_matches_runtime(pair: tuple[FakeProvider, FakeHarness]) -> None:
    provider, harness = pair
    ref = await provider.submit(harness.spec(Scenario.SUCCEED, "account-test-1"))
    for _ in range(20):
        await harness.tick()
    usage = await provider.accounting(ref)
    assert usage.wall_time == timedelta(seconds=5)
    assert float(usage.gpu_hours) == pytest.approx(5 / 3600)
    assert usage.cost is not None
    assert float(usage.cost.amount) == pytest.approx(2 * 5 / 3600)


async def test_queue_snapshot_tracks_free_gpus(pair: tuple[FakeProvider, FakeHarness]) -> None:
    provider, harness = pair
    await provider.submit(harness.spec(Scenario.RUN, "queue-test-0001"))
    queued = (await provider.queue_snapshot()).targets[0]
    assert (queued.pending_jobs, queued.running_jobs, queued.free_gpus) == (1, 0, 8)
    for _ in range(3):
        await harness.tick()
    running = (await provider.queue_snapshot()).targets[0]
    assert (running.pending_jobs, running.running_jobs, running.free_gpus) == (0, 1, 7)


async def test_signal_before_start_is_not_active(pair: tuple[FakeProvider, FakeHarness]) -> None:
    from acm_providers.errors import JobNotActiveError

    provider, harness = pair
    ref = await provider.submit(harness.spec(Scenario.RUN, "queued-signal-1"))
    with pytest.raises(JobNotActiveError):
        await provider.signal(ref, JobSignal.CHECKPOINT_NOW)


async def test_cancel_queued_job_is_immediate(pair: tuple[FakeProvider, FakeHarness]) -> None:
    provider, harness = pair
    ref = await provider.submit(harness.spec(Scenario.RUN, "queued-cancel-1"))
    await provider.cancel(ref, grace=timedelta(seconds=30))
    assert (await provider.status(ref)).state is JobState.CANCELLED


def _account(config: dict[str, object]) -> ProviderAccount:
    return ProviderAccount.model_validate(
        {"kind": ProviderKind.SIMULATOR, "name": "sim", "config": config}
    )


def test_registry_builds_from_account() -> None:
    registry = ProviderRegistry()
    register_fake(registry)
    provider = registry.build(_account({"total_gpus": 2}))
    assert isinstance(provider, Provider)
    assert registry.kinds == {ProviderKind.SIMULATOR}


def test_registry_rejects_bad_configuration() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ConfigurationError, match="no adapter"):
        registry.build(_account({}))
    register_fake(registry)
    with pytest.raises(ConfigurationError, match="already registered"):
        register_fake(registry)
    with pytest.raises(ConfigurationError, match="invalid config"):
        registry.build(_account({"total_gpus": -1}))
    with pytest.raises(ConfigurationError, match="invalid config"):
        registry.build(_account({"unknown": 1}))
    disabled = _account({}).model_copy(update={"enabled": False})
    with pytest.raises(ConfigurationError, match="disabled"):
        registry.build(disabled)
    needs_secret = _account({}).model_copy(update={"credentials_ref": "vault/sim"})
    with pytest.raises(ConfigurationError, match="no resolver"):
        registry.build(needs_secret)
    other = ComputeTarget(provider_account_id=_account({}).id, name="x")
    with pytest.raises(ConfigurationError, match="do not belong"):
        registry.build(_account({}), [other])


def test_registry_resolves_credentials_and_filters_targets() -> None:
    seen: dict[str, object] = {}
    registry = ProviderRegistry()

    def factory(ctx):  # type: ignore[no-untyped-def]
        seen["credential"] = ctx.credential
        seen["targets"] = [t.name for t in ctx.targets]
        return FakeProvider(ctx.settings)

    registry.register(ProviderKind.SIMULATOR, FakeSettings, factory)
    account = _account({}).model_copy(update={"credentials_ref": "vault/sim"})
    targets = [
        ComputeTarget(provider_account_id=account.id, name="on"),
        ComputeTarget(provider_account_id=account.id, name="off", enabled=False),
    ]
    registry.build(account, targets, credentials=lambda ref: f"secret-for-{ref}")
    assert seen == {"credential": "secret-for-vault/sim", "targets": ["on"]}


def test_build_all_skips_disabled_and_keys_by_name() -> None:
    registry = ProviderRegistry()
    register_fake(registry)
    on = _account({})
    off = _account({}).model_copy(update={"name": "off", "enabled": False})
    assert list(registry.build_all([on, off])) == ["sim"]


def test_capabilities_from_targets() -> None:
    from decimal import Decimal  # noqa: F401

    from acm_providers.registry import capabilities_from_targets

    account = _account({})
    targets = [
        ComputeTarget(
            provider_account_id=account.id,
            name="a100",
            gpu_type="a100-80gb",
            gpu_memory_gb=80,
            max_gpus=4,
            max_walltime=timedelta(hours=12),
        ),
        ComputeTarget(
            provider_account_id=account.id,
            name="a100-big",
            gpu_type="a100-80gb",
            gpu_memory_gb=80,
            max_gpus=8,
            max_walltime=timedelta(hours=48),
            preemptible=True,
        ),
        ComputeTarget(provider_account_id=account.id, name="cpu"),
        ComputeTarget(
            provider_account_id=account.id,
            name="off",
            enabled=False,
            gpu_type="h100",
            gpu_memory_gb=80,
        ),
    ]
    caps = capabilities_from_targets(
        ProviderKind.SLURM, targets, signals=frozenset({JobSignal.USR1})
    )
    assert [(g.name, g.max_count) for g in caps.gpu_types] == [("a100-80gb", 8)]
    assert caps.max_walltime is None  # the cpu target has no limit
    assert caps.targets == ("a100", "a100-big", "cpu")
    assert caps.preemptible
    assert caps.signals == {JobSignal.USR1}
