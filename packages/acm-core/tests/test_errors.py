import re

import pytest

from acm_core.errors import (
    ACMError,
    ConflictError,
    ErrorPayload,
    InvalidTransitionError,
    NotFoundError,
    ProviderUnavailableError,
    error_classes,
)


def test_codes_are_unique_snake_case_and_stable() -> None:
    classes = error_classes()
    assert all(re.fullmatch(r"[a-z]+(_[a-z]+)*", code) for code in classes)
    # Released codes never change meaning; this list only grows.
    assert {
        "internal_error",
        "invalid_input",
        "invalid_id",
        "unauthenticated",
        "permission_denied",
        "not_found",
        "conflict",
        "invalid_transition",
        "budget_exceeded",
        "policy_violation",
        "configuration_error",
        "external_service_error",
        "provider_error",
        "provider_unavailable",
    } <= set(classes)
    for code, cls in classes.items():
        assert cls.code == code
        assert 400 <= cls.http_status <= 599


def test_hierarchy() -> None:
    err = InvalidTransitionError("nope")
    assert isinstance(err, ConflictError)
    assert isinstance(err, ACMError)
    assert err.http_status == 409


def test_payload() -> None:
    err = NotFoundError("run not found", details={"run_id": "run_x"})
    assert str(err) == "run not found"
    assert err.to_payload() == ErrorPayload(
        code="not_found", message="run not found", retryable=False, details={"run_id": "run_x"}
    )
    assert ProviderUnavailableError("down").to_payload().retryable is True


def test_duplicate_code_rejected() -> None:
    with pytest.raises(TypeError, match="already used"):

        class _DupError(ACMError):
            code = "not_found"


def test_subclass_must_define_code() -> None:
    with pytest.raises(TypeError, match="must define"):

        class _NoCodeError(NotFoundError):
            pass
