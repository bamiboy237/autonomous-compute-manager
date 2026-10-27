"""Checkpoints and artifacts kept in the object store."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from acm_core.domain.common import ContentHash, Entity
from acm_core.domain.provider import ProviderKind
from acm_core.ids import ArtifactId, AttemptId, CampaignId, CheckpointId, RunId

__all__ = ["Artifact", "ArtifactKind", "Checkpoint"]


class Checkpoint(Entity):
    """A saved training state. The manifest lists the content-addressed blobs."""

    id: CheckpointId = Field(default_factory=CheckpointId.new)
    run_id: RunId
    attempt_id: AttemptId
    step: int = Field(ge=0)
    manifest_uri: str = Field(min_length=1)
    """``s3://<bucket>/manifests/checkpoints/<run_id>/<step>.json``."""
    metrics: dict[str, float] = Field(default_factory=dict[str, float])
    """Metric values at save time."""
    provider_kind: ProviderKind
    """Provider the checkpoint was written on (its cache may already hold the blobs)."""
    size_bytes: int | None = Field(default=None, ge=0)


class ArtifactKind(StrEnum):
    DATASET = "dataset"
    MODEL = "model"
    SUBMISSION = "submission"
    REPORT = "report"
    OTHER = "other"


class Artifact(Entity):
    """A content-addressed file: dataset, model weights, submission, report."""

    id: ArtifactId = Field(default_factory=ArtifactId.new)
    kind: ArtifactKind
    name: str = Field(min_length=1, max_length=500)
    content_hash: ContentHash
    uri: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    media_type: str | None = None
    campaign_id: CampaignId | None = None
    run_id: RunId | None = None
