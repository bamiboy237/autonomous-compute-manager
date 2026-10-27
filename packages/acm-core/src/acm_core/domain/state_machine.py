"""Finite state machines for entity status fields.

A ``StateMachine`` is a fixed table of allowed ``(from, to)`` status pairs. Status changes on
``Run``, ``Attempt`` and ``Decision`` go through their machine, so an illegal change (for
example ``succeeded -> running``) raises ``InvalidTransitionError`` instead of silently
corrupting the record. Workflows call the entity's ``transition_to()``; persistence code can
call ``machine.check()`` directly when it only has the status values.

Self-transitions are not allowed: re-applying the current status is a caller bug or a
duplicate event, and callers that expect duplicates check ``status == target`` first.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum

from acm_core.errors import InvalidTransitionError

__all__ = ["StateMachine"]


class StateMachine[S: StrEnum]:
    """Allowed transitions between the members of one status enum."""

    __slots__ = ("_allowed", "_initial", "name", "status_type")

    def __init__(
        self,
        name: str,
        status_type: type[S],
        *,
        initial: S,
        transitions: Mapping[S, Iterable[S]],
    ) -> None:
        """``transitions`` maps each status to the statuses it may move to.

        Every member of ``status_type`` must appear as a key; terminal statuses map to an
        empty collection. This makes forgetting a status a construction-time error.
        """
        missing = set(status_type) - set(transitions)
        if missing:
            raise ValueError(f"{name}: no transitions declared for {sorted(missing)}")
        allowed = {src: frozenset(dsts) for src, dsts in transitions.items()}
        for src, dsts in allowed.items():
            if src in dsts:
                raise ValueError(f"{name}: self-transition declared for {src}")
        self.name = name
        self.status_type = status_type
        self._initial = initial
        self._allowed: dict[S, frozenset[S]] = allowed

    @property
    def initial(self) -> S:
        """Status a new entity starts in."""
        return self._initial

    @property
    def terminal(self) -> frozenset[S]:
        """Statuses with no outgoing transitions."""
        return frozenset(s for s, dsts in self._allowed.items() if not dsts)

    def is_terminal(self, status: S) -> bool:
        return not self._allowed[status]

    def allowed_from(self, status: S) -> frozenset[S]:
        """Statuses reachable from ``status`` in one step."""
        return self._allowed[status]

    def can_transition(self, src: S, dst: S) -> bool:
        return dst in self._allowed[src]

    def check(self, src: S, dst: S) -> None:
        """Raise ``InvalidTransitionError`` unless ``src -> dst`` is allowed."""
        if dst not in self._allowed[src]:
            allowed = sorted(self._allowed[src])
            raise InvalidTransitionError(
                f"{self.name}: cannot transition from {src.value!r} to {dst.value!r}",
                details={
                    "machine": self.name,
                    "from": src.value,
                    "to": dst.value,
                    "allowed": [s.value for s in allowed],
                },
            )

    def transitions(self) -> frozenset[tuple[S, S]]:
        """Every allowed ``(from, to)`` pair (for docs and exhaustive tests)."""
        return frozenset((src, dst) for src, dsts in self._allowed.items() for dst in dsts)

    def __repr__(self) -> str:
        return f"StateMachine({self.name!r}, {self.status_type.__name__})"
