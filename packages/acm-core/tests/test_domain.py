from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from acm_core.domain import (
    ActionKind,
    Actor,
    AgentRole,
    Approval,
    ApprovalVerdict,
    Artifact,
    ArtifactKind,
    Attempt,
    AutonomyMode,
    Budget,
    BudgetLine,
    Campaign,
    Checkpoint,
    CodeVersion,
    ComputeTarget,
    Currency,
    Decision,
    DecisionKind,
    Evidence,
    EvidenceRef,
    Experiment,
    Hypothesis,
    Incident,
    KaggleSubmission,
    LedgerDirection,
    LedgerEntry,
    LedgerEntryKind,
    LogExcerptEvidence,
    MemoryItem,
    MemorySourceKind,
    Metric,
    MetricDirection,
    MetricQueryEvidence,
    ModelCall,
    Money,
    Policy,
    ProviderAccount,
    ProviderKind,
    RecoveryStep,
    ResourceRequest,
    RiskLevel,
    Run,
    RunComparisonEvidence,
    Workload,
    check_balanced,
)
from acm_core.errors import InvalidInputError
from acm_core.ids import (
    ArtifactId,
    AttemptId,
    CampaignId,
    CodeVersionId,
    ComputeTargetId,
    DecisionId,
    ExperimentId,
    HypothesisId,
    LedgerTransactionId,
    ProviderAccountId,
    RunId,
    WorkloadId,
)

SHA = "sha256:" + "ab" * 32
GIT = "0123456789abcdef0123456789abcdef01234567"
CMP = CampaignId.new()
RUN = RunId.new()

METRIC = Metric(
    name="mrr_at_25",
    direction=MetricDirection.MAXIMIZE,
    evaluator_ref="casmi.evaluate:mrr_at_25",
)


def _examples() -> list[BaseModel]:
    budget = Budget(campaign_id=CMP)
    return [
        Campaign(name="casmi", objective="max MRR@25", metric=METRIC, workload_id=WorkloadId.new()),
        budget.model_copy(
            update={
                "lines": [
                    BudgetLine(budget_id=budget.id, currency=Currency.USD, limit=Decimal(500))
                ]
            }
        ),
        Workload(name="casmi", repo_url="https://github.com/x/casmi", entrypoint="python -m t"),
        CodeVersion(workload_id=WorkloadId.new(), git_sha=GIT, content_hash=SHA),
        Hypothesis(campaign_id=CMP, statement="bigger encoder helps", rationale="underfits"),
        Experiment(
            campaign_id=CMP,
            hypothesis_id=HypothesisId.new(),
            code_version_id=CodeVersionId.new(),
            name="enc-2x",
            params={"lr": 3e-4, "layers": [4, 8], "warmup": None},
            resources=ResourceRequest(gpus=4, gpu_type="a100-80gb", walltime=timedelta(hours=8)),
        ),
        Run(campaign_id=CMP, experiment_id=ExperimentId.new(), final_metrics={"mrr_at_25": 0.4}),
        Attempt(
            run_id=RUN,
            ordinal=1,
            provider_account_id=ProviderAccountId.new(),
            compute_target_id=ComputeTargetId.new(),
            resources=ResourceRequest(),
        ),
        ProviderAccount(kind=ProviderKind.SLURM, name="school", config={"partitions": ["gpu"]}),
        ComputeTarget(
            provider_account_id=ProviderAccountId.new(),
            name="gpu",
            gpu_type="a100-80gb",
            gpus_per_node=4,
            max_walltime=timedelta(hours=48),
            price_per_gpu_hour=Money(currency=Currency.SLURM_SU, amount=Decimal("1.5")),
        ),
        Checkpoint(
            run_id=RUN,
            attempt_id=AttemptId.new(),
            step=1000,
            manifest_uri=f"s3://acm/manifests/checkpoints/{RUN}/1000.json",
            provider_kind=ProviderKind.MODAL,
        ),
        Artifact(
            kind=ArtifactKind.MODEL, name="weights", content_hash=SHA, uri="s3://a", size_bytes=10
        ),
        Decision(
            campaign_id=CMP,
            kind=DecisionKind.ALLOCATION,
            action=ActionKind.EARLY_STOP,
            rationale="plateau",
            proposed_by=Actor.agent(AgentRole.PLANNER),
            evidence=[
                MetricQueryEvidence(
                    summary="flat for 2k steps", run_id=RUN, metric_keys=["val_loss"]
                ),
                RunComparisonEvidence(
                    summary="worse than sibling", run_ids=[RUN, RunId.new()], metric_key="mrr"
                ),
            ],
            expected_cost=[Money(currency=Currency.GPU_HOUR_NORM, amount=Decimal(0))],
        ),
        Evidence(
            decision_id=DecisionId.new(),
            position=0,
            ref=LogExcerptEvidence(
                summary="CUDA OOM", run_id=RUN, start_line=10, end_line=12, excerpt="OOM"
            ),
        ),
        Approval(
            decision_id=DecisionId.new(),
            verdict=ApprovalVerdict.APPROVE,
            actor=Actor.human("usr_1"),
        ),
        LedgerEntry(
            transaction_id=LedgerTransactionId.new(),
            campaign_id=CMP,
            kind=LedgerEntryKind.RESERVATION,
            account=f"budget:{CMP}",
            direction=LedgerDirection.DEBIT,
            currency=Currency.USD,
            amount=Decimal("12.50"),
        ),
        ModelCall(
            agent=AgentRole.TRIAGE,
            provider="baseten",
            model="llama",
            input_tokens=100,
            output_tokens=20,
            latency_ms=300,
            cost=Decimal("0.0004"),
        ),
        Incident(
            campaign_id=CMP,
            run_id=RUN,
            attempt_id=AttemptId.new(),
            summary="no heartbeat for 15 min",
            signals={"last_heartbeat_s": 900},
            recovery_steps=[RecoveryStep(decision_id=DecisionId.new())],
        ),
        MemoryItem(
            source_kind=MemorySourceKind.RUN,
            source_id=str(RUN),
            summary="enc-2x plateaued",
            embedding=[0.1, 0.2],
            embedding_model="e5",
        ),
        KaggleSubmission(
            campaign_id=CMP,
            competition="enveda-casmi-2026",
            artifact_id=ArtifactId.new(),
            decision_id=DecisionId.new(),
            message="enc-2x ensemble",
        ),
    ]


