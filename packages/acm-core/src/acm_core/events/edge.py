"""Command RPC between the control plane and ``acm-edge`` (stream ``ACM_EDGE``).

The control plane publishes an ``EdgeCommandEvent`` on ``acm.edge.<site>.cmd``; the edge
daemon at that site runs it and publishes one ``EdgeReplyEvent`` on
``acm.edge.<site>.reply`` whose ``command_id`` is the command's ``id``. The edge daemon
only runs allowlisted commands (ARCHITECTURE.md §6).
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import Field

from acm_core.events import subjects
from acm_core.events.base import Event
from acm_core.events.streams import EDGE_STREAM
from acm_core.ids import EventId

__all__ = ["EdgeCommandEvent", "EdgeCommandKind", "EdgeReplyEvent", "SiteName"]

SiteName = str
"""Edge site name; see ``subjects.SITE_PATTERN``."""


class EdgeCommandKind(StrEnum):
    """The edge allowlist. ``args`` are passed as argv, never through a shell."""

    SBATCH = "sbatch"
    SQUEUE = "squeue"
    SACCT = "sacct"
    SCANCEL = "scancel"
    SCONTROL_SHOW_JOB = "scontrol_show_job"
    TAIL_FILE = "tail_file"
    """``args[0]`` is a path under a configured root; ``args[1]`` the byte offset."""
    READ_FILE = "read_file"
    """``args[0]`` is a path under a configured root."""


class _EdgeEvent(Event):
    stream_name: ClassVar[str] = EDGE_STREAM

    site: str = Field(pattern=subjects.SITE_PATTERN)


class EdgeCommandEvent(_EdgeEvent):
    """A command for the edge daemon at ``site`` (``acm.edge.<site>.cmd``)."""

    v: Literal[1] = 1
    type: Literal["edge.cmd"] = "edge.cmd"
    command: EdgeCommandKind
    args: list[str] = Field(default_factory=list[str])
    stdin: str | None = None
    """Standard input, e.g. the batch script for ``sbatch``."""
    timeout_s: float = Field(default=60, gt=0, le=3600)
    """The edge kills the command after this long and replies with an error."""

    def subject(self) -> str:
        return subjects.edge_cmd(self.site)


class EdgeReplyEvent(_EdgeEvent):
    """The result of one command (``acm.edge.<site>.reply``)."""

    v: Literal[1] = 1
    type: Literal["edge.reply"] = "edge.reply"
    command_id: EventId
    """``id`` of the ``EdgeCommandEvent`` this answers."""
    ok: bool
    """The command ran and exited 0."""
    exit_code: int | None = None
    """``None`` when the command did not run (rejected, timed out, failed to start)."""
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    """Why the command did not run or was killed."""
    duration_s: float = Field(default=0, ge=0)

    def subject(self) -> str:
        return subjects.edge_reply(self.site)
