"""Provider interface, capability model and compute adapters."""

from acm_providers.base import (
    Capabilities,
    CheckpointConfig,
    DataMount,
    GpuType,
    JobSignal,
    JobSpec,
    JobState,
    JobStatus,
    LogChunk,
    LogLine,
    PricingModel,
    Provider,
    ProviderJobRef,
    QueueSnapshot,
    Resources,
    TargetQueue,
    UsageRecord,
)
from acm_providers.registry import (
    BuildContext,
    ProviderRegistry,
    capabilities_from_targets,
    default_registry,
)

__version__ = "0.1.0"

__all__ = [
    "BuildContext",
    "Capabilities",
    "CheckpointConfig",
    "DataMount",
    "GpuType",
    "JobSignal",
    "JobSpec",
    "JobState",
    "JobStatus",
    "LogChunk",
    "LogLine",
    "PricingModel",
    "Provider",
    "ProviderJobRef",
    "ProviderRegistry",
    "QueueSnapshot",
    "Resources",
    "TargetQueue",
    "UsageRecord",
    "__version__",
    "capabilities_from_targets",
    "default_registry",
]
