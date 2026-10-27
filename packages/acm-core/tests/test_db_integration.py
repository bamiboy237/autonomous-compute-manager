"""Database integration tests against the Compose Postgres (`just up` first)."""

from __future__ import annotations

import asyncio
import math
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from acm_core.db.base import Base
from acm_core.db.migrate import downgrade, upgrade
from acm_core.db.models import EMBEDDING_DIM
from acm_core.db.session import create_engine
from acm_core.domain import (
    Actor,
    AgentRole,
    Approval,
    ApprovalVerdict,
    Artifact,
    ArtifactKind,
    Attempt,
    AttemptStatus,
    Budget,
    BudgetLine,
    Campaign,
    Checkpoint,
    CodeVersion,
    ComputeTarget,
    Currency,
    Decision,
    DecisionKind,
    DecisionStatus,
    Experiment,
    FailureClass,
    Hypothesis,
    Incident,
    KaggleSubmission,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryKind,
    MemoryItem,
    MemorySourceKind,
    Metric,
    MetricDirection,
    ModelCall,
    Money,
    ProviderAccount,
    ProviderKind,
    ResourceRequest,
    Run,
    RunStatus,
    Workload,
)
from acm_core.domain.decision import ActionKind, MetricQueryEvidence, PriorDecisionEvidence
from acm_core.errors import (
    ConflictError,
    InvalidInputError,
    InvalidTransitionError,
    NotFoundError,
)
from acm_core.ids import LedgerTransactionId, ModelCallId
from acm_core.repos import UnitOfWork
from acm_core.settings import get_settings

pytestmark = pytest.mark.integration

Factory = async_sessionmaker[AsyncSession]
HASH = "sha256:" + "ab" * 32
SHA = "0123456789abcdef0123456789abcdef01234567"


class Graph:
    """One of every parent entity, persisted, so tests can hang children off them."""

    workload: Workload
    campaign: Campaign
    hypothesis: Hypothesis
    code_version: CodeVersion
    experiment: Experiment
    run: Run
    account: ProviderAccount
    target: ComputeTarget
    attempt: Attempt


async def seed(factory: Factory) -> Graph:
    g = Graph()
    g.workload = Workload(
        name="casmi", repo_url="https://example.com/r.git", entrypoint="python t.py"
    )
    g.campaign = Campaign(
        name="c",
        objective="maximize mrr",
        metric=Metric(name="mrr_at_25", direction=MetricDirection.MAXIMIZE, evaluator_ref="a.b:c"),
        workload_id=g.workload.id,
    )
    g.hypothesis = Hypothesis(campaign_id=g.campaign.id, statement="s", rationale="r")
    g.code_version = CodeVersion(workload_id=g.workload.id, git_sha=SHA, content_hash=HASH)
    g.experiment = Experiment(
        campaign_id=g.campaign.id,
        hypothesis_id=g.hypothesis.id,
        code_version_id=g.code_version.id,
        name="e",
        params={"lr": 0.1, "layers": [1, 2]},
        resources=ResourceRequest(gpus=1, gpu_type="a100", walltime=timedelta(hours=3)),
    )
    g.run = Run(campaign_id=g.campaign.id, experiment_id=g.experiment.id)
    g.account = ProviderAccount(kind=ProviderKind.SIMULATOR, name="sim", config={"seed": 1})
    g.target = ComputeTarget(provider_account_id=g.account.id, name="gpu")
    g.attempt = Attempt(
        run_id=g.run.id,
        ordinal=1,
        provider_account_id=g.account.id,
        compute_target_id=g.target.id,
        resources=g.experiment.resources,
    )
    async with UnitOfWork(factory) as uow:
        await uow.workloads.add(g.workload)
        await uow.campaigns.add(g.campaign)
        await uow.hypotheses.add(g.hypothesis)
        await uow.code_versions.add(g.code_version)
        await uow.experiments.add(g.experiment)
        await uow.runs.add(g.run)
        await uow.provider_accounts.add(g.account)
        await uow.compute_targets.add(g.target)
        await uow.attempts.add(g.attempt)
        await uow.commit()
    return g


