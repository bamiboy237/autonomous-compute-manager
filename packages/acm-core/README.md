# acm-core

Shared foundation used by every ACM package: settings, ids, errors, logging and the domain
model. Import as `acm_core`. Design background: [ARCHITECTURE.md §4](../../docs/ARCHITECTURE.md).

Database (SQLAlchemy/Alembic) and event schemas will also live here (THE-27 and later).

## Modules

| Module | Contents |
|---|---|
| `acm_core.settings` | `Settings` (pydantic-settings), `get_settings()` |
| `acm_core.ids` | `CampaignId`, `RunId`, … (prefixed ULIDs), `parse_id()` |
| `acm_core.errors` | `ACMError` hierarchy, `ErrorPayload` |
| `acm_core.logging` | `configure_logging()`, `get_logger()`, `trace_context()`, `set_trace_context_provider()` |
| `acm_core.domain` | Entity models, enums, `StateMachine`, `RUN_/ATTEMPT_/DECISION_STATE_MACHINE` |

## Settings

Environment variables use the `ACM_` prefix. Nested sections use `__`: `ACM_DB__URL`,
`ACM_TEMPORAL__ADDRESS`. Sections: `db`, `temporal`, `nats`, `clickhouse`, `storage`, `models`.
Defaults point at the local Compose stack; the full list is in [`.env.example`](../../.env.example).

```python
from acm_core.settings import get_settings

settings = get_settings()  # cached per process
dsn = settings.db.url.get_secret_value()
```

Secrets are `SecretStr` and never show up in `repr()` or logs.

## Ids

Every entity id is `<prefix>_<ULID>`, e.g. `run_01JB3V6Q1Z8K4M2N5P7R9S0T1W`. Only the canonical
form (uppercase ULID) is accepted.

| Prefix | Type | Prefix | Type |
|---|---|---|---|
| `cmp` | `CampaignId` | `art` | `ArtifactId` |
| `bgt` | `BudgetId` | `dec` | `DecisionId` |
| `bdl` | `BudgetLineId` | `evd` | `EvidenceId` |
| `wl` | `WorkloadId` | `apr` | `ApprovalId` |
| `cv` | `CodeVersionId` | `led` | `LedgerEntryId` |
| `hyp` | `HypothesisId` | `ltx` | `LedgerTransactionId` |
| `exp` | `ExperimentId` | `mc` | `ModelCallId` |
| `run` | `RunId` | `inc` | `IncidentId` |
| `att` | `AttemptId` | `mem` | `MemoryItemId` |
| `pa` | `ProviderAccountId` | `ksub` | `KaggleSubmissionId` |
| `tgt` | `ComputeTargetId` | `ckpt` | `CheckpointId` |
| `evt` | `EventId` (bus events) | | |

```python
run_id = RunId.new()  # mint
RunId("run_01JB...")  # validate (raises InvalidIdError)
parse_id("cmp_01JB...")  # -> CampaignId, chosen by prefix
run_id.created_at  # datetime from the ULID
```

Id types are `str` subclasses, distinct to pyright, and work as pydantic field types (JSON:
plain string with a `pattern`). Add a new type with `class FooId(PrefixedId, prefix="foo")`.

## Errors

Raise a subclass of `ACMError`; each has a stable `code`, an `http_status` and `retryable`.
`err.to_payload()` is the API error body. Codes never change meaning once released.

| Code | Class | HTTP |
|---|---|---|
| `invalid_input` | `InvalidInputError` | 400 |
| `invalid_id` | `InvalidIdError` | 400 |
| `unauthenticated` | `AuthenticationError` | 401 |
| `permission_denied` | `PermissionDeniedError` | 403 |
| `policy_violation` | `PolicyViolationError` | 403 |
| `not_found` | `NotFoundError` | 404 |
| `conflict` | `ConflictError` | 409 |
| `invalid_transition` | `InvalidTransitionError` | 409 |
| `budget_exceeded` | `BudgetExceededError` | 409 |
| `internal_error` | `InternalError` | 500 |
| `configuration_error` | `ConfigurationError` | 500 |
| `provider_error` | `ProviderError` | 502 |
| `external_service_error` | `ExternalServiceError` (retryable) | 503 |
| `provider_unavailable` | `ProviderUnavailableError` (retryable) | 503 |

