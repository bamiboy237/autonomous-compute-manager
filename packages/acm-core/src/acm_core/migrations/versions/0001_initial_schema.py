"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-27 12:42:12.207191
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from acm_core.settings import get_settings

EMBEDDING_DIM = (
    get_settings().db.embedding_dim
)  # fixed when the migration runs (ACM_DB__EMBEDDING_DIM)

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "provider_accounts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "slurm",
                "modal",
                "thundercompute",
                "kubernetes",
                "local",
                "simulator",
                name="provider_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("credentials_ref", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_accounts")),
        sa.UniqueConstraint("kind", "name", name=op.f("uq_provider_accounts_kind_name")),
    )
    op.create_table(
        "workloads",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("repo_url", sa.Text(), nullable=False),
        sa.Column("entrypoint", sa.Text(), nullable=False),
        sa.Column("image", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workloads")),
    )
    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("metric", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("workload_id", sa.String(length=64), nullable=False),
        sa.Column(
            "autonomy",
            sa.Enum(
                "advisory", "gated", name="autonomy", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "running",
                "paused",
                "completed",
                "stopped",
                name="campaign_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["workload_id"], ["workloads.id"], name=op.f("fk_campaigns_workload_id_workloads")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
    )
    op.create_index("ix_campaigns_created_at", "campaigns", ["created_at"], unique=False)
    op.create_index(op.f("ix_campaigns_status"), "campaigns", ["status"], unique=False)
    op.create_index(op.f("ix_campaigns_workload_id"), "campaigns", ["workload_id"], unique=False)
    op.create_table(
        "code_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workload_id", sa.String(length=64), nullable=False),
        sa.Column("git_sha", sa.String(length=40), nullable=False),
        sa.Column("content_hash", sa.String(length=71), nullable=False),
        sa.Column("parent_id", sa.String(length=64), nullable=True),
        sa.Column("decision_id", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "git_sha ~ '^[0-9a-f]{40}$'", name=op.f("ck_code_versions_git_sha_format")
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["code_versions.id"],
            name=op.f("fk_code_versions_parent_id_code_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["workload_id"], ["workloads.id"], name=op.f("fk_code_versions_workload_id_workloads")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_code_versions")),
        sa.UniqueConstraint(
            "workload_id", "git_sha", name=op.f("uq_code_versions_workload_id_git_sha")
        ),
    )
    op.create_index(
        op.f("ix_code_versions_workload_id"), "code_versions", ["workload_id"], unique=False
    )
    op.create_table(
        "compute_targets",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("provider_account_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("gpu_type", sa.String(length=100), nullable=True),
        sa.Column("gpu_memory_gb", sa.Integer(), nullable=True),
        sa.Column("gpus_per_node", sa.Integer(), nullable=False),
        sa.Column("max_gpus", sa.Integer(), nullable=True),
        sa.Column("max_walltime", sa.Interval(), nullable=True),
        sa.Column("preemptible", sa.Boolean(), nullable=False),
        sa.Column("price_per_gpu_hour", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("gpu_hour_norm_rate", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_account_id"],
            ["provider_accounts.id"],
            name=op.f("fk_compute_targets_provider_account_id_provider_accounts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compute_targets")),
        sa.UniqueConstraint(
            "provider_account_id", "name", name=op.f("uq_compute_targets_provider_account_id_name")
        ),
    )
    op.create_table(
        "budgets",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name=op.f("fk_budgets_campaign_id_campaigns")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budgets")),
        sa.UniqueConstraint("campaign_id", name=op.f("uq_budgets_campaign_id")),
    )
    op.create_table(
        "hypotheses",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("parent_id", sa.String(length=64), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "testing",
                "supported",
                "refuted",
                "abandoned",
                name="hypothesis_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name=op.f("fk_hypotheses_campaign_id_campaigns")
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["hypotheses.id"], name=op.f("fk_hypotheses_parent_id_hypotheses")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hypotheses")),
    )
    op.create_index(op.f("ix_hypotheses_campaign_id"), "hypotheses", ["campaign_id"], unique=False)
    op.create_index(op.f("ix_hypotheses_parent_id"), "hypotheses", ["parent_id"], unique=False)
    op.create_index(op.f("ix_hypotheses_status"), "hypotheses", ["status"], unique=False)
    op.create_table(
        "memory_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=True),
        sa.Column(
            "source_kind",
            sa.Enum(
                "run",
                "incident",
                "decision",
                name="memory_source_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "(embedding IS NULL) = (embedding_model IS NULL)",
            name=op.f("ck_memory_items_embedding_with_model"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name=op.f("fk_memory_items_campaign_id_campaigns")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_items")),
        sa.UniqueConstraint(
            "source_kind", "source_id", name=op.f("uq_memory_items_source_kind_source_id")
        ),
    )
    op.create_index(
        op.f("ix_memory_items_campaign_id"), "memory_items", ["campaign_id"], unique=False
    )
    op.create_index(
        "ix_memory_items_embedding_hnsw",
        "memory_items",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "budget_lines",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("budget_id", sa.String(length=64), nullable=False),
        sa.Column(
            "currency",
            sa.Enum(
                "usd",
                "slurm_su",
                "gpu_hour_norm",
                "llm_usd",
                name="currency",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("limit", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("soft_threshold", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint('"limit" > 0', name=op.f("ck_budget_lines_limit_positive")),
        sa.CheckConstraint(
            "soft_threshold > 0 AND soft_threshold <= 1",
            name=op.f("ck_budget_lines_soft_threshold"),
        ),
        sa.ForeignKeyConstraint(
            ["budget_id"], ["budgets.id"], name=op.f("fk_budget_lines_budget_id_budgets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_lines")),
        sa.UniqueConstraint(
            "budget_id", "currency", name=op.f("uq_budget_lines_budget_id_currency")
        ),
    )
    op.create_table(
        "experiments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("hypothesis_id", sa.String(length=64), nullable=False),
        sa.Column("code_version_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "planned",
                "running",
                "completed",
                "abandoned",
                name="experiment_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name=op.f("fk_experiments_campaign_id_campaigns")
        ),
        sa.ForeignKeyConstraint(
            ["code_version_id"],
            ["code_versions.id"],
            name=op.f("fk_experiments_code_version_id_code_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"],
            ["hypotheses.id"],
            name=op.f("fk_experiments_hypothesis_id_hypotheses"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiments")),
    )
    op.create_index(
        op.f("ix_experiments_campaign_id"), "experiments", ["campaign_id"], unique=False
    )
    op.create_index("ix_experiments_created_at", "experiments", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_experiments_hypothesis_id"), "experiments", ["hypothesis_id"], unique=False
    )
    op.create_index(op.f("ix_experiments_status"), "experiments", ["status"], unique=False)
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "recovering",
                "succeeded",
                "failed",
                "stopped",
                name="run_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("current_attempt_id", sa.String(length=64), nullable=True),
        sa.Column("final_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name=op.f("fk_runs_campaign_id_campaigns")
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"], ["experiments.id"], name=op.f("fk_runs_experiment_id_experiments")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
    )
    op.create_index(op.f("ix_runs_campaign_id"), "runs", ["campaign_id"], unique=False)
    op.create_index("ix_runs_created_at", "runs", ["created_at"], unique=False)
    op.create_index(op.f("ix_runs_experiment_id"), "runs", ["experiment_id"], unique=False)
    op.create_index(op.f("ix_runs_status"), "runs", ["status"], unique=False)
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "dataset",
                "model",
                "submission",
                "report",
                "other",
                name="artifact_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=71), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=200), nullable=True),
        sa.Column("campaign_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name=op.f("fk_artifacts_campaign_id_campaigns")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_artifacts_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifacts")),
    )
    op.create_index(op.f("ix_artifacts_campaign_id"), "artifacts", ["campaign_id"], unique=False)
    op.create_index("ix_artifacts_content_hash", "artifacts", ["content_hash"], unique=False)
    op.create_index("ix_artifacts_created_at", "artifacts", ["created_at"], unique=False)
    op.create_index(op.f("ix_artifacts_run_id"), "artifacts", ["run_id"], unique=False)
    op.create_table(
        "attempts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("provider_account_id", sa.String(length=64), nullable=False),
        sa.Column("compute_target_id", sa.String(length=64), nullable=False),
        sa.Column("resources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "submitted",
                "running",
                "cancelling",
                "succeeded",
                "failed",
                "preempted",
                "cancelled",
                name="attempt_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("provider_job_ref", sa.Text(), nullable=True),
        sa.Column("resumed_from_checkpoint_id", sa.String(length=64), nullable=True),
        sa.Column(
            "failure_class",
            sa.Enum(
                "oom_gpu",
                "oom_host",
                "nan_divergence",
                "loss_plateau",
                "throughput_regression",
                "stall_no_heartbeat",
                "dataloader_hang",
                "nccl_error",
                "preempted",
                "walltime_exceeded",
                "node_failure",
                "env_import_error",
                "data_missing",
                "quota_exceeded",
                "queue_starvation",
                "unknown",
                name="attempt_failure_class",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("ordinal >= 1", name=op.f("ck_attempts_ordinal_positive")),
        sa.ForeignKeyConstraint(
            ["compute_target_id"],
            ["compute_targets.id"],
            name=op.f("fk_attempts_compute_target_id_compute_targets"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_account_id"],
            ["provider_accounts.id"],
            name=op.f("fk_attempts_provider_account_id_provider_accounts"),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_attempts_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attempts")),
        sa.UniqueConstraint("run_id", "ordinal", name=op.f("uq_attempts_run_id_ordinal")),
    )
    op.create_index(op.f("ix_attempts_status"), "attempts", ["status"], unique=False)
    op.create_index(
        "uq_attempts_one_active_per_run",
        "attempts",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'submitted', 'running', 'cancelling')"),
    )
    op.create_table(
        "checkpoints",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_id", sa.String(length=64), nullable=False),
        sa.Column("step", sa.BigInteger(), nullable=False),
        sa.Column("manifest_uri", sa.Text(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "provider_kind",
            sa.Enum(
                "slurm",
                "modal",
                "thundercompute",
                "kubernetes",
                "local",
                "simulator",
                name="checkpoint_provider_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("step >= 0", name=op.f("ck_checkpoints_step_non_negative")),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["attempts.id"], name=op.f("fk_checkpoints_attempt_id_attempts")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_checkpoints_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checkpoints")),
        sa.UniqueConstraint("attempt_id", "step", name=op.f("uq_checkpoints_attempt_id_step")),
    )
    op.create_index(op.f("ix_checkpoints_run_id"), "checkpoints", ["run_id"], unique=False)
    op.create_table(
        "incidents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_id", sa.String(length=64), nullable=False),
        sa.Column(
            "failure_class",
            sa.Enum(
                "oom_gpu",
                "oom_host",
                "nan_divergence",
                "loss_plateau",
                "throughput_regression",
                "stall_no_heartbeat",
                "dataloader_hang",
                "nccl_error",
                "preempted",
                "walltime_exceeded",
                "node_failure",
                "env_import_error",
                "data_missing",
                "quota_exceeded",
                "queue_starvation",
                "unknown",
                name="incident_failure_class",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "recovering",
                "resolved",
                "escalated",
                "abandoned",
                name="incident_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recovery_steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["attempts.id"], name=op.f("fk_incidents_attempt_id_attempts")
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name=op.f("fk_incidents_campaign_id_campaigns")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_incidents_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incidents")),
    )
    op.create_index(op.f("ix_incidents_campaign_id"), "incidents", ["campaign_id"], unique=False)
    op.create_index("ix_incidents_created_at", "incidents", ["created_at"], unique=False)
    op.create_index(op.f("ix_incidents_run_id"), "incidents", ["run_id"], unique=False)
    op.create_index(op.f("ix_incidents_status"), "incidents", ["status"], unique=False)
    op.create_table(
        "decisions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "recovery",
                "experiment",
                "allocation",
                "code_change",
                "submission",
                name="decision_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "action",
            sa.Enum(
                "retry",
                "resume_from_checkpoint",
                "reduce_batch_with_grad_accum",
                "lower_lr_and_resume",
                "increase_walltime",
                "move_provider_via_checkpoint",
                "patch_code",
                "abandon_with_evidence",
                "early_stop",
                "start_experiment",
                "reallocate_budget",
                "submit_to_kaggle",
                name="action_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("expected_cost", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "risk",
            sa.Enum(
                "low",
                "medium",
                "high",
                name="risk_level",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("experiment_id", sa.String(length=64), nullable=True),
        sa.Column("hypothesis_id", sa.String(length=64), nullable=True),
        sa.Column("incident_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "proposed",
                "approved",
                "rejected",
                "executed",
                "failed",
                "expired",
                name="decision_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("proposed_by", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decided_by", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name=op.f("fk_decisions_campaign_id_campaigns")
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name=op.f("fk_decisions_experiment_id_experiments"),
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"], ["hypotheses.id"], name=op.f("fk_decisions_hypothesis_id_hypotheses")
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"], name=op.f("fk_decisions_incident_id_incidents")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_decisions_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decisions")),
    )
    op.create_index(op.f("ix_decisions_campaign_id"), "decisions", ["campaign_id"], unique=False)
    op.create_index("ix_decisions_created_at", "decisions", ["created_at"], unique=False)
    op.create_index(op.f("ix_decisions_run_id"), "decisions", ["run_id"], unique=False)
    op.create_index(op.f("ix_decisions_status"), "decisions", ["status"], unique=False)
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("decision_id", sa.String(length=64), nullable=False),
        sa.Column(
            "verdict",
            sa.Enum(
                "approve",
                "reject",
                name="approval_verdict",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("actor", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["decisions.id"], name=op.f("fk_approvals_decision_id_decisions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
    )
    op.create_index(op.f("ix_approvals_decision_id"), "approvals", ["decision_id"], unique=False)
    op.create_table(
        "evidence",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("decision_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("ref", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_evidence_position_non_negative")),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["decisions.id"], name=op.f("fk_evidence_decision_id_decisions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence")),
        sa.UniqueConstraint(
            "decision_id", "position", name=op.f("uq_evidence_decision_id_position")
        ),
    )
    op.create_table(
        "kaggle_submissions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("competition", sa.String(length=200), nullable=False),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("decision_id", sa.String(length=64), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "prepared",
                "approved",
                "rejected",
                "submitted",
                "scored",
                "failed",
                name="kaggle_submission_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("kaggle_ref", sa.Text(), nullable=True),
        sa.Column("public_score", sa.Float(), nullable=True),
        sa.Column("private_score", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["artifacts.id"],
            name=op.f("fk_kaggle_submissions_artifact_id_artifacts"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_kaggle_submissions_campaign_id_campaigns"),
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["decisions.id"],
            name=op.f("fk_kaggle_submissions_decision_id_decisions"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_kaggle_submissions_run_id_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kaggle_submissions")),
    )
    op.create_index(
        op.f("ix_kaggle_submissions_campaign_id"),
        "kaggle_submissions",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        "ix_kaggle_submissions_created_at", "kaggle_submissions", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_kaggle_submissions_status"), "kaggle_submissions", ["status"], unique=False
    )
    op.create_table(
        "model_calls",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column(
            "agent",
            sa.Enum(
                "triage",
                "diagnostician",
                "planner",
                "engineer",
                "critic",
                name="agent_role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ok", "error", name="model_call_status", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("campaign_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("decision_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name=op.f("fk_model_calls_campaign_id_campaigns")
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["decisions.id"], name=op.f("fk_model_calls_decision_id_decisions")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_model_calls_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_calls")),
    )
    op.create_index(
        op.f("ix_model_calls_campaign_id"), "model_calls", ["campaign_id"], unique=False
    )
    op.create_index("ix_model_calls_created_at", "model_calls", ["created_at"], unique=False)
    op.create_index(op.f("ix_model_calls_run_id"), "model_calls", ["run_id"], unique=False)
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "allocation",
                "reservation",
                "settlement",
                "release",
                "model_call",
                "adjustment",
                name="ledger_entry_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("account", sa.String(length=300), nullable=False),
        sa.Column(
            "direction",
            sa.Enum(
                "debit",
                "credit",
                name="ledger_direction",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "currency",
            sa.Enum(
                "usd",
                "slurm_su",
                "gpu_hour_norm",
                "llm_usd",
                name="ledger_currency",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("normalized_amount", sa.Numeric(precision=28, scale=10), nullable=True),
        sa.Column("attempt_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("model_call_id", sa.String(length=64), nullable=True),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_ledger_entries_amount_positive")),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["attempts.id"], name=op.f("fk_ledger_entries_attempt_id_attempts")
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name=op.f("fk_ledger_entries_campaign_id_campaigns")
        ),
        sa.ForeignKeyConstraint(
            ["model_call_id"],
            ["model_calls.id"],
            name=op.f("fk_ledger_entries_model_call_id_model_calls"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_ledger_entries_run_id_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
    )
    op.create_index(
        op.f("ix_ledger_entries_attempt_id"), "ledger_entries", ["attempt_id"], unique=False
    )
    op.create_index(
        "ix_ledger_entries_campaign_id_account",
        "ledger_entries",
        ["campaign_id", "account"],
        unique=False,
    )
    op.create_index("ix_ledger_entries_created_at", "ledger_entries", ["created_at"], unique=False)
    op.create_index(op.f("ix_ledger_entries_run_id"), "ledger_entries", ["run_id"], unique=False)
    op.create_index(
        "ix_ledger_entries_transaction_id", "ledger_entries", ["transaction_id"], unique=False
    )
    # Cycle-closing references: created after every table exists, checked at commit.
    op.create_foreign_key(
        "fk_code_versions_decision_id_decisions",
        "code_versions",
        "decisions",
        ["decision_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_runs_current_attempt_id_attempts",
        "runs",
        "attempts",
        ["current_attempt_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_attempts_resumed_from_checkpoint_id_checkpoints",
        "attempts",
        "checkpoints",
        ["resumed_from_checkpoint_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_attempts_resumed_from_checkpoint_id_checkpoints", "attempts", type_="foreignkey"
    )
    op.drop_constraint("fk_runs_current_attempt_id_attempts", "runs", type_="foreignkey")
    op.drop_constraint(
        "fk_code_versions_decision_id_decisions", "code_versions", type_="foreignkey"
    )
    op.drop_index("ix_ledger_entries_transaction_id", table_name="ledger_entries")
    op.drop_index(op.f("ix_ledger_entries_run_id"), table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_created_at", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_campaign_id_account", table_name="ledger_entries")
    op.drop_index(op.f("ix_ledger_entries_attempt_id"), table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index(op.f("ix_model_calls_run_id"), table_name="model_calls")
    op.drop_index("ix_model_calls_created_at", table_name="model_calls")
    op.drop_index(op.f("ix_model_calls_campaign_id"), table_name="model_calls")
    op.drop_table("model_calls")
    op.drop_index(op.f("ix_kaggle_submissions_status"), table_name="kaggle_submissions")
    op.drop_index("ix_kaggle_submissions_created_at", table_name="kaggle_submissions")
    op.drop_index(op.f("ix_kaggle_submissions_campaign_id"), table_name="kaggle_submissions")
    op.drop_table("kaggle_submissions")
    op.drop_table("evidence")
    op.drop_index(op.f("ix_approvals_decision_id"), table_name="approvals")
    op.drop_table("approvals")
    op.drop_index(op.f("ix_decisions_status"), table_name="decisions")
    op.drop_index(op.f("ix_decisions_run_id"), table_name="decisions")
    op.drop_index("ix_decisions_created_at", table_name="decisions")
    op.drop_index(op.f("ix_decisions_campaign_id"), table_name="decisions")
    op.drop_table("decisions")
    op.drop_index(op.f("ix_incidents_status"), table_name="incidents")
    op.drop_index(op.f("ix_incidents_run_id"), table_name="incidents")
    op.drop_index("ix_incidents_created_at", table_name="incidents")
    op.drop_index(op.f("ix_incidents_campaign_id"), table_name="incidents")
    op.drop_table("incidents")
    op.drop_index(op.f("ix_checkpoints_run_id"), table_name="checkpoints")
    op.drop_table("checkpoints")
    op.drop_index(
        "uq_attempts_one_active_per_run",
        table_name="attempts",
        postgresql_where=sa.text("status IN ('pending', 'submitted', 'running', 'cancelling')"),
    )
    op.drop_index(op.f("ix_attempts_status"), table_name="attempts")
    op.drop_table("attempts")
    op.drop_index(op.f("ix_artifacts_run_id"), table_name="artifacts")
    op.drop_index("ix_artifacts_created_at", table_name="artifacts")
    op.drop_index("ix_artifacts_content_hash", table_name="artifacts")
    op.drop_index(op.f("ix_artifacts_campaign_id"), table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index(op.f("ix_runs_status"), table_name="runs")
    op.drop_index(op.f("ix_runs_experiment_id"), table_name="runs")
    op.drop_index("ix_runs_created_at", table_name="runs")
    op.drop_index(op.f("ix_runs_campaign_id"), table_name="runs")
    op.drop_table("runs")
    op.drop_index(op.f("ix_experiments_status"), table_name="experiments")
    op.drop_index(op.f("ix_experiments_hypothesis_id"), table_name="experiments")
    op.drop_index("ix_experiments_created_at", table_name="experiments")
    op.drop_index(op.f("ix_experiments_campaign_id"), table_name="experiments")
    op.drop_table("experiments")
    op.drop_table("budget_lines")
    op.drop_index(
        "ix_memory_items_embedding_hnsw",
        table_name="memory_items",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index(op.f("ix_memory_items_campaign_id"), table_name="memory_items")
    op.drop_table("memory_items")
    op.drop_index(op.f("ix_hypotheses_status"), table_name="hypotheses")
    op.drop_index(op.f("ix_hypotheses_parent_id"), table_name="hypotheses")
    op.drop_index(op.f("ix_hypotheses_campaign_id"), table_name="hypotheses")
    op.drop_table("hypotheses")
    op.drop_table("budgets")
    op.drop_table("compute_targets")
    op.drop_index(op.f("ix_code_versions_workload_id"), table_name="code_versions")
    op.drop_table("code_versions")
    op.drop_index(op.f("ix_campaigns_workload_id"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_status"), table_name="campaigns")
    op.drop_index("ix_campaigns_created_at", table_name="campaigns")
    op.drop_table("campaigns")
    op.drop_table("workloads")
    op.drop_table("provider_accounts")
    # The vector extension is left installed: other objects may depend on it.
