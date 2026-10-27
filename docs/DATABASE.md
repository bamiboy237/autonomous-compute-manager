# Database

Postgres is the system of record for every entity in [ARCHITECTURE.md §4](ARCHITECTURE.md). Code
lives in `acm_core`:

| Module | Contents |
|---|---|
| `acm_core.db.models` | SQLAlchemy 2 tables, one per domain entity (plus `budget_lines`) |
| `acm_core.db.mapping` | Mechanical mapping between frozen `acm_core.domain` models and rows |
| `acm_core.db.session` | `create_engine(settings.db)`, `create_session_factory(engine)` |
| `acm_core.db.migrate` | `upgrade()` / `downgrade()` (programmatic Alembic) |
| `acm_core.migrations` | Alembic environment and versions |
| `acm_core.repos` | `UnitOfWork` and one repository per aggregate |

## Running migrations

```sh
just up          # Compose Postgres
just migrate     # alembic upgrade head against ACM_DB__URL
uv run python -m acm_core.db downgrade base   # or a revision; default is one step back
```

In code, call `acm_core.db.migrate.upgrade(url)`. It is blocking and starts its own event loop, so
from async code use `await asyncio.to_thread(upgrade, url)`.

The vector size is fixed when the migration runs: `ACM_DB__EMBEDDING_DIM` (default `1024`, at most
`2000`, pgvector's HNSW limit) becomes `memory_items.embedding vector(N)`. Changing it later needs a
new migration and a re-embed. A test checks that the models and the migrated schema have no drift,
so after changing `models.py` generate a migration with
`alembic.command.revision(alembic_config(url), autogenerate=True, message=...)` against a
database at `head` and review it by hand.

## Schema conventions

- **Ids** are the prefixed ULID strings from `acm_core.ids` (`VARCHAR(64)` primary keys).
- **Timestamps**: every table has `created_at`/`updated_at` (`timestamptz`), set by the domain model.
- **Enums** are `VARCHAR` plus a named `CHECK` constraint, not native Postgres enums. Adding a value
  is a `DROP CONSTRAINT`/`ADD CONSTRAINT` pair, which works on every host and inside a transaction.
- **Nested value objects** (metric, policy, resources, actors, expected cost, recovery steps,
  hypothesis evidence, ...) are `JSONB`. Non-finite floats are written as `"NaN"`/`"Infinity"`
  strings, so a NaN metric survives the round trip.
- **Decision evidence** lives in the `evidence` table (ordered by `position`); the decision repo
  loads and stores it with the decision.
- **A budget and its lines** form one aggregate; `budget_lines` is unique per `(budget_id, currency)`.
- **Hot-path indexes**: `campaign_id` on every campaign-scoped table, `run_id`, `status` columns,
  `created_at` on the list-heavy tables, `(campaign_id, account)` and `transaction_id` on the ledger.
- **Unique keys** include one active attempt per run (partial unique index
  `uq_attempts_one_active_per_run` over `pending`/`submitted`/`running`/`cancelling`),
  `(run_id, ordinal)`, `(workload_id, git_sha)`, `(attempt_id, step)` for checkpoints, one budget per
  campaign, and one memory item per `(source_kind, source_id)`.
- **Foreign-key cycles** (`runs.current_attempt_id`, `attempts.resumed_from_checkpoint_id`,
  `code_versions.decision_id`) are `DEFERRABLE INITIALLY DEFERRED`: a transaction may set them before
  the referenced row exists, and Postgres checks at commit.
- **Memory**: `memory_items.embedding` has an HNSW index with `vector_cosine_ops`
  (`MemoryRepo.search` orders by cosine distance, and includes campaign-less shared items).

## Repository layer

```python
factory = create_session_factory(create_engine(get_settings().db))
async with UnitOfWork(factory) as uow:
    run = await uow.runs.get(run_id)
    await uow.runs.update(run.transition_to(RunStatus.RUNNING))
    await uow.commit()  # leaving the block without commit() rolls back
```

- One `UnitOfWork` is one transaction; every repository (`uow.campaigns`, `uow.runs`,
  `uow.decisions`, ...) shares its session. Repositories flush on `add`/`update`, so conflicts
  surface at the call site.
- Repositories take and return the frozen domain models. Methods: `get` (raises `NotFoundError`),
  `find`, `add`, `update`, `delete`, `query(**column_filters)`, `version`.
- **State guard.** `runs`, `attempts` and `decisions` updates call the entity's state machine; an
  illegal status change raises `InvalidTransitionError` before anything is written.
- **Optimistic concurrency.** Mutable aggregates have a `version` column that SQLAlchemy increments
  on every update. An entity read earlier in the same unit of work is updated conditionally on the
  version that was read, so a concurrent change raises `ConflictError` (HTTP 409). For an entity
  not read in this unit of work, pass `expected_version=` (from `version()`); without it the write
  is last-wins. Handle a conflict by reloading in a new unit of work and retrying.
- **Integrity errors** (unique, foreign key, check) become `ConflictError` with the constraint name
  in `details`; deferred foreign keys fail at `commit()`.
- **Immutable records** (`code_versions`, `checkpoints`, `artifacts`, `approvals`, `model_calls`,
  `ledger_entries`) have no `update`/`delete`. The ledger is posted with `uow.ledger.post(entries)`,
  which rejects an unbalanced transaction (`check_balanced`).
- After an error, discard the unit of work: the transaction is already aborted.

## PlanetScale Postgres

The schema uses only core Postgres 16+ features plus the `vector` extension, so it needs no
superuser-only features. `CREATE EXTENSION IF NOT EXISTS vector` is the only privileged statement; on
PlanetScale enable `vector` for the database first. Downgrading leaves the extension installed.

- **Connection pooling.** Set `ACM_DB__PGBOUNCER=true` when connecting through PgBouncer in
  transaction-pooling mode (PlanetScale's pooled port). That disables asyncpg's statement cache and
  SQLAlchemy's prepared-statement cache, since server connections are shared between clients. Keep
  `ACM_DB__POOL_SIZE` at or below the pooler's per-database limit, and run migrations over a
  direct (non-pooled) connection.
- **No long-held locks.** Migrations run one transaction per migration; DDL is plain
  `CREATE`/`ALTER`. Create HNSW indexes on a populated table with `CREATE INDEX CONCURRENTLY` in a
  hand-written migration (outside a transaction).
- **Migration workflow with branches.** Develop a schema change on a database branch, point
  `ACM_DB__URL` at it, run `just migrate`, and run `just test-integration`. Open a deploy request to
  merge the branch schema into `main`; apply the same Alembic revision to production with
  `just migrate` against the production direct URL so `alembic_version` stays in step. Keep
  migrations backward compatible with the previous release (add before use, drop in a later
  revision) because the old API and workers keep running during the deploy.
- **Credentials.** The URL is a secret (`SecretStr`); it never goes in the repo.

## Tests

Unit tests (`test_db_mapping.py`) check that every domain entity has a table and mapping. Integration
tests (`test_db_integration.py`, `just test-integration`) create a throwaway database on the Compose
Postgres and cover migrate up/down/up, schema drift, CRUD for every aggregate, the state-transition
guard, the one-active-attempt index, deferred foreign keys, concurrent-update conflicts, ledger
balance, vector search and PgBouncer mode.
