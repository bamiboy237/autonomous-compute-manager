"""Run Alembic programmatically (``just migrate``, tests, deployment entrypoints)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

__all__ = ["alembic_config", "downgrade", "upgrade"]

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def alembic_config(url: str | None = None) -> Config:
    """Alembic config for the packaged migrations. ``url`` defaults to ``Settings.db.url``."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    if url is not None:
        config.attributes["url"] = url
    return config


def upgrade(url: str | None = None, revision: str = "head") -> None:
    """Apply migrations up to ``revision``. Blocking: use ``asyncio.to_thread`` in async code."""
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str | None = None, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)
