"""Base class shared by every bus event."""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from acm_core.domain.common import utcnow
from acm_core.ids import EventId

__all__ = ["Event"]


class Event(BaseModel):
    """Envelope fields every event carries.

    - ``v``: payload schema version, declared by each subclass as ``v: Literal[n] = n``. A
      breaking change to a payload bumps it (see docs/EVENTS.md, "Versioning").
    - ``type``: stable event type name such as ``run.metrics``, declared by each subclass as
      ``type: Literal[...]``. It discriminates ``AnyEvent``.
    - ``id``: unique per event and used as the JetStream ``Nats-Msg-Id``, so a retried
      publish is stored once.
    - ``ts``: when the event happened (timezone-aware UTC), not when it was published.

    Non-finite floats serialize as the strings ``"NaN"``, ``"Infinity"`` and ``"-Infinity"``
    so payloads stay valid JSON; they parse back to ``float``.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_default=True,
        ser_json_inf_nan="strings",
    )

    id: EventId = Field(default_factory=EventId.new)
    ts: AwareDatetime = Field(default_factory=utcnow)

    stream_name: ClassVar[str]
    """JetStream stream that stores this event type."""

    def subject(self) -> str:
        """NATS subject this event is published on."""
        raise NotImplementedError

    def to_json(self) -> bytes:
        """Serialize to the wire format (UTF-8 JSON)."""
        return self.model_dump_json().encode()

    @classmethod
    def event_type(cls) -> str:
        """The ``type`` literal of this event class."""
        return _literal_default(cls, "type")

    @classmethod
    def version(cls) -> int:
        """The ``v`` literal of this event class."""
        return _literal_default(cls, "v")


def _literal_default(cls: type[Event], field: Literal["type", "v"]) -> Any:
    default = cls.model_fields[field].default
    if default is None:
        raise TypeError(f"{cls.__name__} does not pin {field!r}")
    return default