def decision(g: Graph, **kw: Any) -> Decision:
    return Decision(
        campaign_id=g.campaign.id,
        kind=DecisionKind.RECOVERY,
        action=ActionKind.RETRY,
        rationale="because",
        proposed_by=Actor.agent(AgentRole.DIAGNOSTICIAN),
        **kw,
    )


# ---- migrations ----


async def test_migrate_up_down_up(database_url: str):
    await asyncio.to_thread(upgrade, database_url)
    engine = create_engine(get_settings().db, url=database_url)
    try:
        async with engine.connect() as conn:
            tables = set(
                await conn.scalars(
                    sa.text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                )
            )
            assert {"campaigns", "runs", "attempts", "memory_items", "ledger_entries"} <= tables
            ext = await conn.scalar(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
            assert ext == 1
            index = await conn.scalar(
                sa.text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname = 'ix_memory_items_embedding_hnsw'"
                )
            )
            assert "USING hnsw" in index
            assert "vector_cosine_ops" in index
        await asyncio.to_thread(downgrade, database_url, "base")
        async with engine.connect() as conn:
            tables = set(
                await conn.scalars(
                    sa.text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                )
            )
            assert tables == {"alembic_version"}
        await asyncio.to_thread(upgrade, database_url)
    finally:
        await engine.dispose()


async def test_models_match_migrations(engine):
    """No drift: autogenerate against the migrated schema finds nothing to change."""

    def diff(connection: sa.Connection) -> list[Any]:
        ctx = MigrationContext.configure(connection, opts={"compare_type": True})
        return compare_metadata(ctx, Base.metadata)

    async with engine.connect() as conn:
        assert await conn.run_sync(diff) == []


async def test_embedding_dimension_comes_from_settings(engine):
    async with engine.connect() as conn:
        typ = await conn.scalar(
            sa.text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = 'memory_items'::regclass AND attname = 'embedding'"
            )
        )
    assert typ == f"vector({EMBEDDING_DIM})"


# ---- CRUD for every aggregate ----


