# Autonomous Compute Manager — developer commands. Run `just` to list.

set dotenv-load := true

compose := "docker compose -f deploy/compose/compose.yaml"

default:
    @just --list

# Install the Python workspace and dev tools
sync:
    uv sync --all-packages

# Lint Python and Go
lint:
    uv run ruff check .
    uv run ruff format --check .
    cd edge && golangci-lint run ./...

# Format Python and Go
fmt:
    uv run ruff check --fix .
    uv run ruff format .
    cd edge && gofmt -w .

# Type-check Python
typecheck:
    uv run pyright

# Unit tests (no external services)
test:
    uv run pytest -m "not integration and not slurm"
    cd edge && go test ./...

# Integration tests against the Compose stack (`just up` first)
test-integration:
    uv run pytest -m integration

# Start the dev stack and wait until healthy (COMPOSE_PROFILES=observability for otel + Grafana)
up:
    {{compose}} up -d --build --wait --wait-timeout 300
    {{compose}} run --rm minio-init

# Stop the dev stack, all profiles included (pass -v to drop volumes)
down *args:
    {{compose}} --profile '*' down --remove-orphans {{args}}

# Show dev stack containers and their health
ps:
    {{compose}} --profile '*' ps

# Follow dev stack logs (optionally for given services)
logs *services:
    {{compose}} --profile '*' logs -f {{services}}

# Apply database migrations
migrate:
    uv run python -m acm_core.db upgrade

# Everything CI runs on every PR
ci: lint typecheck test
