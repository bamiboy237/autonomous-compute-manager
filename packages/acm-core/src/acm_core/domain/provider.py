"""Compute providers: configured accounts and the targets (partitions, GPU types) they offer."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, JsonValue

from acm_core.domain.common import Entity, Money
from acm_core.ids import ComputeTargetId, ProviderAccountId

__all__ = ["ComputeTarget", "ProviderAccount", "ProviderKind"]


class ProviderKind(StrEnum):
    SLURM = "slurm"
    MODAL = "modal"
    THUNDERCOMPUTE = "thundercompute"
    KUBERNETES = "kubernetes"
    LOCAL = "local"
    SIMULATOR = "simulator"


class ProviderAccount(Entity):
    """A configured provider (one Slurm cluster, one Modal workspace, ...).

    ``config`` holds non-secret settings (hosts, partitions, edge site name). Credentials are
    never stored here: ``credentials_ref`` names a secret in the deployment's secret store.
    """

    id: ProviderAccountId = Field(default_factory=ProviderAccountId.new)
    kind: ProviderKind
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    credentials_ref: str | None = None
    enabled: bool = True


class ComputeTarget(Entity):
    """A placeable resource pool of a provider account: a Slurm partition, a Modal GPU type."""

    id: ComputeTargetId = Field(default_factory=ComputeTargetId.new)
    provider_account_id: ProviderAccountId
    name: str = Field(min_length=1, max_length=200)
    gpu_type: str | None = None
    """Normalized GPU model, e.g. ``a100-80gb``; ``None`` for CPU-only targets."""
    gpu_memory_gb: int | None = Field(default=None, gt=0)
    gpus_per_node: int = Field(default=0, ge=0)
    max_gpus: int | None = Field(default=None, gt=0)
    """Most GPUs one job may request; ``None`` if unlimited."""
    max_walltime: timedelta | None = None
    preemptible: bool = False
    price_per_gpu_hour: Money | None = None
    """Cash or allocation price per GPU-hour (``usd`` or ``slurm_su``)."""
    gpu_hour_norm_rate: Decimal = Field(default=Decimal("1"), ge=0)
    """``gpu_hour_norm`` charged per GPU-hour here (A100-80G = 1.0)."""
    enabled: bool = True
