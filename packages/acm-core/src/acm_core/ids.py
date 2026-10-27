"""Prefixed ULID identifiers.

Every entity id is a lowercase type prefix, an underscore and a canonical (uppercase
Crockford base32) ULID, for example ``cmp_01JB3V6Q1Z8K4M2N5P7R9S0T1W``. The prefix makes ids
self-describing in logs and URLs; the ULID makes them unique and sortable by creation time.

Each entity has its own ``str`` subclass (``CampaignId``, ``RunId``, ...). The classes are
distinct to the type checker, so passing a ``RunId`` where a ``CampaignId`` is expected is a
static error, and they validate their prefix at runtime::

    run_id = RunId.new()
    RunId(str(run_id)) == run_id         # round-trips through plain strings
    RunId("cmp_01JB...")                 # raises InvalidIdError (wrong prefix)
    parse_id("run_01JB...")              # -> RunId, chosen by prefix

They are pydantic-compatible: a field annotated ``id: RunId`` accepts a string, validates
it, and serializes back to a plain JSON string with a ``pattern`` in the JSON schema.

Only canonical ids are accepted: no lowercase ULIDs, no surrounding whitespace. One entity
therefore has exactly one textual id, which keeps equality, hashing and database keys simple.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, ClassVar, Self

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler, JsonValue
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema
from ulid import ULID

from acm_core.errors import InvalidIdError

__all__ = [
    "ApprovalId",
    "ArtifactId",
    "AttemptId",
    "BudgetId",
    "BudgetLineId",
    "CampaignId",
    "CheckpointId",
    "CodeVersionId",
    "ComputeTargetId",
    "DecisionId",
    "EventId",
    "EvidenceId",
    "ExperimentId",
    "HypothesisId",
    "IncidentId",
    "KaggleSubmissionId",
    "LedgerEntryId",
    "LedgerTransactionId",
    "MemoryItemId",
    "ModelCallId",
    "PrefixedId",
    "ProviderAccountId",
    "RunId",
    "WorkloadId",
    "id_types",
    "parse_id",
]

_ULID_PATTERN = "[0-7][0-9A-HJKMNP-TV-Z]{25}"
_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]{0,7}$")

_REGISTRY: dict[str, type[PrefixedId]] = {}


class PrefixedId(str):
    """Base class for prefixed ULID ids. Subclass it with a ``prefix`` to define an id type.

    Instances are ``str``, so they can be used anywhere a string is expected (dict keys,
    SQL parameters, NATS subjects). Construct one from an existing string with
    ``SomeId(value)`` / ``SomeId.parse(value)``, or mint a fresh one with ``SomeId.new()``.
    """

    __slots__ = ()

    prefix: ClassVar[str]
    """Type prefix without the underscore, e.g. ``"cmp"``. Unique across all id types."""

    _pattern: ClassVar[re.Pattern[str]]

    def __init_subclass__(cls, *, prefix: str, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not _PREFIX_RE.fullmatch(prefix):
            raise ValueError(f"id prefix must match {_PREFIX_RE.pattern!r}, got {prefix!r}")
        if prefix in _REGISTRY:
            existing = _REGISTRY[prefix].__name__
            raise ValueError(f"id prefix {prefix!r} is already used by {existing}")
        cls.prefix = prefix
        cls._pattern = re.compile(f"^{prefix}_{_ULID_PATTERN}$")
        _REGISTRY[prefix] = cls

    def __new__(cls, value: str) -> Self:
        if cls is PrefixedId:
            raise TypeError("PrefixedId is abstract; use a concrete id type or parse_id()")
        if type(value) is cls:
            return value
        if not isinstance(value, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise InvalidIdError(f"{cls.__name__} must be a string, got {type(value).__name__}")
        if not cls._pattern.fullmatch(value):
            raise InvalidIdError(
                f"invalid {cls.__name__} {value!r}: expected '{cls.prefix}_' followed by a "
                "26-character uppercase ULID",
                details={"value": value, "expected_prefix": cls.prefix},
            )
        try:
            ULID.from_str(value[len(cls.prefix) + 1 :])
        except ValueError as exc:  # pragma: no cover - the pattern already rejects these
            raise InvalidIdError(f"invalid {cls.__name__} {value!r}: {exc}") from exc
        return super().__new__(cls, value)

    @classmethod
    def new(cls, *, at: datetime | None = None) -> Self:
        """Mint a new id. ``at`` (timezone-aware) sets the ULID timestamp; default is now."""
        ulid = ULID() if at is None else ULID.from_datetime(at)
        return cls(f"{cls.prefix}_{ulid}")

    @classmethod
    def parse(cls, value: str) -> Self:
        """Validate ``value`` as this id type. Equivalent to ``cls(value)``."""
        return cls(value)

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """Return whether ``value`` is a valid id of this type, without raising."""
        try:
            cls(value)
        except InvalidIdError:
            return False
        return True

    @property
    def ulid(self) -> ULID:
        """The ULID part of the id."""
        return ULID.from_str(self[len(self.prefix) + 1 :])

    @property
    def created_at(self) -> datetime:
        """The creation time encoded in the ULID (UTC, millisecond precision)."""
        return self.ulid.datetime

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str.__repr__(self)})"

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.str_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(
                str, return_schema=core_schema.str_schema()
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        json_schema = handler(schema)
        json_schema.update(
            pattern=cls._pattern.pattern,
            examples=[f"{cls.prefix}_01JB3V6Q1Z8K4M2N5P7R9S0T1W"],
        )
        return json_schema

    @classmethod
    def _validate(cls, value: str) -> Self:
        # Raise ValueError so pydantic reports a normal ValidationError.
        try:
            return cls(value)
        except InvalidIdError as exc:
            raise ValueError(exc.message) from exc


def parse_id(value: str) -> PrefixedId:
    """Parse an id of any registered type, choosing the class by its prefix."""
    prefix, sep, _ = value.partition("_")
    id_type = _REGISTRY.get(prefix) if sep else None
    if id_type is None:
        known: list[JsonValue] = [p for p in sorted(_REGISTRY)]
        raise InvalidIdError(
            f"unknown id prefix in {value!r}", details={"value": value, "known_prefixes": known}
        )
    return id_type(value)


def id_types() -> dict[str, type[PrefixedId]]:
    """All registered id types, keyed by prefix."""
    return dict(_REGISTRY)


class CampaignId(PrefixedId, prefix="cmp"):
    """Id of a ``Campaign``."""


class BudgetId(PrefixedId, prefix="bgt"):
    """Id of a ``Budget``."""


class BudgetLineId(PrefixedId, prefix="bdl"):
    """Id of a ``BudgetLine``."""


class WorkloadId(PrefixedId, prefix="wl"):
    """Id of a ``Workload``."""


class CodeVersionId(PrefixedId, prefix="cv"):
    """Id of a ``CodeVersion``."""


class HypothesisId(PrefixedId, prefix="hyp"):
    """Id of a ``Hypothesis``."""


class ExperimentId(PrefixedId, prefix="exp"):
    """Id of an ``Experiment``."""


class RunId(PrefixedId, prefix="run"):
    """Id of a ``Run``."""


class AttemptId(PrefixedId, prefix="att"):
    """Id of an ``Attempt``."""


class ProviderAccountId(PrefixedId, prefix="pa"):
    """Id of a ``ProviderAccount``."""


class ComputeTargetId(PrefixedId, prefix="tgt"):
    """Id of a ``ComputeTarget``."""


class CheckpointId(PrefixedId, prefix="ckpt"):
    """Id of a ``Checkpoint``."""


class ArtifactId(PrefixedId, prefix="art"):
    """Id of an ``Artifact``."""


class DecisionId(PrefixedId, prefix="dec"):
    """Id of a ``Decision``."""


class EvidenceId(PrefixedId, prefix="evd"):
    """Id of an ``Evidence`` row."""


class ApprovalId(PrefixedId, prefix="apr"):
    """Id of an ``Approval``."""


class LedgerEntryId(PrefixedId, prefix="led"):
    """Id of a ``LedgerEntry``."""


class LedgerTransactionId(PrefixedId, prefix="ltx"):
    """Id grouping the balanced ``LedgerEntry`` rows of one double-entry transaction."""


class ModelCallId(PrefixedId, prefix="mc"):
    """Id of a ``ModelCall``."""


class IncidentId(PrefixedId, prefix="inc"):
    """Id of an ``Incident``."""


class MemoryItemId(PrefixedId, prefix="mem"):
    """Id of a ``MemoryItem``."""


class KaggleSubmissionId(PrefixedId, prefix="ksub"):
    """Id of a ``KaggleSubmission``."""


class EventId(PrefixedId, prefix="evt"):
    """Id of a bus event (``acm_core.events``); also its JetStream de-duplication key."""
