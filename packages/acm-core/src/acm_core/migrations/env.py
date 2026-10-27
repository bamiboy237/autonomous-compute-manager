"""Alembic environment: async engine from ``Settings.db`` (or ``-x url=...`` / config attribute)."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection

import acm_core.db.models
from acm_core.db.base import Base
from acm_core.db.session import create_engine
from acm_core.settings import get_settings

config = context.config
assert acm_core.db.models.WorkloadRow  # importing the models registers every table
target_metadata = Base.metadata


def _url() -> str | None:
    return config.attributes.get("url") or context.get_x_argument(as_dictionary=True).get("url")


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        transaction_per_migration=True,
    )


def _run_sync(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_engine(get_settings().db, url=_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=_url() or get_settings().db.url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_async())
