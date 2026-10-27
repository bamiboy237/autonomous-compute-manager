"""Campaigns, their objective metric, autonomy policy and budget."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from acm_core.domain.common import Currency, DomainModel, Entity, Money, RiskLevel
from acm_core.domain.decision import ActionKind
from acm_core.ids import BudgetId, BudgetLineId, CampaignId, WorkloadId

__all__ = [
    "DEFAULT_AUTO_ALLOWED_ACTIONS",
    "AutonomyMode",
    "Budget",
    "BudgetLine",
    "Campaign",
    "CampaignStatus",
    "Metric",
    "MetricDirection",
    "Policy",
]


class MetricDirection(StrEnum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


class Metric(DomainModel):
    """The campaign's success metric, e.g. ``mrr_at_25`` maximized."""

    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.\-/@]+$")
    """Metric key as reported by the workload (``acm.log(step, mrr_at_25=...)``)."""
    direction: MetricDirection
    evaluator_ref: str = Field(min_length=1)
    """Import path of the evaluator, ``package.module:callable``."""

    @field_validator("evaluator_ref")
    @classmethod
    def _check_evaluator_ref(cls, value: str) -> str:
        module, sep, attr = value.partition(":")
        if not sep or not module or not attr:
            raise ValueError("evaluator_ref must look like 'package.module:callable'")
        return value

    def is_better(self, candidate: float, incumbent: float) -> bool:
        """Whether ``candidate`` strictly improves on ``incumbent`` in this direction."""
        if self.direction is MetricDirection.MAXIMIZE:
            return candidate > incumbent
        return candidate < incumbent


class AutonomyMode(StrEnum):
    """How much the agents may do without a human (ARCHITECTURE.md §8)."""

    ADVISORY = "advisory"
    """Every decision waits for human approval."""
    GATED = "gated"
    """Cheap, low-risk, allow-listed decisions run automatically; the rest wait."""


DEFAULT_AUTO_ALLOWED_ACTIONS: frozenset[ActionKind] = frozenset(
    {
        ActionKind.RETRY,
        ActionKind.RESUME_FROM_CHECKPOINT,
        ActionKind.REDUCE_BATCH_WITH_GRAD_ACCUM,
        ActionKind.EARLY_STOP,
    }
)

_NEVER_AUTO_ACTIONS: frozenset[ActionKind] = frozenset({ActionKind.SUBMIT_TO_KAGGLE})


class Policy(DomainModel):
    """Thresholds for the policy gate in ``gated`` mode.

    A decision runs without approval only if its action is in ``auto_allowed_actions``, its
    risk is at most ``max_auto_risk``, and each of its expected costs is strictly below the
    limit for that currency. A cost in a currency with no limit always needs approval.
    Kaggle submissions always need approval.
    """

    auto_allowed_actions: frozenset[ActionKind] = DEFAULT_AUTO_ALLOWED_ACTIONS
    auto_cost_limits: dict[Currency, Decimal] = Field(
        default_factory=lambda: {
            Currency.USD: Decimal("10"),
            Currency.GPU_HOUR_NORM: Decimal("4"),
            Currency.SLURM_SU: Decimal("100"),
            Currency.LLM_USD: Decimal("2"),
        }
    )
    max_auto_risk: RiskLevel = RiskLevel.LOW

    @field_validator("auto_allowed_actions")
    @classmethod
    def _check_actions(cls, value: frozenset[ActionKind]) -> frozenset[ActionKind]:
        forbidden = value & _NEVER_AUTO_ACTIONS
        if forbidden:
            raise ValueError(f"these actions always need approval: {sorted(forbidden)}")
        return value

    @field_validator("auto_cost_limits")
    @classmethod
    def _check_limits(cls, value: dict[Currency, Decimal]) -> dict[Currency, Decimal]:
        if any(limit < 0 for limit in value.values()):
            raise ValueError("auto_cost_limits must be non-negative")
        return value

    def permits_auto_execution(
        self, action: ActionKind, expected_cost: list[Money], risk: RiskLevel
    ) -> bool:
        """Whether a decision may skip approval under this policy (gated mode only)."""
        if action not in self.auto_allowed_actions or action in _NEVER_AUTO_ACTIONS:
            return False
        if risk.rank > self.max_auto_risk.rank:
            return False
        for cost in expected_cost:
            limit = self.auto_cost_limits.get(cost.currency)
            if limit is None or cost.amount >= limit:
                return False
        return True


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    """Objective reached or budget spent."""
    STOPPED = "stopped"
    """Stopped by a user before completion."""


class Campaign(Entity):
    """A user's objective: optimize ``metric`` for ``workload`` within a budget."""

    id: CampaignId = Field(default_factory=CampaignId.new)
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1)
    """Plain-language goal given to the planner."""
    metric: Metric
    workload_id: WorkloadId
    autonomy: AutonomyMode = AutonomyMode.ADVISORY
    policy: Policy = Field(default_factory=Policy)
    status: CampaignStatus = CampaignStatus.DRAFT

    def requires_approval(
        self, action: ActionKind, expected_cost: list[Money], risk: RiskLevel
    ) -> bool:
        """The policy gate: whether a decision must wait for a human."""
        if self.autonomy is AutonomyMode.ADVISORY:
            return True
        return not self.policy.permits_auto_execution(action, expected_cost, risk)


class BudgetLine(Entity):
    """Limit for one currency. ``soft_threshold`` is the fraction of ``limit`` that warns."""

    id: BudgetLineId = Field(default_factory=BudgetLineId.new)
    budget_id: BudgetId
    currency: Currency
    limit: Decimal = Field(gt=0)
    """Hard limit: submissions that would exceed it are rejected."""
    soft_threshold: Decimal = Field(default=Decimal("0.8"), gt=0, le=1)

    @property
    def soft_limit(self) -> Decimal:
        return self.limit * self.soft_threshold


class Budget(Entity):
    """A campaign's spending limits, one line per currency."""

    id: BudgetId = Field(default_factory=BudgetId.new)
    campaign_id: CampaignId
    lines: list[BudgetLine] = Field(default_factory=list[BudgetLine])

    @model_validator(mode="after")
    def _check_lines(self) -> Self:
        currencies = [line.currency for line in self.lines]
        if len(currencies) != len(set(currencies)):
            raise ValueError("a budget has at most one line per currency")
        if any(line.budget_id != self.id for line in self.lines):
            raise ValueError("every budget line must reference this budget")
        return self

    def line_for(self, currency: Currency) -> BudgetLine | None:
        return next((line for line in self.lines if line.currency is currency), None)
