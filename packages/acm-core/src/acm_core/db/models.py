"""SQLAlchemy tables for every entity in ARCHITECTURE.md §4.

Column names equal the field names of the matching ``acm_core.domain`` model, so mapping
(``acm_core.db.mapping``) is mechanical. Conventions:

- ids are the prefixed-ULID strings from ``acm_core.ids``;
- statuses and other enums are VARCHAR with a named CHECK constraint;
- nested value objects (metric, policy, resources, actors, evidence refs, ...) are JSONB;
- mutable aggregates carry a ``version`` column for optimistic concurrency;
- ``runs.current_attempt_id``, ``attempts.resumed_from_checkpoint_id`` and
  ``code_versions.decision_id`` close FK cycles, so they are DEFERRABLE INITIALLY DEFERRED and
  created with ``use_alter``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column

from acm_core.db.base import Amount, Base, Json, Timestamped, Versioned, enum_type, fk
from acm_core.domain import (
    ActionKind,
    AgentRole,
    ApprovalVerdict,
    ArtifactKind,
    AttemptStatus,
    AutonomyMode,
    CampaignStatus,
    Currency,
    DecisionKind,
    DecisionStatus,
    ExperimentStatus,
    FailureClass,
    HypothesisStatus,
    IncidentStatus,
    KaggleSubmissionStatus,
    LedgerDirection,
    LedgerEntryKind,
    MemorySourceKind,
    ModelCallStatus,
    ProviderKind,
    RiskLevel,
    RunStatus,
)
from acm_core.settings import get_settings

__all__ = [
    "ACTIVE_ATTEMPT_STATUSES",
    "ApprovalRow",
    "ArtifactRow",
    "AttemptRow",
    "BudgetLineRow",
    "BudgetRow",
    "CampaignRow",
    "CheckpointRow",
    "CodeVersionRow",
    "ComputeTargetRow",
    "DecisionRow",
    "EvidenceRow",
    "ExperimentRow",
    "HypothesisRow",
    "IncidentRow",
    "KaggleSubmissionRow",
    "LedgerEntryRow",
    "MemoryItemRow",
    "ModelCallRow",
    "ProviderAccountRow",
    "RunRow",
    "WorkloadRow",
]

EMBEDDING_DIM = get_settings().db.embedding_dim

ACTIVE_ATTEMPT_STATUSES = (
    AttemptStatus.PENDING,
    AttemptStatus.SUBMITTED,
    AttemptStatus.RUNNING,
    AttemptStatus.CANCELLING,
)
"""Attempt statuses that occupy the run's single active-attempt slot."""


def _pk() -> Mapped[str]:
    return mapped_column(sa.String(64), primary_key=True)


def _tstz() -> Mapped[datetime | None]:
    return mapped_column(sa.DateTime(timezone=True))


def _created_index(table: str) -> sa.Index:
    return sa.Index(f"ix_{table}_created_at", "created_at")


class WorkloadRow(Timestamped, Versioned, Base):
    __tablename__ = "workloads"

    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(sa.String(200))
    repo_url: Mapped[str] = mapped_column(sa.Text)
    entrypoint: Mapped[str] = mapped_column(sa.Text)
    image: Mapped[str | None] = mapped_column(sa.Text)
    description: Mapped[str | None] = mapped_column(sa.Text)


