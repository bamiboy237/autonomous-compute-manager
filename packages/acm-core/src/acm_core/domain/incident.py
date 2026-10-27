"""Incidents: problems detected on an attempt, and how they were handled."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field, JsonValue

from acm_core.domain.common import DomainModel, Entity, utcnow
from acm_core.ids import AttemptId, CampaignId, DecisionId, IncidentId, RunId

__all__ = ["FailureClass", "Incident", "IncidentStatus", "RecoveryStep", "RecoveryStepOutcome"]


class FailureClass(StrEnum):
    """Failure taxonomy (ARCHITECTURE.md §9)."""

    OOM_GPU = "oom_gpu"
    OOM_HOST = "oom_host"
    NAN_DIVERGENCE = "nan_divergence"
    LOSS_PLATEAU = "loss_plateau"
    THROUGHPUT_REGRESSION = "throughput_regression"
    STALL_NO_HEARTBEAT = "stall_no_heartbeat"
    DATALOADER_HANG = "dataloader_hang"
    NCCL_ERROR = "nccl_error"
    PREEMPTED = "preempted"
    WALLTIME_EXCEEDED = "walltime_exceeded"
    NODE_FAILURE = "node_failure"
    ENV_IMPORT_ERROR = "env_import_error"
    DATA_MISSING = "data_missing"
    QUOTA_EXCEEDED = "quota_exceeded"
    QUEUE_STARVATION = "queue_starvation"
    UNKNOWN = "unknown"


class IncidentStatus(StrEnum):
    OPEN = "open"
    """Detected, not yet classified or acted on."""
    RECOVERING = "recovering"
    RESOLVED = "resolved"
    """The run is healthy again."""
    ESCALATED = "escalated"
    """Waiting on a human (approval needed, or anti-thrash limit hit)."""
    ABANDONED = "abandoned"
    """The run was given up on."""


class RecoveryStepOutcome(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RecoveryStep(DomainModel):
    """One recovery action taken for an incident (the action itself is in the decision)."""

    decision_id: DecisionId
    outcome: RecoveryStepOutcome = RecoveryStepOutcome.PENDING
    note: str | None = None
    at: AwareDatetime = Field(default_factory=utcnow)


class Incident(Entity):
    """A detected problem on an attempt."""

    id: IncidentId = Field(default_factory=IncidentId.new)
    campaign_id: CampaignId
    run_id: RunId
    attempt_id: AttemptId
    failure_class: FailureClass = FailureClass.UNKNOWN
    status: IncidentStatus = IncidentStatus.OPEN
    summary: str = Field(min_length=1)
    signals: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    """Raw detection signals (e.g. ``{"last_heartbeat_s": 900, "loss": "nan"}``)."""
    recovery_steps: list[RecoveryStep] = Field(default_factory=list[RecoveryStep])
    detected_at: AwareDatetime = Field(default_factory=utcnow)
    resolved_at: AwareDatetime | None = None
