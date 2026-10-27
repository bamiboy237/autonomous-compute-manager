"""Decisions, the evidence behind them, and human approvals.

Agents never mutate state. They return a ``Decision`` (``proposed``) carrying the action,
its parameters, a rationale, typed ``EvidenceRef`` pointers, expected value and cost. The
policy gate then approves it automatically (gated mode, cheap and allow-listed actions) or
waits for a human ``Approval``; the workflow executes approved decisions.

Decision lifecycle (``DECISION_STATE_MACHINE``)::

    proposed  -> approved | rejected | expired
    approved  -> executed | failed
    rejected, executed, failed, expired: terminal

``expired`` means the proposal was superseded or timed out before anyone decided.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from acm_core.domain.common import Actor, ActorKind, DomainModel, Entity, Money, RiskLevel, utcnow
from acm_core.domain.state_machine import StateMachine
from acm_core.errors import InvalidInputError
from acm_core.ids import (
    ApprovalId,
    ArtifactId,
    AttemptId,
    CampaignId,
    DecisionId,
    EvidenceId,
    ExperimentId,
    HypothesisId,
    IncidentId,
    RunId,
)

__all__ = [
    "DECISION_STATE_MACHINE",
    "ActionKind",
    "Approval",
    "ApprovalVerdict",
    "ArtifactEvidence",
    "Decision",
    "DecisionKind",
    "DecisionStatus",
    "Evidence",
    "EvidenceKind",
    "EvidenceRef",
    "LogExcerptEvidence",
    "MetricQueryEvidence",
    "PriorDecisionEvidence",
    "RunComparisonEvidence",
]


class DecisionKind(StrEnum):
    """Area a decision belongs to. The UI groups decisions by it."""

    RECOVERY = "recovery"
    """Respond to an incident on a run."""
    EXPERIMENT = "experiment"
    """Start new experiments or hypotheses."""
    ALLOCATION = "allocation"
    """Stop, continue, or shift compute between runs."""
    CODE_CHANGE = "code_change"
    """Apply an Engineer patch as a new code version."""
    SUBMISSION = "submission"
    """Submit a prepared Kaggle submission (always needs human approval)."""


class ActionKind(StrEnum):
    """Concrete action a decision asks the workflow to execute.

    The first eight are the recovery playbook (ARCHITECTURE.md §9); the rest are planner
    actions. ``Policy.auto_allowed_actions`` is a subset of these.
    """

    RETRY = "retry"
    RESUME_FROM_CHECKPOINT = "resume_from_checkpoint"
    REDUCE_BATCH_WITH_GRAD_ACCUM = "reduce_batch_with_grad_accum"
    LOWER_LR_AND_RESUME = "lower_lr_and_resume"
    INCREASE_WALLTIME = "increase_walltime"
    MOVE_PROVIDER_VIA_CHECKPOINT = "move_provider_via_checkpoint"
    PATCH_CODE = "patch_code"
    ABANDON_WITH_EVIDENCE = "abandon_with_evidence"
    EARLY_STOP = "early_stop"
    """Stop a healthy run whose results do not justify more compute."""
    START_EXPERIMENT = "start_experiment"
    REALLOCATE_BUDGET = "reallocate_budget"
    SUBMIT_TO_KAGGLE = "submit_to_kaggle"


class DecisionStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"
    """Approved but execution failed; ``Decision.error`` says why."""
    EXPIRED = "expired"


DECISION_STATE_MACHINE: StateMachine[DecisionStatus] = StateMachine(
    "decision",
    DecisionStatus,
    initial=DecisionStatus.PROPOSED,
    transitions={
        DecisionStatus.PROPOSED: {
            DecisionStatus.APPROVED,
            DecisionStatus.REJECTED,
            DecisionStatus.EXPIRED,
        },
        DecisionStatus.APPROVED: {DecisionStatus.EXECUTED, DecisionStatus.FAILED},
        DecisionStatus.REJECTED: set(),
        DecisionStatus.EXECUTED: set(),
        DecisionStatus.FAILED: set(),
        DecisionStatus.EXPIRED: set(),
    },
)


class EvidenceKind(StrEnum):
    METRIC_QUERY = "metric_query"
    LOG_EXCERPT = "log_excerpt"
    RUN_COMPARISON = "run_comparison"
    PRIOR_DECISION = "prior_decision"
    ARTIFACT = "artifact"


class _EvidenceBase(DomainModel):
    summary: str = Field(min_length=1, max_length=2000)
    """What this evidence shows, in one or two sentences."""


class MetricQueryEvidence(_EvidenceBase):
    """Metric values of one run over a step range (re-runnable against ClickHouse)."""

    kind: Literal[EvidenceKind.METRIC_QUERY] = EvidenceKind.METRIC_QUERY
    run_id: RunId
    metric_keys: list[str] = Field(min_length=1)
    from_step: int | None = Field(default=None, ge=0)
    to_step: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if (
            self.from_step is not None
            and self.to_step is not None
            and self.from_step > self.to_step
        ):
            raise ValueError("from_step must be <= to_step")
        return self


class LogExcerptEvidence(_EvidenceBase):
    """Lines from an attempt's logs. ``excerpt`` is a bounded copy for display."""

    kind: Literal[EvidenceKind.LOG_EXCERPT] = EvidenceKind.LOG_EXCERPT
    run_id: RunId
    attempt_id: AttemptId | None = None
    stream: Literal["stdout", "stderr"] = "stderr"
    start_line: int = Field(ge=0)
    end_line: int = Field(ge=0)
    excerpt: str = Field(max_length=8000)

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.start_line > self.end_line:
            raise ValueError("start_line must be <= end_line")
        return self