## Logging

```python
from acm_core.logging import configure_logging, get_logger, trace_context

configure_logging(settings.log_level, settings.log_format)  # once, at startup
log = get_logger(__name__)
with trace_context(trace_id, span_id):
    log.info("attempt submitted", run_id=run_id)
```

Output is one JSON object per line (`timestamp`, `level`, `logger`, `event`, bound fields,
`trace_id`/`span_id` when known). Standard-library loggers go through the same pipeline.
The OpenTelemetry integration should call `set_trace_context_provider()` with a function that
reads the current span; `acm_core` does not depend on OpenTelemetry.

## Domain model

All models are pydantic v2, **frozen** and `extra="forbid"`. Entities have `id`,
`created_at`, `updated_at` (timezone-aware UTC), with defaults so `Run(campaign_id=...,
experiment_id=...)` works. Change a model with `model_copy(update=...)`.

Entities: `Campaign`, `Budget`/`BudgetLine`, `Workload`, `CodeVersion`, `Hypothesis`,
`Experiment`, `Run`, `Attempt`, `ProviderAccount`, `ComputeTarget`, `Checkpoint`, `Artifact`,
`Decision`, `Evidence`, `Approval`, `LedgerEntry`, `ModelCall`, `Incident`, `MemoryItem`,
`KaggleSubmission`.

Value types: `Metric` (name, direction, evaluator ref), `Policy`, `Money`, `Actor`,
`ResourceRequest`, `RecoveryStep`, and `EvidenceRef` — a union discriminated by `kind`
(`metric_query`, `log_excerpt`, `run_comparison`, `prior_decision`, `artifact`).

Key enums: `AutonomyMode` (`advisory`, `gated`), `ActionKind` (recovery playbook + planner
actions), `DecisionKind`, `FailureClass` (§9 taxonomy), `ProviderKind`, `Currency`,
`AgentRole`, `RiskLevel`.

### State machines

`Run`, `Attempt` and `Decision` statuses change only through `transition_to()`, which raises
`InvalidTransitionError` (409) on an illegal move and stamps the relevant timestamps.

```
Run       pending    -> running | failed | stopped
          running    -> recovering | succeeded | failed | stopped
          recovering -> running | failed | stopped

Attempt   pending    -> submitted | failed | cancelled
          submitted  -> running | cancelling | cancelled | failed | preempted
          running    -> succeeded | failed | preempted | cancelling | cancelled
          cancelling -> cancelled | failed | succeeded

Decision  proposed   -> approved | rejected | expired
          approved   -> executed | failed
```

Every status not listed on the left is terminal.

### Policy gate

`Campaign.requires_approval(action, expected_cost, risk)` implements §8: advisory campaigns
always need approval; gated campaigns skip it only when the action is in
`policy.auto_allowed_actions` (default: retry, resume from checkpoint, reduce batch, early
stop), the risk is at most `policy.max_auto_risk` (default low), and each expected cost is
below `policy.auto_cost_limits` for its currency (a currency without a limit always needs
approval). `submit_to_kaggle` always needs approval.

### Ledger

`LedgerEntry` rows sharing a `transaction_id` form one double-entry transaction;
`check_balanced(entries)` verifies debits equal credits per currency. Account naming
conventions are in the `acm_core.domain.ledger` docstring.

### Temporal workflows

Id minting (`FooId.new()`) and model defaults use the wall clock and randomness, which
workflow code must not do. Create entities and ids in activities, or pass explicit values
(`id=...`, `created_at=workflow.now()`, `transition_to(..., at=workflow.now())`).
