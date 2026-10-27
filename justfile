# Autonomous Compute Manager — developer commands. Run `just` to list.

set dotenv-load := true

compose := "docker compose -f deploy/compose/compose.yaml"
slurm_compose := "docker compose -f deploy/slurm-dev/compose.yaml --profile slurm"

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

# Build and start the containerized Slurm cluster and wait until it accepts jobs
up-slurm:
    #!/usr/bin/env bash
    set -euo pipefail
    key=deploy/slurm-dev/.ssh/id_ed25519
    if [ ! -f "$key" ]; then
        mkdir -p "$(dirname "$key")"
        if command -v ssh-keygen >/dev/null; then
            ssh-keygen -q -t ed25519 -N '' -C acm-slurm-dev -f "$key"
        else
            {{slurm_compose}} build init
            docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/$(dirname "$key"):/out" \
                --entrypoint ssh-keygen acm/slurm-dev:23.11.4 \
                -q -t ed25519 -N '' -C acm-slurm-dev -f /out/id_ed25519
        fi
    fi
    {{slurm_compose}} up -d --build --wait

# Stop the Slurm cluster (pass -v to drop volumes)
down-slurm *args:
    {{slurm_compose}} down {{args}}

# Tests marked `slurm` against the running cluster (`just up-slurm` first)
test-slurm:
    uv run pytest -m slurm

# Apply database migrations
migrate:
    @echo "migrations not defined yet (THE-27)"

# Everything CI runs on every PR
ci: lint typecheck test
