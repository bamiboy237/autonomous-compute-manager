# Development

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 for you)
- Go 1.23+ and [golangci-lint](https://golangci-lint.run/) v2
- [just](https://just.systems/) (`uv tool install rust-just` works)
- Docker with Compose v2 (for integration tests)

## Setup

```sh
uv sync --all-packages     # creates .venv with every workspace package + dev tools
cp .env.example .env
```

## Commands

| Command | What it does |
|---|---|
| `just lint` | ruff check + format check, golangci-lint |
| `just fmt` | auto-fix formatting (Python and Go) |
| `just typecheck` | pyright (strict for `acm_core`, `acm_providers`, `acm_worker`) |
| `just test` | unit tests (Python + Go), no external services |
| `just up` / `just down -v` | start (and wait until healthy) / stop and reset the Compose stack |
| `just ps` / `just logs [svc…]` | stack status and health / follow logs |
| `just test-integration` | tests marked `integration` against the running stack |
| `just up-slurm` / `just down-slurm -v` | start / stop and reset the containerized Slurm cluster ([deploy/slurm-dev](../deploy/slurm-dev/README.md)) |
| `just test-slurm` | tests marked `slurm` against the running cluster |
| `just migrate` | apply database migrations |
| `just ci` | lint + typecheck + unit tests, as CI runs them |

## Dev stack

`deploy/compose/compose.yaml` runs every backing service locally. `just up` starts the stack, waits until every healthcheck passes (up to 5 minutes), then creates the object-store bucket. Images are pinned; bump them deliberately.

| Service | Purpose | Host port(s) (default) | Credentials (default) |
|---|---|---|---|
| `postgres` | Postgres 17 + pgvector. Databases `acm`, plus `temporal` and `temporal_visibility` (created by Temporal) | 5432 | `acm` / `acm`, db `acm` |
| `temporal` | Temporal server, Postgres persistence, namespace `default` | 7233 (gRPC) | none |
| `temporal-ui` | Temporal Web UI | 8233 → http://localhost:8233 | none |
| `nats` | NATS with JetStream; monitoring at `/healthz`, `/jsz` | 4222, 8222 (monitor) | token `acm-dev-token` |
| `clickhouse` | ClickHouse | 8123 (HTTP), 19000 (native) | `acm` / `acm`, db `acm` |
| `minio` | S3-compatible object store; bucket `acm` | 9000 (S3), 9001 (console) | `acm` / `acm-dev-secret` |
| `otel-collector` | OTLP receiver (profile `observability`) | 4317 (gRPC), 4318 (HTTP) | none |
| `grafana` | Dashboards (profile `observability`) | 3000 | `admin` / `admin` |

- **Configuration.** Every port and credential is an `ACM_*` variable in `.env.example`; the Compose file uses the same values as defaults, so the stack runs without a `.env`. `just` loads `.env`, so override values there. A unit test keeps the two files in sync.
- **Network exposure.** Ports bind to `ACM_BIND_HOST` (`127.0.0.1`). Set it to `0.0.0.0` only on a trusted network.
- **Observability profile.** `COMPOSE_PROFILES=observability just up` also starts otel-collector and Grafana. The collector currently logs what it receives (debug exporter).
- **Reset.** `just down` stops containers and keeps data; `just down -v` also deletes the volumes (Postgres, NATS, ClickHouse, MinIO, Grafana), so the next `just up` starts clean.
- **Smoke test.** `just test-integration` runs `tests/integration/test_compose_stack.py`, which connects to every service (pgvector extension, Temporal namespace, JetStream account, ClickHouse query, MinIO bucket round trip).
- **MinIO image.** Upstream stopped publishing MinIO images in 2025, so the stack uses `pgsty/minio`, a maintained build of the same server. Any S3-compatible store works in production.

## Layout

Python packages live in `packages/<name>/src/<module>` with tests in `packages/<name>/tests`. Add a dependency to one package with `uv add --package acm-core <dep>`. Workspace packages depend on each other through `[tool.uv.sources]` entries with `workspace = true`.

The Go daemon lives in `edge/` (`cd edge && go test ./...`).

## Tests

- Unit tests run everywhere and must not touch the network or Docker.
- `@pytest.mark.integration` tests need `just up`.
- Repository-level tests (not tied to one package) live in `tests/`, e.g. the dev stack config check and smoke test.
- `@pytest.mark.slurm` tests need the containerized Slurm cluster (`just up-slurm`).
- Async tests run under `pytest-asyncio` in auto mode, so plain `async def test_...` works.

## CI

`.github/workflows/ci.yml` runs four required checks on every PR: `lint`, `unit`, `go`, `integration`. Keep these job names stable: branch protection depends on them. A fifth job, `slurm`, boots the Slurm dev cluster and runs `pytest -m slurm`; it is not required yet.
