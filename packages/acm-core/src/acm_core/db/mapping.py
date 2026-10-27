"""Mechanical mapping between frozen domain models and table rows.

Row columns carry the same names as the domain fields, so one generic mapper per entity
suffices. Fields listed in ``json_fields`` hold nested value objects and are stored as JSONB;
non-finite floats (a NaN metric) survive as the strings ``"NaN"``/``"Infinity"``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic_core import to_json

from acm_core.db import models
from acm_core.db.base import Base
from acm_core.domain import (
    Approval,
    Artifact,
    Attempt,
    Budget,
    BudgetLine,
    Campaign,
    Checkpoint,
    CodeVersion,
    ComputeTarget,
    Decision,
    Evidence,
    Experiment,
    Hypothesis,
    Incident,
    KaggleSubmission,
    LedgerEntry,
    MemoryItem,
    ModelCall,
    ProviderAccount,
    Run,
    Workload,
)
from acm_core.domain.common import Entity
from acm_core.ids import PrefixedId

__all__ = ["MAPPINGS", "Mapping", "mapping_for"]


@dataclass(frozen=True)
class Mapping[D: Entity, R: Base]:
    domain: type[D]
    row: type[R]
    json_fields: frozenset[str] = frozenset()
    skip_fields: frozenset[str] = field(default_factory=frozenset[str])
    """Domain fields kept out of the row (stored in a child table by the repo)."""

    @property
    def columns(self) -> list[str]:
        return [n for n in self.domain.model_fields if n not in self.skip_fields]

    def to_values(self, entity: D) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for name in self.columns:
            value = getattr(entity, name)
            if name in self.json_fields:
                value = json.loads(to_json(value, inf_nan_mode="strings"))
            elif isinstance(value, PrefixedId):
                value = str(value)
            values[name] = value
        return values

    def to_row(self, entity: D) -> R:
        return self.row(**self.to_values(entity))

    def from_row(self, row: R, **extra: Any) -> D:
        data: dict[str, Any] = {}
        for name in self.columns:
            value = getattr(row, name)
            data[name] = value.tolist() if hasattr(value, "tolist") else value  # pgvector arrays
        return self.domain.model_validate({**data, **extra})


def _m[D: Entity, R: Base](
    domain: type[D], row: type[R], *json: str, skip: tuple[str, ...] = ()
) -> Mapping[D, R]:
    return Mapping(domain, row, frozenset(json), frozenset(skip))


MAPPINGS: dict[type[Entity], Mapping[Any, Any]] = {
    m.domain: m
    for m in (
        _m(Workload, models.WorkloadRow),
        _m(CodeVersion, models.CodeVersionRow),
        _m(Campaign, models.CampaignRow, "metric", "policy"),
        _m(Budget, models.BudgetRow, skip=("lines",)),
        _m(BudgetLine, models.BudgetLineRow),
        _m(Hypothesis, models.HypothesisRow, "evidence"),
        _m(Experiment, models.ExperimentRow, "params", "resources"),
        _m(Run, models.RunRow, "final_metrics"),
        _m(Attempt, models.AttemptRow, "resources"),
        _m(ProviderAccount, models.ProviderAccountRow, "config"),
        _m(ComputeTarget, models.ComputeTargetRow, "price_per_gpu_hour"),
        _m(Checkpoint, models.CheckpointRow, "metrics"),
        _m(Artifact, models.ArtifactRow),
        _m(
            Decision,
            models.DecisionRow,
            "parameters",
            "expected_cost",
            "proposed_by",
            "decided_by",
            skip=("evidence",),
        ),
        _m(Evidence, models.EvidenceRow, "ref"),
        _m(Approval, models.ApprovalRow, "actor"),
        _m(LedgerEntry, models.LedgerEntryRow),
        _m(ModelCall, models.ModelCallRow),
        _m(Incident, models.IncidentRow, "signals", "recovery_steps"),
        _m(MemoryItem, models.MemoryItemRow),
        _m(KaggleSubmission, models.KaggleSubmissionRow),
    )
}


def mapping_for[D: Entity](domain: type[D]) -> Mapping[D, Any]:
    return MAPPINGS[domain]
