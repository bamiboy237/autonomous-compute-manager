"""Declarative base, naming conventions and column helpers shared by every table."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

__all__ = ["Amount", "Base", "Json", "Timestamped", "Versioned", "enum_type", "fk"]

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Json = JSONB
"""Nested value objects (metric, policy, resources, ...) are stored as JSONB."""

Amount = sa.Numeric(28, 10)
"""Exact decimal for amounts and rates."""


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012
        datetime: sa.DateTime(timezone=True),
        Decimal: Amount,
        dict[str, Any]: Json,
    }


def enum_type(enum: type[StrEnum], name: str) -> sa.Enum:
    """VARCHAR column checked against the enum's values.

    A CHECK constraint (not a native PG enum) keeps adding a value a plain
    ``ALTER TABLE ... DROP/ADD CONSTRAINT`` and works on every Postgres host.
    """

    def values(members: type[StrEnum]) -> list[str]:
        return [m.value for m in members]

    return sa.Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=values,
        length=max(len(m.value) for m in enum),
        validate_strings=True,
    )


def fk(target: str, *, nullable: bool = False, **kwargs: Any) -> Mapped[Any]:
    """Foreign key column to ``<table>.id``."""
    return mapped_column(sa.String(64), sa.ForeignKey(target), nullable=nullable, **kwargs)


class Timestamped:
    """``created_at``/``updated_at``: set by the domain model, defaulted by the server."""

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class Versioned:
    """Optimistic concurrency: an UPDATE only matches the version that was loaded.

    SQLAlchemy increments ``version`` on every update and raises ``StaleDataError`` when
    another transaction changed the row first (mapped to ``ConflictError`` by the repos).
    """

    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="1")

    @declared_attr.directive
    @classmethod
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"version_id_col": cls.version}