@pytest.mark.parametrize("model", _examples(), ids=lambda m: type(m).__name__)
def test_json_round_trip(model: BaseModel) -> None:
    restored = type(model).model_validate_json(model.model_dump_json())
    assert restored == model
    assert type(model).model_json_schema()  # schema generation works for the API


def test_models_are_frozen_and_strict() -> None:
    run = Run(campaign_id=CMP, experiment_id=ExperimentId.new())
    with pytest.raises(ValidationError):
        run.status = "running"  # type: ignore[misc,assignment]
    with pytest.raises(ValidationError, match="Extra inputs"):
        Run.model_validate({"campaign_id": CMP, "experiment_id": ExperimentId.new(), "x": 1})


def test_timestamps_must_be_aware() -> None:
    with pytest.raises(ValidationError):
        Run(
            campaign_id=CMP,
            experiment_id=ExperimentId.new(),
            created_at="2026-10-27T00:00:00",  # type: ignore[arg-type]
        )


def test_evidence_ref_discriminates_on_kind() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(EvidenceRef)
    ref = adapter.validate_python(
        {"kind": "log_excerpt", "summary": "s", "run_id": RUN, "start_line": 1, "end_line": 2,
         "excerpt": "x"}
    )  # fmt: skip
    assert isinstance(ref, LogExcerptEvidence)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "nope", "summary": "s"})
    with pytest.raises(ValidationError, match="start_line"):
        adapter.validate_python(
            {"kind": "log_excerpt", "summary": "s", "run_id": RUN, "start_line": 3,
             "end_line": 2, "excerpt": "x"}
        )  # fmt: skip
    with pytest.raises(ValidationError, match="from_step"):
        MetricQueryEvidence(summary="s", run_id=RUN, metric_keys=["l"], from_step=5, to_step=1)
    with pytest.raises(ValidationError):
        RunComparisonEvidence(summary="s", run_ids=[RUN], metric_key="m")


def test_metric() -> None:
    assert METRIC.is_better(0.5, 0.4)
    assert not METRIC.is_better(0.4, 0.4)
    loss = METRIC.model_copy(update={"direction": MetricDirection.MINIMIZE})
    assert loss.is_better(0.1, 0.2)
    with pytest.raises(ValidationError, match="evaluator_ref"):
        Metric(name="m", direction=MetricDirection.MAXIMIZE, evaluator_ref="no_colon")


def _usd(amount: str) -> Money:
    return Money(currency=Currency.USD, amount=Decimal(amount))


def test_policy_gate() -> None:
    policy = Policy()
    assert policy.permits_auto_execution(ActionKind.RETRY, [_usd("9.99")], RiskLevel.LOW)
    assert not policy.permits_auto_execution(ActionKind.RETRY, [_usd("10")], RiskLevel.LOW)
    assert not policy.permits_auto_execution(ActionKind.RETRY, [], RiskLevel.MEDIUM)
    assert not policy.permits_auto_execution(ActionKind.PATCH_CODE, [], RiskLevel.LOW)
    no_usd_limit = Policy(auto_cost_limits={Currency.GPU_HOUR_NORM: Decimal(1)})
    assert not no_usd_limit.permits_auto_execution(ActionKind.RETRY, [_usd("0")], RiskLevel.LOW)
    with pytest.raises(ValidationError, match="always need approval"):
        Policy(auto_allowed_actions=frozenset({ActionKind.SUBMIT_TO_KAGGLE}))
    with pytest.raises(ValidationError):
        Policy(auto_cost_limits={Currency.USD: Decimal(-1)})


