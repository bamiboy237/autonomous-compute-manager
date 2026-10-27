"""Error hierarchy with stable machine-readable codes.

Every error raised deliberately by ACM code derives from ``ACMError``. Each class carries:

- ``code`` — a stable snake_case string. API responses, logs, workflow failures and the UI
  key off it, so a code never changes meaning once released. Codes are unique; defining a
  second class with an existing code fails at import time.
- ``http_status`` — the status the API maps the error to.
- ``retryable`` — whether retrying the same operation unchanged can succeed. Temporal
  activities use it to mark failures non-retryable.

Raise with a human-readable message and optional JSON-safe ``details``::

    raise NotFoundError("run not found", details={"run_id": run_id})

``error.to_payload()`` gives the ``ErrorPayload`` the API serializes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, JsonValue

__all__ = [
    "ACMError",
    "AuthenticationError",
    "BudgetExceededError",
    "ConfigurationError",
    "ConflictError",
    "ErrorPayload",
    "ExternalServiceError",
    "InternalError",
    "InvalidIdError",
    "InvalidInputError",
    "InvalidTransitionError",
    "NotFoundError",
    "PermissionDeniedError",
    "PolicyViolationError",
    "ProviderError",
    "ProviderUnavailableError",
    "error_classes",
]

_CODES: dict[str, type[ACMError]] = {}


class ErrorPayload(BaseModel):
    """Wire form of an ``ACMError`` (API error body, workflow failure details)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    retryable: bool
    details: dict[str, JsonValue]


class ACMError(Exception):
    """Root of all ACM errors. Not raised directly; raise a specific subclass."""

    code: ClassVar[str] = "error"
    http_status: ClassVar[int] = 500
    retryable: ClassVar[bool] = False

    message: str
    details: dict[str, JsonValue]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        code = cls.__dict__.get("code")
        if code is None:
            raise TypeError(f"{cls.__name__} must define its own `code`")
        if code in _CODES:
            raise TypeError(f"error code {code!r} is already used by {_CODES[code].__name__}")
        _CODES[code] = cls

    def __init__(self, message: str, *, details: Mapping[str, JsonValue] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})

    def to_payload(self) -> ErrorPayload:
        return ErrorPayload(
            code=self.code, message=self.message, retryable=self.retryable, details=self.details
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


def error_classes() -> dict[str, type[ACMError]]:
    """All registered error classes keyed by code (for API docs and contract tests)."""
    return dict(_CODES)


class InternalError(ACMError):
    """A bug or broken invariant inside ACM."""

    code = "internal_error"
    http_status = 500


class InvalidInputError(ACMError):
    """The caller supplied a malformed or semantically invalid value."""

    code = "invalid_input"
    http_status = 400


class InvalidIdError(InvalidInputError):
    """A string is not a valid prefixed ULID of the expected type."""

    code = "invalid_id"


class AuthenticationError(ACMError):
    """The caller is not authenticated."""

    code = "unauthenticated"
    http_status = 401


class PermissionDeniedError(ACMError):
    """The authenticated caller's role does not allow the operation."""

    code = "permission_denied"
    http_status = 403


class NotFoundError(ACMError):
    """The referenced entity does not exist."""

    code = "not_found"
    http_status = 404


class ConflictError(ACMError):
    """The operation conflicts with the entity's current state."""

    code = "conflict"
    http_status = 409


class InvalidTransitionError(ConflictError):
    """A status change is not allowed by the entity's state machine."""

    code = "invalid_transition"


class BudgetExceededError(ConflictError):
    """The operation would exceed a hard budget limit."""

    code = "budget_exceeded"


class PolicyViolationError(ACMError):
    """The campaign's autonomy policy does not allow the action without approval."""

    code = "policy_violation"
    http_status = 403


class ConfigurationError(ACMError):
    """Settings or provider configuration are missing or invalid."""

    code = "configuration_error"
    http_status = 500


class ExternalServiceError(ACMError):
    """A dependency (database, NATS, object store, model API) failed or timed out."""

    code = "external_service_error"
    http_status = 503
    retryable = True


class ProviderError(ACMError):
    """A compute provider rejected or failed an operation."""

    code = "provider_error"
    http_status = 502


class ProviderUnavailableError(ProviderError):
    """A compute provider is temporarily unreachable; retrying later may succeed."""

    code = "provider_unavailable"
    http_status = 503
    retryable = True
