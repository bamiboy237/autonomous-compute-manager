"""Experiment memory: embedded summaries the planner retrieves (pgvector)."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from acm_core.domain.common import Entity
from acm_core.ids import CampaignId, MemoryItemId

__all__ = ["MemoryItem", "MemorySourceKind"]


class MemorySourceKind(StrEnum):
    RUN = "run"
    INCIDENT = "incident"
    DECISION = "decision"


class MemoryItem(Entity):
    """A summary of a run, incident or decision, with its embedding."""

    id: MemoryItemId = Field(default_factory=MemoryItemId.new)
    campaign_id: CampaignId | None = None
    """``None`` for items shared across campaigns."""
    source_kind: MemorySourceKind
    source_id: str = Field(min_length=1)
    """Id of the run, incident or decision summarized."""
    summary: str = Field(min_length=1)
    embedding: list[float] | None = None
    embedding_model: str | None = None

    @model_validator(mode="after")
    def _check_embedding(self) -> Self:
        if (self.embedding is None) != (self.embedding_model is None):
            raise ValueError("embedding and embedding_model must be set together")
        if self.embedding is not None and not self.embedding:
            raise ValueError("embedding must not be empty")
        return self
