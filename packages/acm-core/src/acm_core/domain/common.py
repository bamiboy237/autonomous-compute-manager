"""Base classes and value types shared by every domain model."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

__all__ = [
    "Actor",
    "ActorKind",
    "AgentRole",
    "ContentHash",
    "Currency",
    "DomainModel",
    "Entity",
    "Money",
    "NonNegativeDecimal",
    "RiskLevel",
    "utcnow",
]


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]

ContentHash = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
"""Content address: ``sha256:`` followed by 64 lowercase hex digits."""


class DomainModel(BaseModel):
    """Base for every domain model and value object.

    Models are immutable (``frozen``) and reject unknown fields. Change one with
    ``model_copy(update=...)`` or, for status fields, the entity's ``transition_to()``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


class Entity(DomainModel):
    """A persisted record. Subclasses declare ``id`` with their own id type."""

    created_at: AwareDatetime = Field(default_factory=utcnow)
    updated_at: AwareDatetime = Field(default_factory=utcnow)


class Currency(StrEnum):
    """Budget and ledger currencies (ARCHITECTURE.md §11)."""

    USD = "usd"
    """Cash spent on providers."""
    SLURM_SU = "slurm_su"
    """Slurm service units of an allocation."""
    GPU_HOUR_NORM = "gpu_hour_norm"
    """GPU-hours normalized to an A100-80G = 1.0 reference."""
    LLM_USD = "llm_usd"
    """Cash spent on model calls, budgeted separately from compute."""


class Money(DomainModel):
    """A non-negative amount in one currency. ``Decimal`` keeps ledger sums exact."""

    currency: Currency
    amount: NonNegativeDecimal

    def __add__(self, other: Money) -> Money:
        if other.currency is not self.currency:
            raise ValueError(f"cannot add {other.currency} to {self.currency}")
        return Money(currency=self.currency, amount=self.amount + other.amount)


class ActorKind(StrEnum):
    AGENT = "agent"
    HUMAN = "human"
    SYSTEM = "system"
    """Deterministic code: the policy gate, recovery rules, schedulers."""


class AgentRole(StrEnum):
    """Agent roles (ARCHITECTURE.md §8). Model routing in ``acm_agents.models`` keys off it."""

    TRIAGE = "triage"
    DIAGNOSTICIAN = "diagnostician"
    PLANNER = "planner"
    ENGINEER = "engineer"
    CRITIC = "critic"


class Actor(DomainModel):
    """Who did something: an agent role, a human user, or the system.

    ``name`` is the agent role for agents, the user id for humans, and a component name
    (``policy_gate``, ``recovery_rules``) for the system.
    """

    kind: ActorKind
    name: str = Field(min_length=1)

    @classmethod
    def agent(cls, role: AgentRole) -> Actor:
        return cls(kind=ActorKind.AGENT, name=role.value)

    @classmethod
    def human(cls, user_id: str) -> Actor:
        return cls(kind=ActorKind.HUMAN, name=user_id)

    @classmethod
    def system(cls, component: str) -> Actor:
        return cls(kind=ActorKind.SYSTEM, name=component)


class RiskLevel(StrEnum):
    """Coarse risk of executing an action, used by the policy gate. Ordered low to high."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return _RISK_RANK[self]


_RISK_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
