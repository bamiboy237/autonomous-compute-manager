"""NATS subject names (ARCHITECTURE.md §7).

Build subjects with these functions rather than formatting strings: they validate every
token, so an id or site name can never inject ``.``, ``*`` or ``>`` into a subject.

::

    acm.run.<run_id>.metrics          acm.attempt.<attempt_id>.state
    acm.run.<run_id>.heartbeat        acm.edge.<site>.cmd
    acm.run.<run_id>.log              acm.edge.<site>.reply
    acm.run.<run_id>.checkpoint       acm.decision.<campaign_id>
"""

from __future__ import annotations

import re

from acm_core.errors import InvalidInputError
from acm_core.ids import AttemptId, CampaignId, RunId

__all__ = [
    "ALL_ATTEMPTS",
    "ALL_DECISIONS",
    "ALL_EDGE",
    "ALL_RUNS",
    "SITE_PATTERN",
    "attempt_state",
    "decision",
    "edge_cmd",
    "edge_reply",
    "run_all",
    "run_checkpoint",
    "run_heartbeat",
    "run_log",
    "run_metrics",
    "validate_site",
]

ALL_RUNS = "acm.run.>"
ALL_ATTEMPTS = "acm.attempt.>"
ALL_EDGE = "acm.edge.>"
ALL_DECISIONS = "acm.decision.>"

SITE_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"
"""Edge site names: lowercase letters, digits, ``_`` and ``-``; at most 63 characters."""

_SITE_RE = re.compile(SITE_PATTERN)


def validate_site(site: str) -> str:
    """Return ``site`` if it is a valid edge site name, else raise ``InvalidInputError``."""
    if not _SITE_RE.fullmatch(site):
        raise InvalidInputError(
            f"invalid edge site name {site!r}", details={"site": site, "pattern": SITE_PATTERN}
        )
    return site


def run_metrics(run_id: str) -> str:
    return f"acm.run.{RunId(run_id)}.metrics"


def run_heartbeat(run_id: str) -> str:
    return f"acm.run.{RunId(run_id)}.heartbeat"


def run_log(run_id: str) -> str:
    return f"acm.run.{RunId(run_id)}.log"


def run_checkpoint(run_id: str) -> str:
    return f"acm.run.{RunId(run_id)}.checkpoint"


def run_all(run_id: str) -> str:
    """Wildcard for every event of one run (for consumer filters)."""
    return f"acm.run.{RunId(run_id)}.*"


def attempt_state(attempt_id: str) -> str:
    return f"acm.attempt.{AttemptId(attempt_id)}.state"


def edge_cmd(site: str) -> str:
    return f"acm.edge.{validate_site(site)}.cmd"


def edge_reply(site: str) -> str:
    return f"acm.edge.{validate_site(site)}.reply"


def decision(campaign_id: str) -> str:
    return f"acm.decision.{CampaignId(campaign_id)}"
