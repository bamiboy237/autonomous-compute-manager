"""Kaggle submissions. Each one is prepared by the agent and approved by a human."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field

from acm_core.domain.common import Entity
from acm_core.ids import ArtifactId, CampaignId, DecisionId, KaggleSubmissionId, RunId

__all__ = ["KaggleSubmission", "KaggleSubmissionStatus"]


class KaggleSubmissionStatus(StrEnum):
    PREPARED = "prepared"
    """Artifacts and evidence ready; waiting for approval of ``decision_id``."""
    APPROVED = "approved"
    REJECTED = "rejected"
    SUBMITTED = "submitted"
    """Uploaded; Kaggle is scoring."""
    SCORED = "scored"
    FAILED = "failed"
    """Upload or scoring failed (``error`` says why)."""


class KaggleSubmission(Entity):
    """A prepared submission and its Kaggle outcome."""

    id: KaggleSubmissionId = Field(default_factory=KaggleSubmissionId.new)
    campaign_id: CampaignId
    competition: str = Field(min_length=1)
    """Kaggle competition slug."""
    artifact_id: ArtifactId
    """Submission package (notebook + weights dataset reference)."""
    run_id: RunId | None = None
    decision_id: DecisionId
    """The ``submission`` decision a human approves."""
    message: str = Field(min_length=1, max_length=500)
    status: KaggleSubmissionStatus = KaggleSubmissionStatus.PREPARED
    kaggle_ref: str | None = None
    public_score: float | None = None
    private_score: float | None = None
    error: str | None = None
    submitted_at: AwareDatetime | None = None