async def test_crud_every_aggregate(session_factory: Factory):
    g = await seed(session_factory)
    budget = Budget(campaign_id=g.campaign.id)
    budget = budget.model_copy(
        update={
            "lines": [
                BudgetLine(budget_id=budget.id, currency=Currency.USD, limit=Decimal("100")),
                BudgetLine(
                    budget_id=budget.id, currency=Currency.GPU_HOUR_NORM, limit=Decimal("40.5")
                ),
            ]
        }
    )
    ckpt = Checkpoint(
        run_id=g.run.id,
        attempt_id=g.attempt.id,
        step=100,
        manifest_uri="s3://acm/manifests/checkpoints/x/100.json",
        metrics={"loss": 0.25},
        provider_kind=ProviderKind.SIMULATOR,
        size_bytes=10,
    )
    art = Artifact(
        kind=ArtifactKind.MODEL,
        name="m",
        content_hash=HASH,
        uri="s3://a/b",
        size_bytes=5,
        campaign_id=g.campaign.id,
        run_id=g.run.id,
    )
    dec = decision(
        g,
        run_id=g.run.id,
        expected_cost=[Money(currency=Currency.USD, amount=Decimal("1.5"))],
        evidence=[
            MetricQueryEvidence(summary="loss", run_id=g.run.id, metric_keys=["loss"]),
            PriorDecisionEvidence(summary="before", decision_id=decision(g).id),
        ],
    )
    # the prior decision referenced by evidence is just an id inside JSON, not an FK
    approval = Approval(
        decision_id=dec.id, verdict=ApprovalVerdict.APPROVE, actor=Actor.human("u1"), comment="ok"
    )
    incident = Incident(
        campaign_id=g.campaign.id,
        run_id=g.run.id,
        attempt_id=g.attempt.id,
        failure_class=FailureClass.OOM_GPU,
        summary="oom",
        signals={"mem": 99},
    )
    call = ModelCall(
        agent=AgentRole.PLANNER,
        provider="meta",
        model="spark",
        input_tokens=1,
        output_tokens=2,
        latency_ms=3,
        cost=Decimal("0.001"),
        campaign_id=g.campaign.id,
    )
    sub = KaggleSubmission(
        campaign_id=g.campaign.id,
        competition="casmi",
        artifact_id=art.id,
        decision_id=dec.id,
        message="m",
    )
    mem = MemoryItem(
        campaign_id=g.campaign.id,
        source_kind=MemorySourceKind.RUN,
        source_id=str(g.run.id),
        summary="s",
        embedding=[0.5] * EMBEDDING_DIM,
        embedding_model="e",
    )

    async with UnitOfWork(session_factory) as uow:
        await uow.budgets.add(budget)
        await uow.checkpoints.add(ckpt)
        await uow.artifacts.add(art)
        await uow.decisions.add(dec)
        await uow.approvals.add(approval)
        await uow.incidents.add(incident)
        await uow.model_calls.add(call)
        await uow.kaggle_submissions.add(sub)
        await uow.memory.add(mem)
        await uow.commit()

    async with UnitOfWork(session_factory) as uow:
        assert await uow.workloads.get(g.workload.id) == g.workload
        assert await uow.campaigns.get(g.campaign.id) == g.campaign
        assert await uow.hypotheses.get(g.hypothesis.id) == g.hypothesis
        assert await uow.code_versions.get(g.code_version.id) == g.code_version
        assert await uow.experiments.get(g.experiment.id) == g.experiment
        assert await uow.runs.get(g.run.id) == g.run
        assert await uow.provider_accounts.get(g.account.id) == g.account
        assert await uow.compute_targets.get(g.target.id) == g.target
        assert await uow.attempts.get(g.attempt.id) == g.attempt
        assert await uow.budgets.get(budget.id) == budget
        assert await uow.checkpoints.get(ckpt.id) == ckpt
        assert await uow.artifacts.get(art.id) == art
        assert await uow.decisions.get(dec.id) == dec
        assert await uow.approvals.get(approval.id) == approval
        assert await uow.incidents.get(incident.id) == incident
        assert await uow.model_calls.get(call.id) == call
        assert await uow.kaggle_submissions.get(sub.id) == sub
        assert await uow.memory.get(mem.id) == mem

        # update every mutable aggregate
        campaign = g.campaign.model_copy(update={"name": "renamed"})
        await uow.campaigns.update(campaign)
        await uow.workloads.update(g.workload.model_copy(update={"image": "img:1"}))
        await uow.hypotheses.update(g.hypothesis.model_copy(update={"rationale": "r2"}))
        await uow.experiments.update(g.experiment.model_copy(update={"name": "e2"}))
        await uow.incidents.update(incident.model_copy(update={"summary": "oom again"}))
        await uow.kaggle_submissions.update(sub.model_copy(update={"public_score": 0.4}))
        await uow.provider_accounts.update(g.account.model_copy(update={"enabled": False}))
        await uow.compute_targets.update(g.target.model_copy(update={"preemptible": True}))
        await uow.memory.update(mem.model_copy(update={"summary": "s2"}))
        await uow.commit()

    async with UnitOfWork(session_factory) as uow:
        assert (await uow.campaigns.get(g.campaign.id)).name == "renamed"
        assert (await uow.incidents.get(incident.id)).summary == "oom again"
        assert (await uow.kaggle_submissions.get(sub.id)).public_score == 0.4
        assert (
            await uow.version(g.campaign.id)
            if False
            else await uow.campaigns.version(g.campaign.id) == 2
        )
        assert [c.id for c in await uow.campaigns.query(status=campaign.status)] == [g.campaign.id]
        assert await uow.runs.query(campaign_id=g.campaign.id, status=RunStatus.RUNNING) == []
        assert [r.id for r in await uow.runs.query(campaign_id=g.campaign.id)] == [g.run.id]
        with pytest.raises(InvalidInputError):
            await uow.runs.query(nonsense=1)
        with pytest.raises(NotFoundError):
            await uow.runs.get("run_01JB3V6Q1Z8K4M2N5P7R9S0T1W")
        assert await uow.runs.find("run_01JB3V6Q1Z8K4M2N5P7R9S0T1W") is None

    async with UnitOfWork(session_factory) as uow:
        await uow.budgets.update(
            budget.model_copy(
                update={"lines": [budget.lines[0].model_copy(update={"limit": Decimal("250")})]}
            )
        )
        await uow.commit()
    async with UnitOfWork(session_factory) as uow:
        loaded = await uow.budgets.get(budget.id)
        assert [(line.currency, line.limit) for line in loaded.lines] == [
            (Currency.USD, Decimal("250"))
        ]
        assert (await uow.budgets.find_by_campaign(g.campaign.id)).id == budget.id  # type: ignore[union-attr]
        await uow.budgets.delete(budget.id)
        await uow.commit()
    async with UnitOfWork(session_factory) as uow:
        assert await uow.budgets.find(budget.id) is None


