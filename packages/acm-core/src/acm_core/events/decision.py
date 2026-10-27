"""Decision lifecycle (``acm.decision.<campaign_id>``, stream ``ACM_DECISIONS``).

Published by the workflow that owns the decision on every status change; the API relays
them to the UI over SSE. The full decision (evidence, proposal) is read from the API.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from acm_core.domain.common import Actor
from acm_core.domain.decision import ActionKind, DecisionKind, DecisionStatus
from acm_core.events import subjects
from acm_core.events.base import Event
from acm_core.events.streams import DECISIONS_STREAM
from acm_core.ids import CampaignId, DecisionId

__all__ = ["DecisionEvent"]


class DecisionEvent(Event):
    """A decision was proposed or changed status."""

    stream_name: ClassVar[str] = DECISIONS_STREAM

    v: Literal[1] = 1
    type: Literal["decision.lifecycle"] = "decision.lifecycle"
    campaign_id: CampaignId
    decision_id: DecisionId
    kind: DecisionKind
    action: ActionKind | None = None
    status: DecisionStatus
    previous_status: DecisionStatus | None = None
    """``None`` when the decision is first proposed."""
    requires_approval: bool = False
    """Whether the policy gate is waiting on a human (only meaningful while ``proposed``)."""
    actor: Actor | None = None
    """Who caused this change: the proposing agent, the approver, or the system."""
    summary: str | None = Field(default=None, max_length=1000)
    """One line for notifications and lists."""

    def subject(self) -> str:
        return subjects.decision(self.campaign_id)
