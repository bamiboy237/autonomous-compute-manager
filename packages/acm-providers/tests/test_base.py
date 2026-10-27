from datetime import timedelta

import pytest
from pydantic import ValidationError

from acm_core.domain import AttemptStatus, ProviderKind
from acm_providers.base import (
    Capabilities,
    CheckpointConfig,
    DataMount,
    GpuType,
    JobSignal,
    JobSpec,
    JobState,
    JobStatus,
    LogChunk,
    LogLine,
    ProviderJobRef,
    Resources,
)

KEY = "att_01JB3V6Q1Z8K"


def spec(**overrides: object) -> JobSpec:
    base: dict[str, object] = {
        "name": "train",
        "idempotency_key": KEY,
        "command": ("python", "t.py"),
    }
    return JobSpec.model_validate(base | overrides)


def test_every_state_maps_to_an_attempt_status() -> None:
    assert {s: s.attempt_status for s in JobState}[JobState.TIMED_OUT] is AttemptStatus.FAILED
    assert JobState.QUEUED.attempt_status is AttemptStatus.SUBMITTED
    assert [s for s in JobState if s.is_terminal] == [
        JobState.SUCCEEDED,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.PREEMPTED,
        JobState.TIMED_OUT,
    ]


def test_fingerprint_ignores_only_the_idempotency_key() -> None:
    a = spec()
    assert a.fingerprint() == spec(idempotency_key="att_other_key1").fingerprint()
    assert a.fingerprint() != spec(name="other").fingerprint()
    assert a.fingerprint() != spec(env={"A": "1"}).fingerprint()


def test_spec_validation() -> None:
    with pytest.raises(ValidationError):
        spec(command=())
    with pytest.raises(ValidationError):
        spec(idempotency_key="short")
    with pytest.raises(ValidationError):
        spec(idempotency_key="has space in it")
    mounts = (DataMount(uri="s3://a", path="/data"), DataMount(uri="s3://b", path="/data"))
    with pytest.raises(ValidationError, match="distinct"):
        spec(mounts=mounts)
    with pytest.raises(ValidationError):
        DataMount(uri="s3://a", path="relative")


def test_runtime_env_injects_acm_variables() -> None:
    plain = spec(env={"ACM_IDEMPOTENCY_KEY": "spoofed", "X": "1"}).runtime_env()
    assert plain["ACM_IDEMPOTENCY_KEY"] == KEY
    assert plain["ACM_WALLTIME_SECONDS"] == "3600"
    assert plain["X"] == "1"
    assert "ACM_CHECKPOINT_URI" not in plain

    ckpt = CheckpointConfig(uri="s3://ckpt/run", resume_from="s3://ckpt/run/7/manifest.json")
    env = spec(checkpoint=ckpt).runtime_env()
    assert env["ACM_CHECKPOINT_URI"] == "s3://ckpt/run"
    assert env["ACM_CHECKPOINT_SIGNAL"] == "checkpoint-now"
    assert env["ACM_RESUME_FROM"] == "s3://ckpt/run/7/manifest.json"


def test_job_ref_round_trips() -> None:
    ref = ProviderJobRef(kind=ProviderKind.SLURM, account="pa_01JB", job_id="1234_5")
    assert str(ref) == "slurm:pa_01JB:1234_5"
    assert ProviderJobRef.parse(str(ref)) == ref
    withcolon = ProviderJobRef(kind=ProviderKind.MODAL, account="a", job_id="fc:123")
    assert ProviderJobRef.parse(str(withcolon)) == withcolon
    for bad in ("slurm", "slurm:a", "nope:a:1"):
        with pytest.raises(ValueError, match=r"malformed|not a valid"):
            ProviderJobRef.parse(bad)
    with pytest.raises(ValidationError):
        ProviderJobRef(kind=ProviderKind.SLURM, account="has:colon", job_id="1")


def test_status_finished_requires_terminal_state() -> None:
    ref = ProviderJobRef(kind=ProviderKind.LOCAL, account="a", job_id="1")
    from acm_core.domain import utcnow

    with pytest.raises(ValidationError):
        JobStatus(ref=ref, state=JobState.RUNNING, raw_state="R", finished_at=utcnow())


def test_log_chunk_must_be_contiguous() -> None:
    lines = (LogLine(offset=3, text="a"), LogLine(offset=4, text="b"))
    assert LogChunk(lines=lines, next_offset=5).next_offset == 5
    with pytest.raises(ValidationError):
        LogChunk(lines=lines, next_offset=9)
    with pytest.raises(ValidationError):
        LogChunk(lines=(LogLine(offset=0, text="a"), LogLine(offset=2, text="b")), next_offset=3)


def test_capability_mismatches() -> None:
    caps = Capabilities(
        kind=ProviderKind.SLURM,
        gpu_types=(GpuType(name="a100-80gb", memory_gb=80, max_count=4),),
        max_walltime=timedelta(hours=12),
        max_cpus=32,
        max_memory_gb=256,
        targets=("gpu",),
    )
    ok = spec(resources=Resources(gpus=2, gpu_type="a100-80gb", walltime=timedelta(hours=1)))
    assert caps.fits(ok)
    assert caps.fits(spec(resources=Resources(gpus=1, gpu_memory_gb=40)))
    assert caps.fits(spec())  # CPU-only

    for bad, fragment in [
        (spec(resources=Resources(gpus=8, gpu_type="a100-80gb")), "no GPU type"),
        (spec(resources=Resources(gpus=1, gpu_type="h100")), "no GPU type"),
        (spec(resources=Resources(gpus=1, gpu_memory_gb=96)), "no GPU type"),
        (spec(resources=Resources(walltime=timedelta(days=2))), "walltime"),
        (spec(resources=Resources(cpus=64)), "cpus"),
        (spec(resources=Resources(memory_gb=512)), "memory"),
        (spec(target="cpu"), "target"),
        (spec(image="nvcr.io/x"), "images"),
    ]:
        assert any(fragment in p for p in caps.mismatches(bad)), fragment


def test_signal_values() -> None:
    assert JobSignal("checkpoint-now") is JobSignal.CHECKPOINT_NOW
