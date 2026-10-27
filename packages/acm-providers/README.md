# acm-providers

The provider-neutral contract every compute adapter implements, a conformance suite, a fake
provider and the registry that builds providers from configuration. Import as `acm_providers`.
Design background: [ARCHITECTURE.md §6](../../docs/ARCHITECTURE.md).

| Module | Contents |
|---|---|
| `acm_providers.base` | `Provider` protocol, `JobSpec`, `Resources`, `DataMount`, `CheckpointConfig`, `ProviderJobRef`, `JobState`, `JobStatus`, `JobSignal`, `LogChunk`, `UsageRecord`, `QueueSnapshot`, `Capabilities` |
| `acm_providers.errors` | `JobNotFoundError`, `IdempotencyConflictError`, `JobNotActiveError`, `CapabilityMismatchError`, `UnsupportedSignalError` |
| `acm_providers.registry` | `ProviderRegistry`, `default_registry`, `capabilities_from_targets()` |
| `acm_providers.testing` | `ProviderConformance` suite, `ProviderHarness`, `FakeHarness` |
| `acm_providers.fake` | `FakeProvider` (in-memory, injectable clock), `ManualClock` |
| `acm_providers.scenarios` | `Scenario`: the workload behaviours the suite needs |

## The contract

All methods are async. Agents and workflows only ever see this interface.

| Method | Contract |
|---|---|
| `capabilities()` | Declarative: GPU types and memory, max walltime/cpus/memory, targets, preemptible, shared filesystem, egress, pricing model, deliverable `signals`, image support. The placement engine reads it. |
| `submit(spec)` | **Idempotent on `spec.idempotency_key`.** Same key and spec returns the same ref and starts nothing new; same key, different spec raises `IdempotencyConflictError`. A spec that does not fit `Capabilities.mismatches(spec)` raises `CapabilityMismatchError`. |
| `status(ref)` | Normalized `JobState` plus the provider's own state verbatim in `raw_state`; exit code, `failure_class` when known, timestamps. |
| `cancel(ref, grace=)` | Send `TERM`, kill after `grace`. While shutting down the state is `cancelling`. **Idempotent**: finished or already-cancelling jobs are a no-op. |
| `signal(ref, sig)` | `UnsupportedSignalError` if `sig` is not in `capabilities.signals`, `JobNotActiveError` if the job is not running. `checkpoint-now` maps to `SIGUSR1` unless the provider has a better mechanism. |
| `logs(ref, since=, limit=)` | Paginated by line offset. Past the end returns an empty chunk. `complete` is true once the job is finished and nothing remains. Negative `since` or `limit < 1` is `InvalidInputError`. |
| `accounting(ref)` | Usable at any time; `is_final` tells provisional from final. GPU-hours, normalized GPU-hours, cost (`None` if unpriceable), raw provider fields. |
| `queue_snapshot()` | Per-target pending/running jobs and free GPUs, for queue-wait prediction. |
| `close()` | Release connections; safe to call twice. |

Every method on a ref the provider does not know raises `JobNotFoundError`. Use
`acm_core.errors.ProviderUnavailableError` (retryable) when the provider cannot be reached and
`ProviderError` for any other rejection.

### States

`queued → running → succeeded | failed | cancelled | preempted | timed_out`, with `cancelling`
between `running` and `cancelled`. `JobState.attempt_status` maps to `AttemptStatus`
(`timed_out` is a failure; set `failure_class=walltime_exceeded`). Set `failure_class` whenever
the provider tells you why (OOM, node failure, preemption).

### JobSpec

Provider-neutral: `command`, optional `image`, `resources` (gpus, gpu_type, gpu_memory_gb,
cpus, memory_gb, nodes, walltime), `target`, `mounts` (object-store URIs), `checkpoint`,
`env`, `labels`, `code_version_id`. Adapters must put `spec.runtime_env()` in the job's
environment (it adds `ACM_IDEMPOTENCY_KEY`, `ACM_WALLTIME_SECONDS` and the `ACM_CHECKPOINT_*`
variables). Store `spec.idempotency_key` and `spec.fingerprint()` with the native job
(Slurm `--comment`/job name, Kubernetes labels/annotations, Modal tags) so that a retried
`submit` finds the job again even after a process restart.

## Writing an adapter

1. Define a pydantic settings model for `ProviderAccount.config` (`extra="forbid"`).
2. Implement `Provider` in `acm_<kind>`; declare `kind` and build `Capabilities` (use
   `capabilities_from_targets()` for the GPU/walltime parts).
3. Register a factory: `default_registry.register(ProviderKind.SLURM, SlurmSettings, build)`.
   `BuildContext` carries the account, its enabled targets, the validated settings and the
   resolved credential. Invalid config raises `ConfigurationError` before your factory runs.
4. Run the conformance suite against it.

### Conformance suite

```python
class TestSlurmConformance(ProviderConformance):
    @pytest.fixture
    async def provider(self) -> AsyncIterator[Provider]:
        provider = SlurmProvider(...)
        yield provider
        await provider.close()

    @pytest.fixture
    def harness(self) -> ProviderHarness:
        return SlurmHarness(...)
```

The suite covers submit→running→succeeded, a failing job, cancel with grace and escalation,
idempotent submit and cancel, key conflicts, signal delivery and rejection, log pagination,
provisional and final accounting, queue snapshots, oversized specs and unknown refs.

The **harness** is the adapter's side: `spec(scenario, key)` builds a `JobSpec` whose job
behaves as the `Scenario` describes (see `acm_providers.scenarios`; for example `RUN` echoes
`received signal <name>` for every signal it traps), `tick()` lets time pass (advance a
simulated clock, or sleep a poll interval), `grace` is a short cancel grace, and `scenarios`
lists what the adapter can run (tests for omitted scenarios skip). Mark tests that need real
infrastructure with the matching marker (`integration`, `slurm`). `FakeHarness` in
`acm_providers.testing` is a template.

### Using the fake

`FakeProvider(clock=ManualClock())` is deterministic and good for tests of code that needs a
provider. Jobs run `("fake-workload", <Scenario>)`; `provider.preempt(ref)` simulates
preemption. `register_fake(registry)` registers it as the `simulator` kind.
