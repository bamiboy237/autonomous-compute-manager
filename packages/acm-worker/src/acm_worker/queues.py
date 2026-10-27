"""Task queue names (ARCHITECTURE.md §5).

Workflow code cannot read the environment, so the queue names travel with each workflow's
input as a ``TaskQueues`` value. Whoever starts a ``CampaignWorkflow`` (the API) builds it
with ``TaskQueues.from_settings(get_settings())``; child workflows inherit it.
"""

from __future__ import annotations

from enum import StrEnum

from acm_core.settings import Settings
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["DEFAULT_TASK_QUEUES", "QueueRole", "TaskQueues"]


class QueueRole(StrEnum):
    """What runs on a queue. Worker processes are started per role."""

    CONTROL = "control"
    """All workflows and cheap activities (record reads/writes, deterministic rules)."""
    AGENTS = "agents"
    """LLM activities."""
    IO = "io"
    """Provider and storage activities."""


class TaskQueues(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    control: str = Field(default="acm-control", min_length=1)
    agents: str = Field(default="acm-agents", min_length=1)
    io: str = Field(default="acm-io", min_length=1)

    @classmethod
    def from_settings(cls, settings: Settings) -> TaskQueues:
        temporal = settings.temporal
        return cls(
            control=temporal.task_queue_control,
            agents=temporal.task_queue_agents,
            io=temporal.task_queue_io,
        )

    def for_role(self, role: QueueRole) -> str:
        return {QueueRole.CONTROL: self.control, QueueRole.AGENTS: self.agents}.get(
            role, self.io
        )


DEFAULT_TASK_QUEUES = TaskQueues()
