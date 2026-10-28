# Orchestration guide

How the backend is built: one orchestrator session dispatches Linear tickets to worker sessions. Read this if you are the orchestrator.

## Roles

- **Owner** — decides; supplies credentials; owns `ui/`.
- **Orchestrator** — one cloud session, model `claude-sonnet-5-5`. Picks unblocked tickets, starts worker sessions, merges green PRs the workers can't, keeps Linear accurate. Writes little code itself.
- **Workers** — one cloud session per ticket, model `claude-sonnet-5-5`, started with the prompt template below.

## Usage budget (most important rule)

The owner's weekly usage limit is the binding constraint.

- At most **3 workers** running at once.
- Workers do **not** run Docker or integration tests locally. CI runs `integration` and `slurm` on GitHub for free. Workers run `just lint`, `just typecheck` and the unit tests of the packages they touch.
- Workers read only the ARCHITECTURE.md sections named in their prompt, plus the READMEs of the packages they depend on.
- No subagents. No polling loops. A worker that hits a limit pushes a `wip:` commit and stops.
- The orchestrator acts only on events: a worker's report, a PR event, or a check-in at most every 60 minutes. It keeps no long transcripts in context: Linear and this file are its memory.
- Model policy: Sonnet by default; Opus only for work the owner calls mission-critical.

## Dispatch loop

1. List Linear issues in project "Autonomous Compute Manager" with label `backend` and state Todo/In Progress.
2. A ticket is ready when every `blockedBy` ticket is Done. Prefer the lowest milestone, then priority, then the critical path to THE-40 (the UI mock API) and THE-46 (run/attempt workflows).
3. If `origin/<branch>` already exists for the ticket, the worker resumes it (a previous worker was cut off).
4. Start the worker with `create_session` (model `claude-sonnet-5-5`, source_url the repo, tag `acm-worker`) using the template.
5. When a worker reports, verify the PR on GitHub, update Linear if the worker couldn't, archive the worker session, dispatch the next ticket.

## Merge rules

A PR merges (squash) when `lint`, `unit`, `go`, `integration` (and `slurm` if the PR touches Slurm) are green and there is no conflict.

**Entire Gates** (the owner's forge) also runs on every PR:

- **Approvals** row failing: not blocking (owner decision).
- **Checks**, **Findings**, **Up to date** rows failing: blocking. Fix findings in the PR. "Up to date" means merge `main` into the branch.

Workers often refuse to merge past a failing Approvals row on a relayed decision. The orchestrator then verifies the rows and merges itself.

## Section map for worker prompts

| Ticket area | ARCHITECTURE.md sections |
|---|---|
| core, DB, ledger | §4, §11 |
| Temporal workflows | §5, §9 |
| providers, edge, Slurm | §6, §12 |
| telemetry, SDK | §7, §12 |
| agents | §8, §10 |
| API | §13 |
| deploy | §14 |
| CASMI workload | §15 |

## Worker prompt template

Replace `{TICKET}`, `{BRANCH}`, `{SECTIONS}`, `{PARALLEL}`.

```
You are a backend engineer on Autonomous Compute Manager (repo bamiboy237/autonomous-compute-manager). Implement Linear ticket {TICKET} end to end and get it merged.

Budget: the owner's usage limit is tight. Read only what you need. Do NOT run Docker, `just up` or integration tests locally; CI runs them. Locally run `just lint`, `just typecheck` and the unit tests of the packages you touch. No subagents. If you hit a usage limit, push a `wip:` commit first.

Steps
1. Read AGENTS.md, docs/ORCHESTRATION.md (merge rules), ARCHITECTURE.md sections {SECTIONS}, and the README of each package you depend on. Use your coding-standards and tdd skills.
2. Linear: get_issue {TICKET}, set In Progress, read comments on its blocking tickets.
3. git fetch origin. If origin/{BRANCH} exists, resume it (read its Linear WIP comment, merge origin/main in). Else git checkout -b {BRANCH} origin/main.
4. Tooling if missing: `uv tool install rust-just`; `uv sync --all-packages`.
5. Implement the Scope; meet every Acceptance item. Write integration tests (@pytest.mark.integration) where the ticket touches I/O; CI runs them. Pin deps with `uv add --package <pkg> <dep>`; commit uv.lock.
6. Commit (conventional commits), push, open PR "{TICKET}: <summary>" linking the Linear issue, with What changed / How verified / API changes. End the body with: 🤖 Generated with [Claude Code](https://claude.com/claude-code)
7. Subscribe to the PR. Fix CI and blocking Entire rows. Never skip or weaken a test.
8. Merge per docs/ORCHESTRATION.md merge rules. Set Linear Done with a comment: what landed, public APIs, env vars, notes for downstream tickets.
9. send_message to the orchestrator: ticket, PR URL, merged yes/no, notes.

Rules: touch only files your ticket needs ({PARALLEL} run in parallel). Never edit ui/. If blocked on a credential or owner decision, comment on Linear, message the orchestrator, stop. No model names in commits/PRs/code except the trailer. Commit messages end with:
Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
```
