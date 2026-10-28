"""Errors every adapter raises for the same situations, so callers can handle them uniformly.

Each is an ``acm_core.errors.ACMError`` with a stable ``code``. Adapters raise
``acm_core.errors.ProviderError`` for any other provider rejection and
``ProviderUnavailableError`` when the provider is unreachable (retryable).
"""

from __future__ import annotations

from acm_core.errors import ConflictError, InvalidInputError, NotFoundError

__all__ = [
    "CapabilityMismatchError",
    "IdempotencyConflictError",
    "JobNotActiveError",
    "JobNotFoundError",
    "UnsupportedSignalError",
]


class JobNotFoundError(NotFoundError):
    """The provider does not know the job reference (never submitted, or another provider's)."""

    code = "provider_job_not_found"


class IdempotencyConflictError(ConflictError):
    """A job with this idempotency key was already submitted with a different spec."""

    code = "idempotency_conflict"


class JobNotActiveError(ConflictError):
    """The operation needs a running job (e.g. a signal), but the job is queued or finished."""

    code = "job_not_active"


class CapabilityMismatchError(InvalidInputError):
    """The job spec asks for resources or a target the provider does not offer."""

    code = "capability_mismatch"


class UnsupportedSignalError(InvalidInputError):
    """The provider cannot deliver this signal (see ``Capabilities.signals``)."""

    code = "unsupported_signal"
