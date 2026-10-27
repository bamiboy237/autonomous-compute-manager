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
    uv run pytest -m integration || [ $? -eq 5 ]

# Start the dev stack and wait until healthy
up:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -f deploy/compose/compose.yaml ]; then echo "compose stack not defined yet (THE-25)"; exit 0; fi
    {{compose}} up -d --wait

# Stop the dev stack (pass -v to drop volumes)
down *args:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -f deploy/compose/compose.yaml ]; then exit 0; fi
    {{compose}} down {{args}}

# Apply database migrations
migrate:
    @echo "migrations not defined yet (THE-27)"

# Everything CI runs on every PR
ci: lint typecheck test
