"""Fixtures for database integration tests: a throwaway, fully migrated database per session."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from acm_core.db.migrate import upgrade
from acm_core.db.session import create_engine, create_session_factory
from acm_core.settings import get_settings


def _admin_url() -> URL:
    return make_url(get_settings().db.url.get_secret_value())


async def _admin(statement: str) -> None:
    engine = create_engine(get_settings().db, url=_admin_url().render_as_string(False))
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(sa.text(statement))
    await engine.dispose()


@pytest.fixture
def database_url() -> Iterator[str]:
    """URL of an empty database that exists for the duration of one test."""
    name = f"acm_test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    try:
        yield _admin_url().set(database=name).render_as_string(hide_password=False)
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    name = f"acm_test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    url = _admin_url().set(database=name).render_as_string(hide_password=False)
    try:
        upgrade(url)
        yield url
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
async def engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(get_settings().db, url=migrated_database_url)
    yield eng
    async with eng.begin() as conn:
        tables = await conn.scalars(
            sa.text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        await conn.execute(sa.text(f"TRUNCATE {', '.join(tables.all())} CASCADE"))
    await eng.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)