class RunComparisonEvidence(_EvidenceBase):
    """A comparison of one metric across runs (e.g. learning curves at equal steps)."""

    kind: Literal[EvidenceKind.RUN_COMPARISON] = EvidenceKind.RUN_COMPARISON
    run_ids: list[RunId] = Field(min_length=2)
    metric_key: str = Field(min_length=1)
    at_step: int | None = Field(default=None, ge=0)


class PriorDecisionEvidence(_EvidenceBase):
    """An earlier decision (and its outcome) this one builds on."""

    kind: Literal[EvidenceKind.PRIOR_DECISION] = EvidenceKind.PRIOR_DECISION
    decision_id: DecisionId


class ArtifactEvidence(_EvidenceBase):
    """A stored artifact, e.g. a validation report or submission file."""

    kind: Literal[EvidenceKind.ARTIFACT] = EvidenceKind.ARTIFACT
    artifact_id: ArtifactId


EvidenceRef = Annotated[
    MetricQueryEvidence
    | LogExcerptEvidence
    | RunComparisonEvidence
    | PriorDecisionEvidence
    | ArtifactEvidence,
    Field(discriminator="kind"),
]
"""Typed pointer backing a decision or hypothesis, discriminated by ``kind``."""


class Decision(Entity):
    """An agent or human decision and its lifecycle."""

    id: DecisionId = Field(default_factory=DecisionId.new)
    campaign_id: CampaignId
    kind: DecisionKind
    action: ActionKind
    parameters: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    """Action-specific arguments (e.g. ``{"batch_size": 16, "grad_accum": 4}``)."""
    rationale: str = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(default_factory=list[EvidenceRef])
    expected_value: float | None = None
    """Expected improvement of the campaign metric, in metric units, if known."""
    expected_cost: list[Money] = Field(default_factory=list[Money])
    """Expected spend, at most one entry per currency."""
    risk: RiskLevel = RiskLevel.LOW

    run_id: RunId | None = None
    experiment_id: ExperimentId | None = None
    hypothesis_id: HypothesisId | None = None
    incident_id: IncidentId | None = None

    status: DecisionStatus = DecisionStatus.PROPOSED
    proposed_by: Actor
    decided_by: Actor | None = None
    """Who approved or rejected it (a human, or the policy gate as a system actor)."""
    decided_at: AwareDatetime | None = None
    executed_at: AwareDatetime | None = None
    error: str | None = None
    """Why execution failed (status ``failed``)."""

    @model_validator(mode="after")
    def _check_costs(self) -> Self:
        currencies = [m.currency for m in self.expected_cost]
        if len(currencies) != len(set(currencies)):
            raise ValueError("expected_cost has more than one entry for a currency")
        return self

    def transition_to(
        self,
        status: DecisionStatus,
        *,
        actor: Actor | None = None,
        error: str | None = None,
        at: datetime | None = None,
    ) -> Decision:
        """Return a copy in ``status``; raises ``InvalidTransitionError`` if not allowed.

        ``actor`` is required when approving or rejecting and is recorded as ``decided_by``
        (optional for ``expired``); ``error`` is recorded when moving to ``failed``.
        """
        DECISION_STATE_MACHINE.check(self.status, status)
        if actor is None and status in (DecisionStatus.APPROVED, DecisionStatus.REJECTED):
            raise InvalidInputError(f"an actor is required to move a decision to {status.value}")
        now = at or utcnow()
        update: dict[str, object] = {"status": status, "updated_at": now}
        if status in (DecisionStatus.APPROVED, DecisionStatus.REJECTED, DecisionStatus.EXPIRED):
            update["decided_by"] = actor
            update["decided_at"] = now
        elif status is DecisionStatus.EXECUTED:
            update["executed_at"] = now
        elif status is DecisionStatus.FAILED:
            update["error"] = error
        return self.model_copy(update=update)


class Evidence(Entity):
    """Persisted evidence row linking an ``EvidenceRef`` to its decision."""

    id: EvidenceId = Field(default_factory=EvidenceId.new)
    decision_id: DecisionId
    position: int = Field(ge=0)
    """Order within the decision's evidence list."""
    ref: EvidenceRef


class ApprovalVerdict(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class Approval(Entity):
    """A human's response to a decision that waited for approval."""

    id: ApprovalId = Field(default_factory=ApprovalId.new)
    decision_id: DecisionId
    verdict: ApprovalVerdict
    actor: Actor
    comment: str | None = None

    @model_validator(mode="after")
    def _check_actor(self) -> Self:
        if self.actor.kind is not ActorKind.HUMAN:
            raise ValueError("approvals are made by humans")
        return self
