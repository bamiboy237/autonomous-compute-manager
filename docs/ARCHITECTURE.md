# Architecture

Status: **settled** (2026-10-27). Changes to anything in "Decisions" need the owner's sign-off.
Background and goals: [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

## 1. Decisions

| Area | Decision |
|---|---|
| Product shape | Self-hosted infrastructure. One organization per deployment, many users. |
| Install | Docker Compose (single machine) and a Helm chart (Kubernetes). |
| Agent authority | Chosen per campaign: **advisory** (every action needs approval) or **gated** (autonomous below spend/risk thresholds, approval above them). |
| Code edits | Sandboxed patches proposed by Muse Code; a patch runs only after it passes a smoke test in a sandbox; every patch is a versioned commit. |
| Models | Meta only, behind a pluggable provider interface. Muse Spark (Meta Model API) for reasoning; Llama open weights (Baseten / Modal endpoints) for high-volume triage. |
| Agent runtime | Pydantic AI, run durably through its Temporal integration. |
| Orchestration | Temporal, self-hosted, persistence on PlanetScale Postgres. |
| System of record | PlanetScale Postgres with pgvector. Compose and Helm ship a bundled Postgres; any Postgres 16+ with pgvector works. |
| Telemetry | NATS JetStream (event bus) + ClickHouse (metrics, logs, events). |
| Workload reporting | Optional `acm` SDK for rich signals; uninstrumented workloads are monitored from logs, process stats and NVML. |
| Artifacts | S3-compatible object storage (MinIO bundled; R2/S3/GCS in production). Content-addressed. |
| Providers | Slurm, Modal, ThunderCompute (SSH VMs), Kubernetes, Local, Simulator. |
| Slurm access | Through `acm-edge` and/or SSH. Real cluster details are pending; all development targets a containerized Slurm cluster. |
| Cross-provider moves | Running jobs can move providers by checkpoint: stop → checkpoint → resume elsewhere. |
| Languages | Python 3.12 core; Go for `acm-edge`; TypeScript/Next.js for the UI. |
| UI | Full control (create campaigns, budgets, approvals, stop jobs, live views). **Owned by the project owner (Codex + Figma).** Backend owns the API contract. |
| First campaign | Kaggle **Enveda CASMI 2026** — molecule structure (SMILES) from MS/MS spectra, metric MRR@25. |
| Kaggle submissions | The agent prepares submissions with evidence; a human approves each one. |

## 2. System overview

```
                         ┌──────────────────────── Control plane ────────────────────────┐
  Web UI (Next.js) ─────▶│  acm-api (FastAPI: REST + SSE)        acm-worker (Temporal)   │
  acmctl (CLI)     ─────▶│        │                                 │  workflows          │
                         │        ▼                                 │  activities         │
                         │  Postgres/PlanetScale ◀──────────────────┤  Pydantic AI agents │
                         │  (record, ledger, pgvector)              │                     │
                         │        ▲                                 ▼                     │
                         │  ClickHouse ◀── ingest ── NATS JetStream ◀── provider events   │
                         │  (metrics/logs)                 ▲                              │
                         └─────────────────────────────────┼──────────────────────────────┘
                                                           │ (outbound only from sites)
           ┌───────────────────────┬───────────────────────┼───────────────────────┐
           ▼                       ▼                       ▼                       ▼
   Slurm site                 Modal                  ThunderCompute / SSH     Kubernetes
   acm-edge on login node     (SDK, serverless)      VMs (acm-edge on VM)     (Jobs API)
   sbatch/squeue/sacct
   or slurmrestd
           │                       │                       │                       │
           └──── workloads using the `acm` SDK (or plain scripts) ─────────────────┘
                                   │
                           S3-compatible object store (datasets, checkpoints, artifacts)
```

## 3. Repository layout

```
packages/                      # Python uv workspace members (src/ layout)
  acm-core/      acm_core      # settings, ids, domain models (pydantic), SQLAlchemy models,
                               # Alembic migrations, event schemas, errors
  acm-sdk/       acm           # workload SDK (pip install acm)
  acm-storage/   acm_storage   # object store client, content addressing, checkpoint manifests
  acm-telemetry/ acm_telemetry # NATS publishers/consumers, ClickHouse schema + ingestion + query API
  acm-providers/ acm_providers # provider interface, capability model, adapters, simulator
  acm-agents/    acm_agents    # Pydantic AI agents, tools, policies, model provider config
  acm-worker/    acm_worker    # Temporal workflows + activities, worker entrypoint
  acm-api/       acm_api       # FastAPI app, auth, OpenAPI, SSE
  acm-cli/       acm_cli       # `acmctl`
  acm-eval/      acm_eval      # baselines, benchmark harness, agent replay evaluation
edge/                          # Go module: acm-edge daemon
workloads/casmi/               # CASMI 2026 training/inference workload
deploy/compose/                # docker compose stack (+ profiles: slurm, observability)
deploy/helm/acm/               # Helm chart
deploy/slurm-dev/              # containerized Slurm cluster images/config
ui/                            # Web UI (owner + Codex)
docs/
```

Tooling: `uv` workspace, ruff (lint + format), pyright (strict for `acm_core`, `acm_providers`, `acm_worker`), pytest + pytest-asyncio, testcontainers where Compose is not already up. Go 1.23 with golangci-lint. A root `justfile` exposes `just up`, `just test`, `just test-integration`, `just lint`, `just migrate`.

Key libraries: pydantic v2, SQLAlchemy 2 (async, asyncpg), Alembic, FastAPI, temporalio, pydantic-ai (+ `pydantic_ai.durable_exec.temporal`), nats-py, clickhouse-connect, obstore or aioboto3, structlog, OpenTelemetry SDK.

## 4. Domain model (Postgres)

All ids are prefixed ULIDs (`cmp_…`, `exp_…`). Every row has `created_at`, `updated_at`.

| Entity | Purpose |
|---|---|
| `Campaign` | Objective, metric (name, direction, evaluator ref), autonomy mode, policy, status, workload ref. |
| `Budget` / `BudgetLine` | Limits per currency (`gpu_hour_norm`, `usd`, `slurm_su`, `llm_usd`), soft/hard thresholds. |
| `Workload` / `CodeVersion` | Workload repo + entrypoint; each code version is a git commit SHA + content hash; patches link to parent version. |
| `Hypothesis` | Node in the campaign's hypothesis tree: statement, rationale, parent, status, evidence links. |
| `Experiment` | A configuration (code version + params + resources) testing a hypothesis. |
| `Run` | One execution of an experiment toward completion; owns metrics and final result. |
| `Attempt` | One placement of a run on a provider (a run has many attempts across failures/moves). |
| `ProviderAccount` / `ComputeTarget` | Configured provider + partitions/GPU types/limits + pricing. |
| `Checkpoint` | Manifest pointer in object store, step, metrics at save, provider of origin. |
| `Artifact` | Datasets, model files, submissions; content hash + URI. |
| `Decision` | Agent or human decision: kind, action, rationale, status (proposed/approved/rejected/executed), actor. |
| `Evidence` | Typed pointer backing a decision: metric query, log excerpt, run comparison, prior decision. |
| `Approval` | Human response to a gated/advisory decision. |
| `LedgerEntry` | Double-entry cost record: account, currency, amount, normalized amount, source (attempt, model call, reservation). |
| `ModelCall` | Every LLM call: model, tokens, latency, cost, agent, decision link. |
| `Incident` | Detected problem on an attempt: classification, signals, recovery actions taken, outcome. |
| `MemoryItem` | pgvector-embedded summaries of runs, incidents, decisions for retrieval. |
| `KaggleSubmission` | Prepared submission, evidence, approval, Kaggle status/score. |

Migrations must be PlanetScale-compatible (no reliance on superuser-only features beyond enabling `vector`).

## 5. Orchestration (Temporal)

Task queues: `acm-control` (workflows, cheap activities), `acm-agents` (LLM activities), `acm-io` (provider + storage activities).

- **CampaignWorkflow** — long-lived. Loop: gather state → planner proposes → critic reviews → policy gate (auto-execute or wait for `approve`/`reject` signal) → start/stop ExperimentWorkflows → sleep until event or timer. Uses continue-as-new. Signals: `approve_decision`, `reject_decision`, `pause`, `resume`, `stop`, `update_budget`. Queries: `status`, `pending_decisions`.
- **ExperimentWorkflow / RunWorkflow** — drives a run to completion across attempts; asks the placement engine where to run; reserves budget before each attempt; handles move-by-checkpoint.
- **AttemptWorkflow** — submit → watch (heartbeat + progress timers) → on signal of anomaly, open an Incident → RecoveryWorkflow → finalize ledger.
- **RecoveryWorkflow** — classify → choose playbook action (deterministic first, Diagnostician agent when ambiguous) → policy gate → execute → verify; anti-thrash limits per run.

Workflows are deterministic; all I/O happens in activities. Agent runs use Pydantic AI's Temporal integration so each model call and tool call is an activity with retries.

## 6. Providers

```python
class Provider(Protocol):
    kind: ProviderKind
    async def capabilities(self) -> Capabilities          # GPU types, max walltime, preemptible,
                                                          # shared fs, network egress, pricing model
    async def submit(self, spec: JobSpec) -> ProviderJobRef
    async def status(self, ref: ProviderJobRef) -> JobStatus
    async def cancel(self, ref: ProviderJobRef, *, grace: timedelta) -> None
    async def signal(self, ref: ProviderJobRef, sig: str) -> None   # e.g. checkpoint-now
    async def logs(self, ref: ProviderJobRef, *, since: int) -> LogChunk
    async def accounting(self, ref: ProviderJobRef) -> UsageRecord
    async def queue_snapshot(self) -> QueueSnapshot      # for wait-time prediction
```

`JobSpec` is provider-neutral: image or env, command, code version, resources (gpus, gpu_type, cpus, mem, walltime), data mounts (object-store URIs), checkpoint config, env, labels. Every adapter must pass the shared conformance suite (`acm_providers.testing`).

| Adapter | Mechanism | Dev/test target |
|---|---|---|
| Simulator | Discrete-event cluster model: partitions, queues, GPU types, preemption, hardware faults, synthetic training curves from a parameterized response surface. Deterministic by seed. | itself |
| Local | subprocess / docker on the worker host | itself |
| Slurm | `slurmrestd` when available, else `sbatch/squeue/sacct/scancel` over SSH or via `acm-edge`. `--signal=USR1@<s>` before walltime; requeue; job arrays. | `deploy/slurm-dev` (slurmctld, 2× slurmd with fake GRES GPUs, slurmdbd, slurmrestd) |
| Modal | Modal Python SDK: functions with GPU, volumes, sandboxes | mocks + live smoke test with credentials |
| ThunderCompute | SSH VM adapter + `acm-edge` on the VM | local sshd container |
| Kubernetes | batch/v1 Jobs | kind cluster in CI |

**acm-edge** (Go): a static binary run on a Slurm login node or VM. It dials *out* to NATS (token or mTLS); it never accepts inbound connections. It executes an allowlisted command set (`sbatch`, `squeue`, `sacct`, `scancel`, `scontrol show job`, file tail/read under configured roots), tails workload telemetry files, and relays everything as NATS messages. This works behind MFA/VPN because nothing has to connect into the site.

**Placement engine:** scores (provider, target) candidates on fit (GPU type/memory), predicted queue wait (learned from `sacct`/queue snapshots), price per normalized GPU-hour, data locality (cached dataset/checkpoint), and remaining budget per currency.

## 7. Telemetry

NATS subjects (JSON payloads, schemas in `acm_core.events`, versioned with a `v` field):

```
acm.run.<run_id>.metrics      step, wall_time, values{}
acm.run.<run_id>.heartbeat    step, throughput, gpu{util,mem}, host
acm.run.<run_id>.log          stream, lines[]
acm.run.<run_id>.checkpoint   step, manifest_uri
acm.attempt.<id>.state        provider state transitions
acm.edge.<site>.cmd / .reply  command RPC to edge
acm.decision.<campaign_id>    decision lifecycle (for UI streaming)
```

JetStream streams persist these; `acm_telemetry.ingest` consumers write ClickHouse tables `metrics`, `heartbeats`, `logs`, `events` (MergeTree, partitioned by day, ordered by `(run_id, ts)`). `acm_telemetry.query` exposes typed queries used by both the API and agent tools (curves, slopes, plateaus, percentiles, log search, cross-run comparison).

**acm SDK** (`import acm`): `acm.init()`, `acm.log(step, **metrics)`, `acm.heartbeat()` (automatic background thread with NVML), `acm.checkpoint.save(state, step)` / `acm.checkpoint.restore()`, `acm.should_stop()`, preemption signal handling (USR1/SIGTERM → checkpoint), fault-injection hooks driven by env vars. Transport: NATS if reachable, else JSONL files under `$ACM_RUN_DIR` that edge/adapters tail. **Uninstrumented** workloads: adapters capture stdout/stderr and process/NVML stats; a log-pattern extractor recovers step/loss where possible; the Engineer agent can propose an instrumentation patch.

## 8. Agents (Pydantic AI on Temporal)

| Agent | Model | Job |
|---|---|---|
| Triage | Llama (Baseten/Modal endpoint) | Cheap, frequent: classify log windows and heartbeat anomalies; escalate. |
| Diagnostician | Muse Spark | Root-cause an incident from logs, metrics, code, history; choose a recovery action. |
| Planner | Muse Spark | Maintain the hypothesis tree; propose experiments; stop/continue/reallocate; propose Kaggle submissions. |
| Engineer | Muse Code | Produce code patches (features, fixes, instrumentation); patches run only after a sandbox smoke test passes. |
| Critic | Muse Spark | Check proposals against evidence, budget and objective; can veto or request revision. |

- All agents return **typed outputs** (pydantic models) — e.g. `ExperimentProposal`, `RecoveryPlan`, `StopDecision` — each carrying `evidence: list[EvidenceRef]` and `expected_value` / `expected_cost`.
- Tools are typed functions over `acm_telemetry.query`, the record (Postgres), memory (pgvector), the placement engine and budget ledger. Mutating actions are never tools; agents return decisions, and workflows execute them after the policy gate.
- **Policy gate:** advisory → always wait for approval. Gated → auto-execute when `expected_cost` < threshold and the action is in the campaign's auto-allowed set (default: retry, resume-from-checkpoint, reduce-batch, early-stop); everything else waits.
- Model access goes through `acm_agents.models`, a registry keyed by role, so another provider can be plugged in without touching agent code. Every call is recorded as a `ModelCall` and a ledger entry.

## 9. Recovery

Failure taxonomy (initial): `oom_gpu`, `oom_host`, `nan_divergence`, `loss_plateau`, `throughput_regression`, `stall_no_heartbeat`, `dataloader_hang`, `nccl_error`, `preempted`, `walltime_exceeded`, `node_failure`, `env_import_error`, `data_missing`, `quota_exceeded`, `queue_starvation`, `unknown`.

Playbook actions: `retry`, `resume_from_checkpoint`, `reduce_batch_with_grad_accum`, `lower_lr_and_resume`, `increase_walltime`, `move_provider_via_checkpoint`, `patch_code` (Engineer), `abandon_with_evidence`. Each action declares preconditions, cost estimate and verification. Deterministic rules handle unambiguous classes; the Diagnostician handles the rest. Anti-thrash: per-run limits on repeated actions; repeated identical failures escalate.

Failure injection (`acm_eval.chaos` + SDK hooks + simulator): OOM, NaN at step N, SIGKILL, hang, slow I/O, preemption, walltime. Same scenarios run against baseline and agent.

## 10. Optimization

The planner works over a hypothesis tree, not a flat sweep. Allocation uses agent-guided successive halving with statistical guardrails (no stop decision without minimum steps / confidence on the learning-curve comparison). Learning-curve extrapolation and cross-run comparisons are tools, not prompts. Experiment memory (pgvector) lets the planner retrieve similar past runs and incidents.

## 11. Budget ledger

Double-entry ledger in Postgres. Currencies: `usd`, `slurm_su`, `gpu_hour_norm`, `llm_usd`. Normalization rates are configured per ComputeTarget (e.g. A100-80G = 1.0 GPU-hour-norm). A reservation is posted before every submission and settled from provider accounting on completion; submissions that would exceed a hard limit are rejected in the workflow, not by convention.

## 12. Storage & cross-provider moves

Object store layout: `cas/<sha256>` blobs; `manifests/checkpoints/<run_id>/<step>.json` listing blobs + metadata. Providers keep a local cache keyed by hash. Moving a run: signal checkpoint → wait for manifest → cancel attempt → new attempt on target with `ACM_RESUME_MANIFEST`. Datasets are pre-staged to targets by the placement engine when locality is known.

## 13. API contract (for the UI)

The backend owns `acm-api`; the UI consumes it. The contract is the generated OpenAPI document (`/openapi.json`, committed at `docs/api/openapi.json` by CI) plus SSE streams. A TypeScript client is generated from it (`openapi-typescript`) into `ui/src/api/`.

```
POST   /v1/campaigns                       create (objective, metric, workload, budget, autonomy)
GET    /v1/campaigns, /v1/campaigns/{id}
POST   /v1/campaigns/{id}:start|pause|resume|stop
PATCH  /v1/campaigns/{id}/budget
GET    /v1/campaigns/{id}/hypotheses       tree
GET    /v1/campaigns/{id}/experiments, /v1/experiments/{id}
GET    /v1/runs/{id}, /v1/runs/{id}/attempts
GET    /v1/runs/{id}/metrics?keys=&from=&to=&downsample=
GET    /v1/runs/{id}/logs?since=&q=
POST   /v1/runs/{id}:stop
GET    /v1/campaigns/{id}/decisions        with evidence
POST   /v1/decisions/{id}:approve|reject
GET    /v1/campaigns/{id}/ledger           burn by currency/provider/time
GET    /v1/incidents?campaign_id=
GET    /v1/providers, /v1/providers/{id}/targets
GET    /v1/kaggle/submissions, POST /v1/kaggle/submissions/{id}:approve
GET    /v1/stream/campaigns/{id}           SSE: decisions, run state, metrics deltas, ledger
GET    /v1/stream/runs/{id}                SSE: metrics, logs, heartbeats
```

Auth: API keys + session tokens; OIDC later. Single org, roles `admin`, `operator`, `viewer`.

## 14. Deployment

- `deploy/compose`: postgres (pgvector), temporal (+ ui), nats, clickhouse, minio, acm-api, acm-worker, otel-collector, grafana. Profiles: `slurm` (dev cluster), `observability`.
- `deploy/helm/acm`: same services; Postgres/ClickHouse/NATS/MinIO can be bundled or external (PlanetScale for Postgres + Temporal persistence).
- Secrets: Kubernetes secrets / SOPS-encrypted files; provider credentials never in the database in plaintext.
- Observability: OpenTelemetry traces across API → workflow → activity → provider/LLM; Grafana dashboards.

## 15. First campaign: Enveda CASMI 2026

Task: predict up to 25 ranked SMILES per MS/MS spectrum; metric MRR@25 on InChIKey first block after RDKit tautomer canonicalization. Kaggle code competition: notebook ≤ 9 h, no internet, external public data and pre-trained models allowed. Final submission deadline 2026-12-14.

Implication: training and search happen on our providers (Slurm/Modal); the inference notebook loads our trained weights uploaded as a Kaggle dataset. `workloads/casmi` contains data prep, a local MRR@25 evaluator identical to the metric notebook, training entrypoints instrumented with the `acm` SDK, and an inference/packaging script. Submission flow: agent prepares artifacts + evidence → human approves → upload via Kaggle API.

## 16. Engineering standards

- One ticket = one PR. A PR is done when CI is green and includes **unit tests, an integration test against the Compose stack where the ticket touches infrastructure, and docs**.
- PRs auto-merge when green. Never weaken or skip a test to get green.
- Conventional commits. No secrets in the repo; `.env.example` documents every variable.
