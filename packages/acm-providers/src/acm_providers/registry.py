"""Registry that builds providers from configured ``ProviderAccount``/``ComputeTarget`` rows.

Each adapter registers its kind with a pydantic settings model (validating
``ProviderAccount.config``) and a factory::

    registry.register(ProviderKind.SLURM, SlurmSettings, build_slurm)

``ProviderRegistry.build`` validates the config, resolves the credential and calls the factory.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from acm_core.domain import ComputeTarget, ProviderAccount, ProviderKind
from acm_core.errors import ConfigurationError
from acm_providers.base import Capabilities, GpuType, PricingModel, Provider

__all__ = [
    "BuildContext",
    "CredentialResolver",
    "ProviderFactory",
    "ProviderRegistry",
    "capabilities_from_targets",
    "default_registry",
]

CredentialResolver = Callable[[str], str]
"""Maps ``ProviderAccount.credentials_ref`` to the secret value (from the deployment's secret
store). Raise ``ConfigurationError`` when it cannot."""


@dataclass(frozen=True, slots=True)
class BuildContext[S: BaseModel]:
    """Everything a factory needs to construct one provider."""

    account: ProviderAccount
    targets: tuple[ComputeTarget, ...]
    """The account's enabled targets."""
    settings: S
    """``account.config`` validated against the registered settings model."""
    credential: str | None
    """Resolved secret, or ``None`` when the account has no ``credentials_ref``."""


ProviderFactory = Callable[[BuildContext[Any]], Provider]


@dataclass(frozen=True, slots=True)
class _Registration:
    settings_model: type[BaseModel]
    factory: ProviderFactory


class ProviderRegistry:
    def __init__(self) -> None:
        self._registrations: dict[ProviderKind, _Registration] = {}

    def register[S: BaseModel](
        self,
        kind: ProviderKind,
        settings_model: type[S],
        factory: Callable[[BuildContext[S]], Provider],
    ) -> None:
        if kind in self._registrations:
            raise ConfigurationError(f"provider kind {kind.value!r} is already registered")
        self._registrations[kind] = _Registration(settings_model, factory)

    @property
    def kinds(self) -> frozenset[ProviderKind]:
        return frozenset(self._registrations)

    def build(
        self,
        account: ProviderAccount,
        targets: Sequence[ComputeTarget] = (),
        credentials: CredentialResolver | None = None,
    ) -> Provider:
        """Construct the provider for ``account``.

        Raises ``ConfigurationError`` for an unregistered kind, a disabled account, targets
        of another account, invalid ``config``, or a ``credentials_ref`` with no resolver.
        """
        registration = self._registrations.get(account.kind)
        if registration is None:
            raise ConfigurationError(
                f"no adapter registered for provider kind {account.kind.value!r}"
            )
        if not account.enabled:
            raise ConfigurationError(f"provider account {account.name!r} is disabled")
        foreign = [t.name for t in targets if t.provider_account_id != account.id]
        if foreign:
            raise ConfigurationError(
                f"targets {foreign} do not belong to provider account {account.name!r}"
            )
        try:
            settings = registration.settings_model.model_validate(account.config)
        except ValidationError as exc:
            raise ConfigurationError(
                f"invalid config for provider account {account.name!r}: {exc}"
            ) from exc
        credential: str | None = None
        if account.credentials_ref is not None:
            if credentials is None:
                raise ConfigurationError(
                    f"provider account {account.name!r} needs credential "
                    f"{account.credentials_ref!r} but no resolver was given"
                )
            credential = credentials(account.credentials_ref)
        context = BuildContext(
            account=account,
            targets=tuple(t for t in targets if t.enabled),
            settings=settings,
            credential=credential,
        )
        return registration.factory(context)

    def build_all(
        self,
        accounts: Sequence[ProviderAccount],
        targets: Sequence[ComputeTarget] = (),
        credentials: CredentialResolver | None = None,
    ) -> Mapping[str, Provider]:
        """Build every enabled account; keys are account names."""
        built: dict[str, Provider] = {}
        for account in accounts:
            if not account.enabled:
                continue
            mine = [t for t in targets if t.provider_account_id == account.id]
            built[account.name] = self.build(account, mine, credentials)
        return built


def capabilities_from_targets(
    kind: ProviderKind,
    targets: Sequence[ComputeTarget],
    *,
    pricing_model: PricingModel = PricingModel.FREE,
    **overrides: Any,
) -> Capabilities:
    """Derive declarative capabilities from configured targets.

    GPU types are the distinct ``gpu_type`` values (largest ``max_gpus`` wins);
    ``max_walltime`` is the longest across targets, ``preemptible`` true if any target is.
    ``overrides`` set the remaining ``Capabilities`` fields (``signals``, ``shared_filesystem``...).
    """
    enabled = [t for t in targets if t.enabled]
    gpu_types: dict[str, GpuType] = {}
    for t in enabled:
        if t.gpu_type is None or t.gpu_memory_gb is None:
            continue
        count = t.max_gpus or t.gpus_per_node or 1
        known = gpu_types.get(t.gpu_type)
        if known is None or count > known.max_count:
            gpu_types[t.gpu_type] = GpuType(
                name=t.gpu_type, memory_gb=t.gpu_memory_gb, max_count=count
            )
    walltimes = [t.max_walltime for t in enabled]
    fields: dict[str, Any] = {
        "kind": kind,
        "gpu_types": tuple(gpu_types.values()),
        "max_walltime": None
        if (not walltimes or None in walltimes)
        else max(w for w in walltimes if w),
        "targets": tuple(t.name for t in enabled),
        "preemptible": any(t.preemptible for t in enabled),
        "pricing_model": pricing_model,
    }
    fields.update(overrides)
    return Capabilities(**fields)


default_registry = ProviderRegistry()
"""Process-wide registry; adapter packages register themselves into it on import."""