class CodeVersionRow(Timestamped, Base):
    __tablename__ = "code_versions"
    __table_args__ = (
        sa.UniqueConstraint("workload_id", "git_sha"),
        sa.CheckConstraint("git_sha ~ '^[0-9a-f]{40}$'", name="git_sha_format"),
    )

    id: Mapped[str] = _pk()
    workload_id: Mapped[str] = fk("workloads.id", index=True)
    git_sha: Mapped[str] = mapped_column(sa.String(40))
    content_hash: Mapped[str] = mapped_column(sa.String(71))
    parent_id: Mapped[str | None] = fk("code_versions.id", nullable=True)
    decision_id: Mapped[str | None] = mapped_column(
        sa.String(64),
        sa.ForeignKey(
            "decisions.id",
            use_alter=True,
            name="fk_code_versions_decision_id_decisions",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    message: Mapped[str | None] = mapped_column(sa.Text)


class CampaignRow(Timestamped, Versioned, Base):
    __tablename__ = "campaigns"
    __table_args__ = (_created_index("campaigns"),)

    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(sa.String(200))
    objective: Mapped[str] = mapped_column(sa.Text)
    metric: Mapped[dict[str, Any]] = mapped_column(Json)
    workload_id: Mapped[str] = fk("workloads.id", index=True)
    autonomy: Mapped[AutonomyMode] = mapped_column(enum_type(AutonomyMode, "autonomy"))
    policy: Mapped[dict[str, Any]] = mapped_column(Json)
    status: Mapped[CampaignStatus] = mapped_column(
        enum_type(CampaignStatus, "campaign_status"), index=True
    )


class BudgetRow(Timestamped, Versioned, Base):
    __tablename__ = "budgets"

    id: Mapped[str] = _pk()
    campaign_id: Mapped[str] = fk("campaigns.id", unique=True)


class BudgetLineRow(Timestamped, Versioned, Base):
    __tablename__ = "budget_lines"
    __table_args__ = (
        sa.UniqueConstraint("budget_id", "currency"),
        sa.CheckConstraint('"limit" > 0', name="limit_positive"),
        sa.CheckConstraint("soft_threshold > 0 AND soft_threshold <= 1", name="soft_threshold"),
    )

    id: Mapped[str] = _pk()
    budget_id: Mapped[str] = fk("budgets.id")
    currency: Mapped[Currency] = mapped_column(enum_type(Currency, "currency"))
    limit: Mapped[Decimal] = mapped_column(Amount)
    soft_threshold: Mapped[Decimal] = mapped_column(sa.Numeric(5, 4))


class HypothesisRow(Timestamped, Versioned, Base):
    __tablename__ = "hypotheses"

    id: Mapped[str] = _pk()
    campaign_id: Mapped[str] = fk("campaigns.id", index=True)
    parent_id: Mapped[str | None] = fk("hypotheses.id", nullable=True, index=True)
    statement: Mapped[str] = mapped_column(sa.Text)
    rationale: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[HypothesisStatus] = mapped_column(
        enum_type(HypothesisStatus, "hypothesis_status"), index=True
    )
    evidence: Mapped[list[Any]] = mapped_column(Json)


class ExperimentRow(Timestamped, Versioned, Base):
    __tablename__ = "experiments"
    __table_args__ = (_created_index("experiments"),)

    id: Mapped[str] = _pk()
    campaign_id: Mapped[str] = fk("campaigns.id", index=True)
    hypothesis_id: Mapped[str] = fk("hypotheses.id", index=True)
    code_version_id: Mapped[str] = fk("code_versions.id")
    name: Mapped[str] = mapped_column(sa.String(200))
    params: Mapped[dict[str, Any]] = mapped_column(Json)
    resources: Mapped[dict[str, Any]] = mapped_column(Json)
    status: Mapped[ExperimentStatus] = mapped_column(
        enum_type(ExperimentStatus, "experiment_status"), index=True
    )


class RunRow(Timestamped, Versioned, Base):
    __tablename__ = "runs"
    __table_args__ = (_created_index("runs"),)

    id: Mapped[str] = _pk()
    campaign_id: Mapped[str] = fk("campaigns.id", index=True)
    experiment_id: Mapped[str] = fk("experiments.id", index=True)
    status: Mapped[RunStatus] = mapped_column(enum_type(RunStatus, "run_status"), index=True)
    current_attempt_id: Mapped[str | None] = mapped_column(
        sa.String(64),
        sa.ForeignKey(
            "attempts.id",
            use_alter=True,
            name="fk_runs_current_attempt_id_attempts",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    final_metrics: Mapped[dict[str, Any]] = mapped_column(Json)
    stop_reason: Mapped[str | None] = mapped_column(sa.Text)
    started_at: Mapped[datetime | None] = _tstz()
    finished_at: Mapped[datetime | None] = _tstz()


class ProviderAccountRow(Timestamped, Versioned, Base):
    __tablename__ = "provider_accounts"
    __table_args__ = (sa.UniqueConstraint("kind", "name"),)

    id: Mapped[str] = _pk()
    kind: Mapped[ProviderKind] = mapped_column(enum_type(ProviderKind, "provider_kind"))
    name: Mapped[str] = mapped_column(sa.String(200))
    config: Mapped[dict[str, Any]] = mapped_column(Json)
    credentials_ref: Mapped[str | None] = mapped_column(sa.Text)
    enabled: Mapped[bool] = mapped_column(sa.Boolean)


class ComputeTargetRow(Timestamped, Versioned, Base):
    __tablename__ = "compute_targets"
    __table_args__ = (sa.UniqueConstraint("provider_account_id", "name"),)

    id: Mapped[str] = _pk()
    provider_account_id: Mapped[str] = fk("provider_accounts.id")
    name: Mapped[str] = mapped_column(sa.String(200))
    gpu_type: Mapped[str | None] = mapped_column(sa.String(100))
    gpu_memory_gb: Mapped[int | None] = mapped_column(sa.Integer)
    gpus_per_node: Mapped[int] = mapped_column(sa.Integer)
    max_gpus: Mapped[int | None] = mapped_column(sa.Integer)
    max_walltime: Mapped[timedelta | None] = mapped_column(sa.Interval)
    preemptible: Mapped[bool] = mapped_column(sa.Boolean)
    price_per_gpu_hour: Mapped[dict[str, Any] | None] = mapped_column(Json)
    gpu_hour_norm_rate: Mapped[Decimal] = mapped_column(Amount)
    enabled: Mapped[bool] = mapped_column(sa.Boolean)


class AttemptRow(Timestamped, Versioned, Base):
    __tablename__ = "attempts"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "ordinal"),
        sa.Index(
            "uq_attempts_one_active_per_run",
            "run_id",
            unique=True,
            postgresql_where=sa.text(
                "status IN (" + ", ".join(f"'{s.value}'" for s in ACTIVE_ATTEMPT_STATUSES) + ")"
            ),
        ),
        sa.CheckConstraint("ordinal >= 1", name="ordinal_positive"),
    )

    id: Mapped[str] = _pk()
    run_id: Mapped[str] = fk("runs.id")
    ordinal: Mapped[int] = mapped_column(sa.Integer)
    provider_account_id: Mapped[str] = fk("provider_accounts.id")
    compute_target_id: Mapped[str] = fk("compute_targets.id")
    resources: Mapped[dict[str, Any]] = mapped_column(Json)
    status: Mapped[AttemptStatus] = mapped_column(
        enum_type(AttemptStatus, "attempt_status"), index=True
    )
    provider_job_ref: Mapped[str | None] = mapped_column(sa.Text)
    resumed_from_checkpoint_id: Mapped[str | None] = mapped_column(
        sa.String(64),
        sa.ForeignKey(
            "checkpoints.id",
            use_alter=True,
            name="fk_attempts_resumed_from_checkpoint_id_checkpoints",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    failure_class: Mapped[FailureClass | None] = mapped_column(
        enum_type(FailureClass, "attempt_failure_class")
    )
    exit_code: Mapped[int | None] = mapped_column(sa.Integer)
    submitted_at: Mapped[datetime | None] = _tstz()
    started_at: Mapped[datetime | None] = _tstz()
    finished_at: Mapped[datetime | None] = _tstz()


class CheckpointRow(Timestamped, Base):
    __tablename__ = "checkpoints"
    __table_args__ = (
        sa.UniqueConstraint("attempt_id", "step"),
        sa.CheckConstraint("step >= 0", name="step_non_negative"),
    )

    id: Mapped[str] = _pk()
    run_id: Mapped[str] = fk("runs.id", index=True)
    attempt_id: Mapped[str] = fk("attempts.id")
    step: Mapped[int] = mapped_column(sa.BigInteger)
    manifest_uri: Mapped[str] = mapped_column(sa.Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(Json)
    provider_kind: Mapped[ProviderKind] = mapped_column(
        enum_type(ProviderKind, "checkpoint_provider_kind")
    )
    size_bytes: Mapped[int | None] = mapped_column(sa.BigInteger)


class ArtifactRow(Timestamped, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        sa.Index("ix_artifacts_content_hash", "content_hash"),
        _created_index("artifacts"),
    )

    id: Mapped[str] = _pk()
    kind: Mapped[ArtifactKind] = mapped_column(enum_type(ArtifactKind, "artifact_kind"))
    name: Mapped[str] = mapped_column(sa.String(500))
    content_hash: Mapped[str] = mapped_column(sa.String(71))
    uri: Mapped[str] = mapped_column(sa.Text)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger)
    media_type: Mapped[str | None] = mapped_column(sa.String(200))
    campaign_id: Mapped[str | None] = fk("campaigns.id", nullable=True, index=True)
    run_id: Mapped[str | None] = fk("runs.id", nullable=True, index=True)


class IncidentRow(Timestamped, Versioned, Base):
    __tablename__ = "incidents"
    __table_args__ = (_created_index("incidents"),)

    id: Mapped[str] = _pk()
    campaign_id: Mapped[str] = fk("campaigns.id", index=True)
    run_id: Mapped[str] = fk("runs.id", index=True)
    attempt_id: Mapped[str] = fk("attempts.id")
    failure_class: Mapped[FailureClass] = mapped_column(
        enum_type(FailureClass, "incident_failure_class")
    )
    status: Mapped[IncidentStatus] = mapped_column(
        enum_type(IncidentStatus, "incident_status"), index=True
    )
    summary: Mapped[str] = mapped_column(sa.Text)
    signals: Mapped[dict[str, Any]] = mapped_column(Json)
    recovery_steps: Mapped[list[Any]] = mapped_column(Json)
    detected_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = _tstz()


class DecisionRow(Timestamped, Versioned, Base):
    __tablename__ = "decisions"
    __table_args__ = (_created_index("decisions"),)

    id: Mapped[str] = _pk()
    campaign_id: Mapped[str] = fk("campaigns.id", index=True)
    kind: Mapped[DecisionKind] = mapped_column(enum_type(DecisionKind, "decision_kind"))
    action: Mapped[ActionKind] = mapped_column(enum_type(ActionKind, "action_kind"))
    parameters: Mapped[dict[str, Any]] = mapped_column(Json)
    rationale: Mapped[str] = mapped_column(sa.Text)
    expected_value: Mapped[float | None] = mapped_column(sa.Float)
    expected_cost: Mapped[list[Any]] = mapped_column(Json)
    risk: Mapped[RiskLevel] = mapped_column(enum_type(RiskLevel, "risk_level"))
    run_id: Mapped[str | None] = fk("runs.id", nullable=True, index=True)
    experiment_id: Mapped[str | None] = fk("experiments.id", nullable=True)
    hypothesis_id: Mapped[str | None] = fk("hypotheses.id", nullable=True)
    incident_id: Mapped[str | None] = fk("incidents.id", nullable=True)
    status: Mapped[DecisionStatus] = mapped_column(
        enum_type(DecisionStatus, "decision_status"), index=True
    )
    proposed_by: Mapped[dict[str, Any]] = mapped_column(Json)
    decided_by: Mapped[dict[str, Any] | None] = mapped_column(Json)
    decided_at: Mapped[datetime | None] = _tstz()
    executed_at: Mapped[datetime | None] = _tstz()
    error: Mapped[str | None] = mapped_column(sa.Text)


class EvidenceRow(Timestamped, Base):
    __tablename__ = "evidence"
    __table_args__ = (
        sa.UniqueConstraint("decision_id", "position"),
        sa.CheckConstraint("position >= 0", name="position_non_negative"),
    )

    id: Mapped[str] = _pk()
    decision_id: Mapped[str] = fk("decisions.id")
    position: Mapped[int] = mapped_column(sa.Integer)
    ref: Mapped[dict[str, Any]] = mapped_column(Json)


class ApprovalRow(Timestamped, Base):
    __tablename__ = "approvals"

    id: Mapped[str] = _pk()
    decision_id: Mapped[str] = fk("decisions.id", index=True)
    verdict: Mapped[ApprovalVerdict] = mapped_column(enum_type(ApprovalVerdict, "approval_verdict"))
    actor: Mapped[dict[str, Any]] = mapped_column(Json)
    comment: Mapped[str | None] = mapped_column(sa.Text)


class LedgerEntryRow(Timestamped, Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        sa.CheckConstraint("amount > 0", name="amount_positive"),
        sa.Index("ix_ledger_entries_transaction_id", "transaction_id"),
        sa.Index("ix_ledger_entries_campaign_id_account", "campaign_id", "account"),
        _created_index("ledger_entries"),
    )

    id: Mapped[str] = _pk()
    transaction_id: Mapped[str] = mapped_column(sa.String(64))
    campaign_id: Mapped[str] = fk("campaigns.id")
    kind: Mapped[LedgerEntryKind] = mapped_column(enum_type(LedgerEntryKind, "ledger_entry_kind"))
    account: Mapped[str] = mapped_column(sa.String(300))
    direction: Mapped[LedgerDirection] = mapped_column(
        enum_type(LedgerDirection, "ledger_direction")
    )
    currency: Mapped[Currency] = mapped_column(enum_type(Currency, "ledger_currency"))
    amount: Mapped[Decimal] = mapped_column(Amount)
    normalized_amount: Mapped[Decimal | None] = mapped_column(Amount)
    attempt_id: Mapped[str | None] = fk("attempts.id", nullable=True, index=True)
    run_id: Mapped[str | None] = fk("runs.id", nullable=True, index=True)
    model_call_id: Mapped[str | None] = fk("model_calls.id", nullable=True)
    memo: Mapped[str | None] = mapped_column(sa.Text)


class ModelCallRow(Timestamped, Base):
    __tablename__ = "model_calls"
    __table_args__ = (_created_index("model_calls"),)

    id: Mapped[str] = _pk()
    agent: Mapped[AgentRole] = mapped_column(enum_type(AgentRole, "agent_role"))
    provider: Mapped[str] = mapped_column(sa.String(100))
    model: Mapped[str] = mapped_column(sa.String(200))
    input_tokens: Mapped[int] = mapped_column(sa.Integer)
    output_tokens: Mapped[int] = mapped_column(sa.Integer)
    latency_ms: Mapped[int] = mapped_column(sa.Integer)
    cost: Mapped[Decimal] = mapped_column(Amount)
    status: Mapped[ModelCallStatus] = mapped_column(enum_type(ModelCallStatus, "model_call_status"))
    error: Mapped[str | None] = mapped_column(sa.Text)
    campaign_id: Mapped[str | None] = fk("campaigns.id", nullable=True, index=True)
    run_id: Mapped[str | None] = fk("runs.id", nullable=True, index=True)
    decision_id: Mapped[str | None] = fk("decisions.id", nullable=True)


class MemoryItemRow(Timestamped, Versioned, Base):
    __tablename__ = "memory_items"
    __table_args__ = (
        sa.UniqueConstraint("source_kind", "source_id"),
        sa.Index(
            "ix_memory_items_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        sa.CheckConstraint(
            "(embedding IS NULL) = (embedding_model IS NULL)", name="embedding_with_model"
        ),
    )

    id: Mapped[str] = _pk()
    campaign_id: Mapped[str | None] = fk("campaigns.id", nullable=True, index=True)
    source_kind: Mapped[MemorySourceKind] = mapped_column(
        enum_type(MemorySourceKind, "memory_source_kind")
    )
    source_id: Mapped[str] = mapped_column(sa.String(64))
    summary: Mapped[str] = mapped_column(sa.Text)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(sa.String(200))


class KaggleSubmissionRow(Timestamped, Versioned, Base):
    __tablename__ = "kaggle_submissions"
    __table_args__ = (_created_index("kaggle_submissions"),)

    id: Mapped[str] = _pk()
    campaign_id: Mapped[str] = fk("campaigns.id", index=True)
    competition: Mapped[str] = mapped_column(sa.String(200))
    artifact_id: Mapped[str] = fk("artifacts.id")
    run_id: Mapped[str | None] = fk("runs.id", nullable=True)
    decision_id: Mapped[str] = fk("decisions.id")
    message: Mapped[str] = mapped_column(sa.String(500))
    status: Mapped[KaggleSubmissionStatus] = mapped_column(
        enum_type(KaggleSubmissionStatus, "kaggle_submission_status"), index=True
    )
    kaggle_ref: Mapped[str | None] = mapped_column(sa.Text)
    public_score: Mapped[float | None] = mapped_column(sa.Float)
    private_score: Mapped[float | None] = mapped_column(sa.Float)
    error: Mapped[str | None] = mapped_column(sa.Text)
    submitted_at: Mapped[datetime | None] = _tstz()
