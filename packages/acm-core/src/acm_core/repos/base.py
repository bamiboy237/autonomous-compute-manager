"""Generic async repository over one entity table."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from acm_core.db.mapping import Mapping, mapping_for
from acm_core.domain import Entity
from acm_core.domain.state_machine import StateMachine
from acm_core.errors import ConflictError, InvalidInputError, NotFoundError

__all__ = ["Repository", "translate_db_errors"]

DEFAULT_LIMIT = 100


def _constraint(exc: DBAPIError) -> str | None:
    orig: Any = getattr(exc.orig, "__cause__", None) or exc.orig
    return getattr(orig, "constraint_name", None)


async def translate_db_errors[T](call: Callable[[], Awaitable[T]]) -> T:
    """Run a database call, mapping integrity and stale-row errors to ``ConflictError``."""
    try:
        return await call()
    except StaleDataError as exc:
        raise ConflictError(
            "the record was changed by another transaction; reload it and retry"
        ) from exc
    except IntegrityError as exc:
        constraint = _constraint(exc)
        raise ConflictError(
            f"integrity constraint violated: {constraint or 'unknown'}",
            details={"constraint": constraint},
        ) from exc


class Repository[D: Entity]:
    """CRUD for one entity type.

    - ``add``/``update`` flush immediately so constraint and version conflicts surface at the
      call site as ``ConflictError``.
    - ``update`` writes every field except ``id``/``created_at``. For an entity read earlier
      in the same unit of work, the UPDATE is conditional on the version that was read, so a
      concurrent change in between raises ``ConflictError``. For an entity not read in this
      unit of work, pass ``expected_version`` (from an earlier ``version()``) to get the same
      protection; without it the write is last-wins.
    - When the entity has a status state machine, ``update`` calls ``machine.check`` for a
      status change, so an illegal transition raises ``InvalidTransitionError``.
    """

    domain: ClassVar[type[Entity]]
    mutable: ClassVar[bool] = True
    state_machine: ClassVar[StateMachine[Any] | None] = None

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._held: dict[str, Any] = {}
        """Rows loaded by this unit of work. The session's identity map only holds weak
        references, so without this a later ``update`` would reload the row at its *current*
        version and could never detect a change made since the caller read it."""
        self.mapping: Mapping[D, Any] = mapping_for(self.domain)  # type: ignore[arg-type]

    # ---- reads ----

    async def find(self, entity_id: str) -> D | None:
        row = await self.session.get(self.mapping.row, str(entity_id))
        if row is None:
            return None
        self._held[row.id] = row
        return await self._load(row)

    async def get(self, entity_id: str) -> D:
        entity = await self.find(entity_id)
        if entity is None:
            raise NotFoundError(
                f"{self.domain.__name__} {entity_id} not found",
                details={"id": str(entity_id)},
            )
        return entity

    async def version(self, entity_id: str) -> int:
        """Current optimistic-concurrency version of a mutable entity."""
        row = await self._row(entity_id)
        return int(row.version)

    async def query(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        newest_first: bool = False,
        **filters: Any,
    ) -> list[D]:
        """Entities whose columns equal ``filters``, ordered by ``created_at`` then id."""
        row_cls = self.mapping.row
        query = sa.select(row_cls)
        for name, value in filters.items():
            if name not in self.mapping.columns:
                raise InvalidInputError(f"cannot filter {self.domain.__name__} by {name!r}")
            query = query.where(getattr(row_cls, name) == value)
        order = (row_cls.created_at, row_cls.id)
        query = query.order_by(*(c.desc() if newest_first else c for c in order))
        rows = (await self.session.scalars(query.limit(limit).offset(offset))).all()
        self._held.update({r.id: r for r in rows})
        return await self._load_many(rows)

    # ---- writes ----

    async def add(self, entity: D) -> D:
        self.session.add(self.mapping.to_row(entity))
        await self._after_add(entity)
        await translate_db_errors(self.session.flush)
        return entity

    async def update(self, entity: D, *, expected_version: int | None = None) -> D:
        if not self.mutable:
            raise InvalidInputError(f"{self.domain.__name__} records are immutable")
        row = await self._row(entity.id)  # type: ignore[attr-defined]
        if expected_version is not None and row.version != expected_version:
            raise ConflictError(
                f"{self.domain.__name__} {entity.id} is at version {row.version}, "  # type: ignore[attr-defined]
                f"expected {expected_version}"
            )
        if self.state_machine is not None:
            new_status = entity.status  # type: ignore[attr-defined]
            if row.status != new_status:
                self.state_machine.check(row.status, new_status)
        for name, value in self.mapping.to_values(entity).items():
            if name not in ("id", "created_at"):
                setattr(row, name, value)
        await self._after_update(entity)
        await translate_db_errors(self.session.flush)
        return entity

    async def delete(self, entity_id: str) -> None:
        if not self.mutable:
            raise InvalidInputError(f"{self.domain.__name__} records are immutable")
        await self.session.delete(await self._row(entity_id))
        await translate_db_errors(self.session.flush)

    # ---- hooks for aggregates with child tables ----

    async def _after_add(self, entity: D) -> None:
        return None

    async def _after_update(self, entity: D) -> None:
        return None

    async def _load(self, row: Any) -> D:
        return self.mapping.from_row(row)

    async def _load_many(self, rows: Sequence[Any]) -> list[D]:
        return [await self._load(row) for row in rows]

    async def _row(self, entity_id: str) -> Any:
        row = self._held.get(str(entity_id)) or await self.session.get(
            self.mapping.row, str(entity_id)
        )
        if row is None:
            raise NotFoundError(
                f"{self.domain.__name__} {entity_id} not found", details={"id": str(entity_id)}
            )
        return row
