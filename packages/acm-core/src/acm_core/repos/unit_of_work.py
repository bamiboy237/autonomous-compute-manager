"""Async unit of work: one transaction, one session, every repository."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from acm_core.repos import entities as e
from acm_core.repos.base import translate_db_errors

__all__ = ["UnitOfWork"]


class UnitOfWork:
    """Scope of one database transaction.

    Leaving the block without ``commit()`` (or with an exception) rolls back. Repositories
    share the session, so a change and its guard checks see each other. A conflicting
    concurrent change surfaces as ``ConflictError`` from the repository call or from
    ``commit()`` (deferred foreign keys are checked there).
    """

    session: AsyncSession

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def __aenter__(self) -> Self:
        s = self.session = self._factory()
        self.workloads = e.WorkloadRepo(s)
        self.code_versions = e.CodeVersionRepo(s)
        self.campaigns = e.CampaignRepo(s)
        self.budgets = e.BudgetRepo(s)
        self.hypotheses = e.HypothesisRepo(s)
        self.experiments = e.ExperimentRepo(s)
        self.runs = e.RunRepo(s)
        self.attempts = e.AttemptRepo(s)
        self.provider_accounts = e.ProviderAccountRepo(s)
        self.compute_targets = e.ComputeTargetRepo(s)
        self.checkpoints = e.CheckpointRepo(s)
        self.artifacts = e.ArtifactRepo(s)
        self.decisions = e.DecisionRepo(s)
        self.approvals = e.ApprovalRepo(s)
        self.ledger = e.LedgerRepo(s)
        self.model_calls = e.ModelCallRepo(s)
        self.incidents = e.IncidentRepo(s)
        self.memory = e.MemoryRepo(s)
        self.kaggle_submissions = e.KaggleSubmissionRepo(s)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await self.session.rollback()  # no-op after commit
        finally:
            await self.session.close()

    async def commit(self) -> None:
        await translate_db_errors(self.session.commit)

    async def rollback(self) -> None:
        await self.session.rollback()
