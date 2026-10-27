"""Events a running workload emits (``acm.run.<run_id>.*``, stream ``ACM_RUNS``)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import Field, WithJsonSchema

from acm_core.domain.common import DomainModel
from acm_core.events import subjects
from acm_core.events.base import Event
from acm_core.events.streams import RUNS_STREAM
from acm_core.ids import AttemptId, CheckpointId, RunId

__all__ = [
    "CheckpointEvent",
    "GpuSample",
    "HeartbeatEvent",
    "LogEvent",
    "LogStream",
    "MetricValue",
    "MetricsEvent",
    "RunEvent",
]

MetricValue = Annotated[
    float,
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "number"},
                {"type": "string", "enum": ["NaN", "Infinity", "-Infinity"]},
            ]
        }
    ),
]
"""A metric value. NaN and infinities are kept (divergence detection needs them)."""


class RunEvent(Event):
    """An event about one run, optionally attributed to the attempt that produced it."""

    stream_name: ClassVar[str] = RUNS_STREAM

    run_id: RunId
    attempt_id: AttemptId | None = None


class MetricsEvent(RunEvent):
    """Training/eval metrics at one step (``acm.run.<run_id>.metrics``)."""

    v: Literal[1] = 1
    type: Literal["run.metrics"] = "run.metrics"
    step: int = Field(ge=0)
    wall_time: float = Field(ge=0)
    """Seconds since the run started."""
    values: dict[str, MetricValue] = Field(min_length=1)
    """Metric name to value, e.g. ``{"loss": 0.41, "val/mrr@25": 0.18}``."""

    def subject(self) -> str:
        return subjects.run_metrics(self.run_id)


class GpuSample(DomainModel):
    """One GPU's utilization at heartbeat time (from NVML)."""

    index: int = Field(ge=0)
    util: float = Field(ge=0, le=100)
    """Compute utilization, percent."""
    mem_used_mb: float = Field(ge=0)
    mem_total_mb: float | None = Field(default=None, ge=0)


class HeartbeatEvent(RunEvent):
    """Liveness and throughput (``acm.run.<run_id>.heartbeat``). A missing heartbeat is the
    ``stall_no_heartbeat`` signal, so workloads send one every few seconds even when idle."""

    v: Literal[1] = 1
    type: Literal["run.heartbeat"] = "run.heartbeat"
    host: str = Field(min_length=1)
    step: int | None = Field(default=None, ge=0)
    """Last completed step, if the workload reports steps."""
    throughput: float | None = Field(default=None, ge=0)
    """Workload-defined units per second (samples/s, tokens/s)."""
    gpu: list[GpuSample] = Field(default_factory=list[GpuSample])
    cpu_util: float | None = Field(default=None, ge=0)
    """Process CPU utilization, percent of one core (may exceed 100)."""
    mem_rss_mb: float | None = Field(default=None, ge=0)

    def subject(self) -> str:
        return subjects.run_heartbeat(self.run_id)


class LogStream(StrEnum):
    STDOUT = "stdout"
    STDERR = "stderr"
    SYSTEM = "system"
    """Lines from the provider or scheduler rather than the workload (e.g. Slurm messages)."""


class LogEvent(RunEvent):
    """A chunk of log lines (``acm.run.<run_id>.log``)."""

    v: Literal[1] = 1
    type: Literal["run.log"] = "run.log"
    stream: LogStream
    lines: list[str] = Field(min_length=1)
    """Lines without trailing newlines, in order."""
    offset: int | None = Field(default=None, ge=0)
    """Zero-based line number of ``lines[0]`` in the stream, when the source knows it."""

    def subject(self) -> str:
        return subjects.run_log(self.run_id)


class CheckpointEvent(RunEvent):
    """A checkpoint was written and its manifest is readable (``acm.run.<run_id>.checkpoint``)."""

    v: Literal[1] = 1
    type: Literal["run.checkpoint"] = "run.checkpoint"
    step: int = Field(ge=0)
    manifest_uri: str = Field(min_length=1)
    """Object-store URI of the manifest, e.g. ``s3://acm/manifests/checkpoints/<run>/<step>.json``."""
    checkpoint_id: CheckpointId | None = None
    """Set once the control plane has recorded the ``Checkpoint`` entity."""

    def subject(self) -> str:
        return subjects.run_checkpoint(self.run_id)
