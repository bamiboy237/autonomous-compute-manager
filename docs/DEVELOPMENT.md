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
| `just up` / `just down -v` | start / stop and reset the Compose stack |
| `just test-integration` | tests marked `integration` against the running stack |
| `just migrate` | apply database migrations |
| `just ci` | lint + typecheck + unit tests, as CI runs them |

## Layout

Python packages live in `packages/<name>/src/<module>` with tests in `packages/<name>/tests`. Add a dependency to one package with `uv add --package acm-core <dep>`. Workspace packages depend on each other through `[tool.uv.sources]` entries with `workspace = true`.

The Go daemon lives in `edge/` (`cd edge && go test ./...`).

## Tests

- Unit tests run everywhere and must not touch the network or Docker.
- `@pytest.mark.integration` tests need `just up`.
- `@pytest.mark.slurm` tests need the containerized Slurm cluster.
- Async tests run under `pytest-asyncio` in auto mode, so plain `async def test_...` works.

## CI

`.github/workflows/ci.yml` runs four required checks on every PR: `lint`, `unit`, `go`, `integration`. Keep these job names stable: branch protection depends on them.
