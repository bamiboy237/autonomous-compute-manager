# Working in this repository

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing anything. It is the source of truth for decisions, layout and contracts.

## Ownership

- `ui/` — owned by the project owner (Codex + Figma). Backend work never edits `ui/`.
- Everything else — backend. The UI depends on the API contract: `docs/api/openapi.json` (generated) plus the SSE streams in ARCHITECTURE.md §13. Breaking contract changes need a note in the PR description under **API changes**.

## Ticket workflow

Work is tracked in Linear (team Thebookiers, project "Autonomous Compute Manager"). One ticket = one PR.

1. Branch from latest `main`: `<ticket-id-lowercase>-<short-slug>` (e.g. `the-12-slurm-adapter`).
2. PR title: `<TICKET-ID>: <summary>`. Link the Linear issue in the body.
3. Done means: CI green, unit tests, an integration test against the Compose stack when the ticket touches infrastructure or I/O, and docs updated (`docs/` or package README).
4. PRs auto-merge when green. Never skip, disable or weaken a test to get green.
5. If a ticket is blocked by a missing credential or an owner decision, stop, comment on the Linear issue with exactly what is needed, and move on.

## Conventions

- Python 3.12, `uv` workspace, ruff, pyright, pytest. Packages live in `packages/<name>/src/<module>`.
- Go 1.23 for `edge/`.
- Async everywhere on I/O paths. Typed pydantic models at every boundary.
- Temporal workflows stay deterministic; all I/O lives in activities.
- Agents never mutate state directly; they return typed decisions that workflows execute after the policy gate.
- Conventional commits. No secrets in the repo; document new env vars in `.env.example`.