async def test_immutable_records_reject_update(session_factory: Factory):
    g = await seed(session_factory)
    art = Artifact(
        kind=ArtifactKind.DATASET, name="d", content_hash=HASH, uri="s3://a", size_bytes=1
    )
    async with UnitOfWork(session_factory) as uow:
        await uow.artifacts.add(art)
        with pytest.raises(InvalidInputError):
            await uow.artifacts.update(art.model_copy(update={"name": "x"}))
        with pytest.raises(InvalidInputError):
            await uow.artifacts.delete(art.id)
    assert g


async def test_nan_metric_survives(session_factory: Factory):
    g = await seed(session_factory)
    async with UnitOfWork(session_factory) as uow:
        running = g.run.transition_to(RunStatus.RUNNING)
        done = running.transition_to(RunStatus.FAILED, reason="nan").model_copy(
            update={"final_metrics": {"loss": math.nan, "mrr": 0.1}}
        )
        await uow.runs.update(running)
        await uow.runs.update(done)
        await uow.commit()
    async with UnitOfWork(session_factory) as uow:
        loaded = await uow.runs.get(g.run.id)
        assert math.isnan(loaded.final_metrics["loss"])
        assert loaded.stop_reason == "nan"


# ---- constraints ----


async def test_state_transition_guard(session_factory: Factory):
    g = await seed(session_factory)
    async with UnitOfWork(session_factory) as uow:
        bad = g.run.model_copy(update={"status": RunStatus.SUCCEEDED})  # pending -> succeeded
        with pytest.raises(InvalidTransitionError):
            await uow.runs.update(bad)
        bad_attempt = g.attempt.model_copy(update={"status": AttemptStatus.RUNNING})
        with pytest.raises(InvalidTransitionError):
            await uow.attempts.update(bad_attempt)
        dec = decision(g)
        await uow.decisions.add(dec)
        with pytest.raises(InvalidTransitionError):
            await uow.decisions.update(dec.model_copy(update={"status": DecisionStatus.EXECUTED}))
        await uow.commit()

    async with UnitOfWork(session_factory) as uow:
        assert (await uow.runs.get(g.run.id)).status is RunStatus.PENDING
        # legal path, then a move out of a terminal status
        run = g.run.transition_to(RunStatus.RUNNING).transition_to(RunStatus.SUCCEEDED)
        await uow.runs.update(g.run.transition_to(RunStatus.RUNNING))
        await uow.runs.update(run)
        with pytest.raises(InvalidTransitionError):
            await uow.runs.update(run.model_copy(update={"status": RunStatus.RUNNING}))
        await uow.commit()


async def test_one_active_attempt_per_run(session_factory: Factory):
    g = await seed(session_factory)
    second = g.attempt.model_copy(update={"id": type(g.attempt.id).new(), "ordinal": 2})
    async with UnitOfWork(session_factory) as uow:
        with pytest.raises(ConflictError) as err:
            await uow.attempts.add(second)
    assert "uq_attempts_one_active_per_run" in str(err.value.details)

    # finishing the first attempt frees the slot; ordinals stay unique per run
    async with UnitOfWork(session_factory) as uow:
        await uow.attempts.update(g.attempt.transition_to(AttemptStatus.FAILED))
        await uow.attempts.add(second)
        await uow.commit()
    async with UnitOfWork(session_factory) as uow:
        dup = second.model_copy(
            update={"id": type(g.attempt.id).new(), "status": AttemptStatus.FAILED}
        )
        with pytest.raises(ConflictError):
            await uow.attempts.add(dup)


