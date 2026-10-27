"""Workloads and their code versions."""

from __future__ import annotations

from pydantic import Field

from acm_core.domain.common import ContentHash, Entity
from acm_core.ids import CodeVersionId, DecisionId, WorkloadId

__all__ = ["CodeVersion", "Workload"]


class Workload(Entity):
    """The user's code: a git repository and the command that runs it."""

    id: WorkloadId = Field(default_factory=WorkloadId.new)
    name: str = Field(min_length=1, max_length=200)
    repo_url: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1)
    """Command run inside the repo, e.g. ``python -m casmi.train``."""
    image: str | None = None
    """Default container image; providers without containers use the repo's environment."""
    description: str | None = None


class CodeVersion(Entity):
    """An immutable snapshot of workload code.

    Patches proposed by the Engineer agent become new versions whose ``parent_id`` is the
    patched version and whose ``decision_id`` is the approved ``code_change`` decision.
    """

    id: CodeVersionId = Field(default_factory=CodeVersionId.new)
    workload_id: WorkloadId
    git_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    content_hash: ContentHash
    """Hash of the tracked file tree, so identical code under different commits is detectable."""
    parent_id: CodeVersionId | None = None
    decision_id: DecisionId | None = None
    message: str | None = None