def test_campaign_requires_approval() -> None:
    campaign = Campaign(name="c", objective="o", metric=METRIC, workload_id=WorkloadId.new())
    assert campaign.autonomy is AutonomyMode.ADVISORY
    assert campaign.requires_approval(ActionKind.RETRY, [], RiskLevel.LOW)
    gated = campaign.model_copy(update={"autonomy": AutonomyMode.GATED})
    assert not gated.requires_approval(ActionKind.RETRY, [_usd("1")], RiskLevel.LOW)
    assert gated.requires_approval(ActionKind.START_EXPERIMENT, [_usd("1")], RiskLevel.LOW)
    assert gated.requires_approval(ActionKind.SUBMIT_TO_KAGGLE, [], RiskLevel.LOW)


def test_budget_lines() -> None:
    budget = Budget(campaign_id=CMP)
    usd = BudgetLine(budget_id=budget.id, currency=Currency.USD, limit=Decimal(100))
    full = Budget(id=budget.id, campaign_id=CMP, lines=[usd])
    assert full.line_for(Currency.USD) == usd
    assert full.line_for(Currency.LLM_USD) is None
    assert usd.soft_limit == Decimal(80)
    with pytest.raises(ValidationError, match="one line per currency"):
        Budget(id=budget.id, campaign_id=CMP, lines=[usd, usd])
    with pytest.raises(ValidationError, match="reference this budget"):
        Budget(campaign_id=CMP, lines=[usd])
    with pytest.raises(ValidationError):
        BudgetLine(budget_id=budget.id, currency=Currency.USD, limit=Decimal(0))


def test_decision_rejects_duplicate_cost_currency() -> None:
    with pytest.raises(ValidationError, match="more than one entry"):
        Decision(
            campaign_id=CMP,
            kind=DecisionKind.RECOVERY,
            action=ActionKind.RETRY,
            rationale="r",
            proposed_by=Actor.system("recovery_rules"),
            expected_cost=[_usd("1"), _usd("2")],
        )


def test_approval_must_be_human() -> None:
    with pytest.raises(ValidationError, match="humans"):
        Approval(
            decision_id=DecisionId.new(),
            verdict=ApprovalVerdict.APPROVE,
            actor=Actor.agent(AgentRole.CRITIC),
        )


def test_money() -> None:
    assert _usd("1.10") + _usd("2.20") == _usd("3.30")
    with pytest.raises(ValueError, match="cannot add"):
        _ = _usd("1") + Money(currency=Currency.LLM_USD, amount=Decimal(1))
    with pytest.raises(ValidationError):
        _usd("-1")


def test_resource_request() -> None:
    with pytest.raises(ValidationError, match="gpu_type"):
        ResourceRequest(gpu_type="h100")
    with pytest.raises(ValidationError):
        ResourceRequest(walltime=timedelta(0))


def test_memory_item_embedding_pairs() -> None:
    with pytest.raises(ValidationError):
        MemoryItem(source_kind=MemorySourceKind.RUN, source_id="x", summary="s", embedding=[1.0])


def _entry(txn: LedgerTransactionId, direction: LedgerDirection, amount: str) -> LedgerEntry:
    account = f"budget:{CMP}" if direction is LedgerDirection.DEBIT else f"reserved:{RUN}"
    return LedgerEntry(
        transaction_id=txn,
        campaign_id=CMP,
        kind=LedgerEntryKind.RESERVATION,
        account=account,
        direction=direction,
        currency=Currency.USD,
        amount=Decimal(amount),
    )


def test_ledger_balance() -> None:
    txn = LedgerTransactionId.new()
    check_balanced(
        [_entry(txn, LedgerDirection.DEBIT, "5"), _entry(txn, LedgerDirection.CREDIT, "5")]
    )
    with pytest.raises(InvalidInputError, match="not balanced"):
        check_balanced(
            [_entry(txn, LedgerDirection.DEBIT, "5"), _entry(txn, LedgerDirection.CREDIT, "4")]
        )
    with pytest.raises(InvalidInputError, match="at least two"):
        check_balanced([_entry(txn, LedgerDirection.DEBIT, "5")])
    other = LedgerTransactionId.new()
    with pytest.raises(InvalidInputError, match="several transactions"):
        check_balanced(
            [_entry(txn, LedgerDirection.DEBIT, "5"), _entry(other, LedgerDirection.CREDIT, "5")]
        )


def test_ledger_account_format() -> None:
    with pytest.raises(ValidationError):
        LedgerEntry(
            transaction_id=LedgerTransactionId.new(),
            campaign_id=CMP,
            kind=LedgerEntryKind.ADJUSTMENT,
            account="Budget",
            direction=LedgerDirection.DEBIT,
            currency=Currency.USD,
            amount=Decimal(1),
        )