async def test_foreign_keys_and_checks(session_factory: Factory):
    g = await seed(session_factory)
    async with UnitOfWork(session_factory) as uow:
        orphan = g.hypothesis.model_copy(
            update={"id": type(g.hypothesis.id).new(), "campaign_id": type(g.campaign.id).new()}
        )
        with pytest.raises(ConflictError):
            await uow.hypotheses.add(orphan)
    async with UnitOfWork(session_factory) as uow:
        with pytest.raises(ConflictError):  # same workload + sha
            await uow.code_versions.add(
                g.code_version.model_copy(update={"id": type(g.code_version.id).new()})
            )
    async with UnitOfWork(session_factory) as uow:
        with pytest.raises(InvalidInputError):
            await uow.memory.add(
                MemoryItem(
                    source_kind=MemorySourceKind.RUN,
                    source_id="r",
                    summary="s",
                    embedding=[1.0],
                    embedding_model="e",
                )
            )


async def test_enum_check_constraint_rejects_unknown_status(engine):
    async with engine.begin() as conn:
        with pytest.raises(DBAPIError, match=r"ck_provider_accounts_provider_kind"):
            await conn.execute(
                sa.text(
                    "INSERT INTO provider_accounts (id, kind, name, config, enabled) "
                    "VALUES ('pa_x', 'bogus', 'n', '{}', true)"
                )
            )


async def test_deferred_cycle_foreign_key(session_factory: Factory):
    g = await seed(session_factory)
    async with UnitOfWork(session_factory) as uow:
        run = g.run.model_copy(update={"current_attempt_id": g.attempt.id})
        await uow.runs.update(run)  # allowed: attempt exists
        await uow.commit()
    async with UnitOfWork(session_factory) as uow:
        missing = type(g.attempt.id).new()
        await uow.runs.update(g.run.model_copy(update={"current_attempt_id": missing}))
        with pytest.raises(ConflictError):  # checked at commit
            await uow.commit()


# ---- optimistic concurrency ----


async def test_concurrent_update_conflict(session_factory: Factory):
    g = await seed(session_factory)
    async with UnitOfWork(session_factory) as a, UnitOfWork(session_factory) as b:
        run_a = await a.runs.get(g.run.id)
        run_b = await b.runs.get(g.run.id)  # both loaded at version 1
        await a.runs.update(run_a.transition_to(RunStatus.RUNNING))
        await a.commit()
        with pytest.raises(ConflictError):
            await b.runs.update(run_b.transition_to(RunStatus.STOPPED, reason="user"))
    async with UnitOfWork(session_factory) as uow:
        assert (await uow.runs.get(g.run.id)).status is RunStatus.RUNNING
        assert await uow.runs.version(g.run.id) == 2


async def test_expected_version(session_factory: Factory):
    g = await seed(session_factory)
    async with UnitOfWork(session_factory) as uow:
        v = await uow.runs.version(g.run.id)
    async with UnitOfWork(session_factory) as uow:
        await uow.runs.update(g.run.transition_to(RunStatus.RUNNING), expected_version=v)
        await uow.commit()
    async with UnitOfWork(session_factory) as uow:
        with pytest.raises(ConflictError):
            await uow.runs.update(g.run.transition_to(RunStatus.STOPPED), expected_version=v)


# ---- aggregates with child tables ----


async def test_decision_evidence_order_and_listing(session_factory: Factory):
    g = await seed(session_factory)
    refs = [
        MetricQueryEvidence(summary=f"e{i}", run_id=g.run.id, metric_keys=["loss"])
        for i in range(3)
    ]
    dec = decision(g, evidence=refs)
    async with UnitOfWork(session_factory) as uow:
        await uow.decisions.add(dec)
        await uow.commit()
    async with UnitOfWork(session_factory) as uow:
        [loaded] = await uow.decisions.query(campaign_id=g.campaign.id)
        assert [e.summary for e in loaded.evidence] == ["e0", "e1", "e2"]
        rows = await uow.decisions.evidence_rows(dec.id)
        assert [r.position for r in rows] == [0, 1, 2]
        approved = loaded.transition_to(DecisionStatus.APPROVED, actor=Actor.human("u"))
        await uow.decisions.update(approved)
        await uow.commit()
    async with UnitOfWork(session_factory) as uow:
        got = await uow.decisions.get(dec.id)
        assert got.status is DecisionStatus.APPROVED
        assert got.decided_by == Actor.human("u")
        assert len(got.evidence) == 3


