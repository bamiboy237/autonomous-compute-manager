"""Concrete repositories: one per entity, with extras where an aggregate spans tables."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from pydantic import TypeAdapter

from acm_core.db import models
from acm_core.db.mapping import mapping_for
from acm_core.db.models import EMBEDDING_DIM
from acm_core.domain import (
    ATTEMPT_STATE_MACHINE,
    DECISION_STATE_MACHINE,
    RUN_STATE_MACHINE,
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
    EvidenceRef,
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
    check_balanced,
)
from acm_core.errors import InvalidInputError
from acm_core.repos.base import Repository, translate_db_errors

_EVIDENCE_REF = TypeAdapter[Any](EvidenceRef)


class WorkloadRepo(Repository[Workload]):
    domain = Workload


class CodeVersionRepo(Repository[CodeVersion]):
    domain = CodeVersion
    mutable = False


class CampaignRepo(Repository[Campaign]):
    domain = Campaign


class HypothesisRepo(Repository[Hypothesis]):
    domain = Hypothesis


class ExperimentRepo(Repository[Experiment]):
    domain = Experiment


class RunRepo(Repository[Run]):
    domain = Run
    state_machine = RUN_STATE_MACHINE


class AttemptRepo(Repository[Attempt]):
    domain = Attempt
    state_machine = ATTEMPT_STATE_MACHINE


class ProviderAccountRepo(Repository[ProviderAccount]):
    domain = ProviderAccount


class ComputeTargetRepo(Repository[ComputeTarget]):
    domain = ComputeTarget


class CheckpointRepo(Repository[Checkpoint]):
    domain = Checkpoint
    mutable = False


class ArtifactRepo(Repository[Artifact]):
    domain = Artifact
    mutable = False


class IncidentRepo(Repository[Incident]):
    domain = Incident


class ApprovalRepo(Repository[Approval]):
    domain = Approval
    mutable = False


class ModelCallRepo(Repository[ModelCall]):
    domain = ModelCall
    mutable = False


class KaggleSubmissionRepo(Repository[KaggleSubmission]):
    domain = KaggleSubmission


class BudgetRepo(Repository[Budget]):
    """A budget and its lines (one per currency) are one aggregate.

    ``update`` upserts the lines in the entity and deletes lines that are no longer in it.
    """

    domain = Budget
    _lines = mapping_for(BudgetLine)

    async def find_by_campaign(self, campaign_id: str) -> Budget | None:
        row = await self.session.scalar(
            sa.select(models.BudgetRow).where(models.BudgetRow.campaign_id == str(campaign_id))
        )
        return None if row is None else await self._load(row)

    async def _line_rows(self, budget_id: str) -> Sequence[models.BudgetLineRow]:
        return (
            await self.session.scalars(
                sa.select(models.BudgetLineRow)
                .where(models.BudgetLineRow.budget_id == str(budget_id))
                .order_by(models.BudgetLineRow.created_at, models.BudgetLineRow.id)
            )
        ).all()

    async def _load(self, row: Any) -> Budget:
        lines = [self._lines.from_row(r) for r in await self._line_rows(row.id)]
        return self.mapping.from_row(row, lines=lines)

    async def _after_add(self, entity: Budget) -> None:
        await translate_db_errors(self.session.flush)  # the budget row precedes its lines
        for line in entity.lines:
            self.session.add(self._lines.to_row(line))

    async def _after_update(self, entity: Budget) -> None:
        existing = {r.id: r for r in await self._line_rows(entity.id)}
        keep = {str(line.id) for line in entity.lines}
        for line in entity.lines:
            row = existing.get(str(line.id))
            if row is None:
                self.session.add(self._lines.to_row(line))
                continue
            for name, value in self._lines.to_values(line).items():
                if name not in ("id", "created_at"):
                    setattr(row, name, value)
        for line_id, row in existing.items():
            if line_id not in keep:
                await self.session.delete(row)

    async def delete(self, entity_id: str) -> None:
        for row in await self._line_rows(entity_id):
            await self.session.delete(row)
        await translate_db_errors(self.session.flush)
        await super().delete(entity_id)


class DecisionRepo(Repository[Decision]):
    """A decision and its ``Evidence`` rows.

    Evidence is written when the decision is added and is not changed by ``update``: it is
    the record of what the proposer saw.
    """

    domain = Decision
    state_machine = DECISION_STATE_MACHINE
    _evidence = mapping_for(Evidence)

    async def _after_add(self, entity: Decision) -> None:
        await translate_db_errors(self.session.flush)  # the decision row precedes its evidence
        for position, ref in enumerate(entity.evidence):
            item = Evidence(decision_id=entity.id, position=position, ref=ref)
            self.session.add(self._evidence.to_row(item))

    async def _evidence_for(self, ids: Sequence[str]) -> dict[str, list[Any]]:
        rows = (
            await self.session.scalars(
                sa.select(models.EvidenceRow)
                .where(models.EvidenceRow.decision_id.in_(ids))
                .order_by(models.EvidenceRow.decision_id, models.EvidenceRow.position)
            )
        ).all()
        grouped: dict[str, list[Any]] = {}
        for r in rows:
            grouped.setdefault(r.decision_id, []).append(_EVIDENCE_REF.validate_python(r.ref))
        return grouped

    async def _load(self, row: Any) -> Decision:
        evidence = (await self._evidence_for([row.id])).get(row.id, [])
        return self.mapping.from_row(row, evidence=evidence)

    async def _load_many(self, rows: Sequence[Any]) -> list[Decision]:
        evidence = await self._evidence_for([r.id for r in rows]) if rows else {}
        return [self.mapping.from_row(r, evidence=evidence.get(r.id, [])) for r in rows]

    async def evidence_rows(self, decision_id: str) -> list[Evidence]:
        rows = (
            await self.session.scalars(
                sa.select(models.EvidenceRow)
                .where(models.EvidenceRow.decision_id == str(decision_id))
                .order_by(models.EvidenceRow.position)
            )
        ).all()
        return [self._evidence.from_row(r) for r in rows]


class LedgerRepo(Repository[LedgerEntry]):
    """Append-only. Entries are posted as balanced transactions with ``post``."""

    domain = LedgerEntry
    mutable = False

    async def add(self, entity: LedgerEntry) -> LedgerEntry:
        raise InvalidInputError("post ledger entries as a balanced transaction with post()")

    async def post(self, entries: Sequence[LedgerEntry]) -> Sequence[LedgerEntry]:
        """Insert one balanced transaction (``check_balanced``), all or nothing."""
        check_balanced(entries)
        for entry in entries:
            self.session.add(self.mapping.to_row(entry))
        await translate_db_errors(self.session.flush)
        return entries

    async def net(self, campaign_id: str, account: str) -> dict[str, Decimal]:
        """Debits minus credits per currency for one account of a campaign."""
        sign = sa.case(
            (models.LedgerEntryRow.direction == "debit", models.LedgerEntryRow.amount),
            else_=-models.LedgerEntryRow.amount,
        )
        rows = await self.session.execute(
            sa.select(models.LedgerEntryRow.currency, sa.func.sum(sign))
            .where(
                models.LedgerEntryRow.campaign_id == str(campaign_id),
                models.LedgerEntryRow.account == account,
            )
            .group_by(models.LedgerEntryRow.currency)
        )
        return {currency.value: total for currency, total in rows.all()}


class MemoryRepo(Repository[MemoryItem]):
    domain = MemoryItem

    @staticmethod
    def _check_dim(embedding: Sequence[float]) -> None:
        if len(embedding) != EMBEDDING_DIM:
            raise InvalidInputError(
                f"embedding has {len(embedding)} dimensions, the schema has {EMBEDDING_DIM}"
            )

    async def add(self, entity: MemoryItem) -> MemoryItem:
        if entity.embedding is not None:
            self._check_dim(entity.embedding)
        return await super().add(entity)

    async def update(
        self, entity: MemoryItem, *, expected_version: int | None = None
    ) -> MemoryItem:
        if entity.embedding is not None:
            self._check_dim(entity.embedding)
        return await super().update(entity, expected_version=expected_version)

    async def search(
        self, embedding: Sequence[float], *, campaign_id: str | None = None, limit: int = 10
    ) -> list[tuple[MemoryItem, float]]:
        """Nearest items by cosine distance (smaller is closer). Includes shared items
        (``campaign_id`` null) when a campaign is given."""
        self._check_dim(embedding)
        row = models.MemoryItemRow
        distance = row.embedding.cosine_distance(list(embedding))
        query = sa.select(row, distance.label("distance")).where(row.embedding.is_not(None))
        if campaign_id is not None:
            query = query.where(
                sa.or_(row.campaign_id == str(campaign_id), row.campaign_id.is_(None))
            )
        result = await self.session.execute(query.order_by(distance).limit(limit))
        return [(self.mapping.from_row(r), float(d)) for r, d in result.all()]


__all__ = [
    "ApprovalRepo",
    "ArtifactRepo",
    "AttemptRepo",
    "BudgetRepo",
    "CampaignRepo",
    "CheckpointRepo",
    "CodeVersionRepo",
    "ComputeTargetRepo",
    "DecisionRepo",
    "ExperimentRepo",
    "HypothesisRepo",
    "IncidentRepo",
    "KaggleSubmissionRepo",
    "LedgerRepo",
    "MemoryRepo",
    "ModelCallRepo",
    "ProviderAccountRepo",
    "RunRepo",
    "WorkloadRepo",
]
