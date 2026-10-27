"""Async engine and session factory built from ``Settings.db``."""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from acm_core.settings import DatabaseSettings

__all__ = ["create_engine", "create_session_factory"]


def create_engine(settings: DatabaseSettings, *, url: str | None = None) -> AsyncEngine:
    """Engine for ``settings.db`` (or an explicit ``url``).

    Behind PgBouncer in transaction-pooling mode (``settings.pgbouncer``) server connections
    are shared between clients, so prepared statements must be off: asyncpg's statement
    cache and SQLAlchemy's prepared-statement cache are disabled and pool pre-ping is kept
    so a connection dropped by the pooler is replaced.
    """
    target = make_url(url or settings.url.get_secret_value())
    connect_args: dict[str, Any] = {}
    if settings.pgbouncer:
        target = target.update_query_dict({"prepared_statement_cache_size": "0"})
        connect_args["statement_cache_size"] = 0
    return create_async_engine(
        target,
        echo=settings.echo,
        pool_size=settings.pool_size,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions that keep loaded rows after commit (the repos return domain models)."""
    return async_sessionmaker(engine, expire_on_commit=False)