def _entry(
    g: Graph, tx: LedgerTransactionId, account: str, direction: LedgerDirection, amount: str
) -> LedgerEntry:
    return LedgerEntry(
        transaction_id=tx,
        campaign_id=g.campaign.id,
        kind=LedgerEntryKind.RESERVATION,
        account=account,
        direction=direction,
        currency=Currency.USD,
        amount=Decimal(amount),
    )


async def test_ledger_is_append_only_and_balanced(session_factory: Factory):
    g = await seed(session_factory)
    tx = LedgerTransactionId.new()
    debit = _entry(g, tx, f"reserved:{g.run.id}", LedgerDirection.DEBIT, "5")
    credit = _entry(g, tx, f"budget:{g.campaign.id}", LedgerDirection.CREDIT, "5")
    async with UnitOfWork(session_factory) as uow:
        with pytest.raises(InvalidInputError):
            await uow.ledger.post([debit, credit.model_copy(update={"amount": Decimal("4")})])
        with pytest.raises(InvalidInputError):
            await uow.ledger.add(debit)
        await uow.ledger.post([debit, credit])
        await uow.commit()
    async with UnitOfWork(session_factory) as uow:
        assert len(await uow.ledger.query(campaign_id=g.campaign.id)) == 2
        assert await uow.ledger.net(g.campaign.id, f"reserved:{g.run.id}") == {"usd": Decimal("5")}
        assert await uow.ledger.net(g.campaign.id, f"budget:{g.campaign.id}") == {
            "usd": Decimal("-5")
        }
        assert ModelCallId


async def test_memory_search_nearest_first(session_factory: Factory):
    g = await seed(session_factory)

    def vec(i: int) -> list[float]:
        v = [0.0] * EMBEDDING_DIM
        v[i] = 1.0
        return v

    items = [
        MemoryItem(
            campaign_id=g.campaign.id,
            source_kind=MemorySourceKind.RUN,
            source_id=f"r{i}",
            summary=f"s{i}",
            embedding=vec(i),
            embedding_model="e",
        )
        for i in range(3)
    ]
    shared = MemoryItem(
        source_kind=MemorySourceKind.INCIDENT,
        source_id="i0",
        summary="shared",
        embedding=vec(5),
        embedding_model="e",
    )
    async with UnitOfWork(session_factory) as uow:
        for item in [*items, shared]:
            await uow.memory.add(item)
        await uow.commit()
    async with UnitOfWork(session_factory) as uow:
        hits = await uow.memory.search(vec(1), campaign_id=g.campaign.id, limit=2)
        assert hits[0][0].summary == "s1"
        assert hits[0][1] == pytest.approx(0.0, abs=1e-6)
        assert hits[1][1] == pytest.approx(1.0, abs=1e-6)
        all_hits = await uow.memory.search(vec(5), campaign_id=g.campaign.id, limit=5)
        assert all_hits[0][0].summary == "shared"  # shared items are searchable from any campaign
        with pytest.raises(InvalidInputError):
            await uow.memory.search([1.0])


async def test_rollback_without_commit(session_factory: Factory):
    g = await seed(session_factory)
    async with UnitOfWork(session_factory) as uow:
        await uow.workloads.add(Workload(name="tmp", repo_url="u", entrypoint="e"))
    async with UnitOfWork(session_factory) as uow:
        assert [w.id for w in await uow.workloads.query()] == [g.workload.id]


async def test_pgbouncer_mode_engine_works(migrated_database_url: str):
    settings = get_settings().db.model_copy(update={"pgbouncer": True})
    engine = create_engine(settings, url=migrated_database_url)
    try:
        async with engine.connect() as conn:
            for _ in range(3):  # repeated statements would reuse prepared ones without the flag
                assert await conn.scalar(sa.text("SELECT 1")) == 1
    finally:
        await engine.dispose()
