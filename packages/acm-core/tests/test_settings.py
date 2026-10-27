from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from acm_core.settings import Environment, LogFormat, Settings, get_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import os

    for key in list(os.environ):
        if key.startswith("ACM_"):
            monkeypatch.delenv(key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_target_local_stack() -> None:
    settings = Settings()
    assert settings.env is Environment.DEV
    assert settings.log_format is LogFormat.JSON
    assert settings.temporal.address == "localhost:7233"
    assert settings.temporal.task_queue_control == "acm-control"
    assert settings.nats.url == "nats://localhost:4222"
    assert settings.clickhouse.port == 8123
    assert settings.storage.bucket == "acm"
    assert settings.db.url.get_secret_value().startswith("postgresql+asyncpg://")
    assert settings.models.reasoning_model is None


def test_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACM_ENV", "prod")
    monkeypatch.setenv("ACM_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ACM_LOG_FORMAT", "console")
    monkeypatch.setenv("ACM_DB__URL", "postgresql+asyncpg://u:p@db:5432/acm")
    monkeypatch.setenv("ACM_DB__POOL_SIZE", "25")
    monkeypatch.setenv("ACM_TEMPORAL__ADDRESS", "temporal:7233")
    monkeypatch.setenv("ACM_TEMPORAL__TLS", "true")
    monkeypatch.setenv("ACM_NATS__TOKEN", "s3cret")
    monkeypatch.setenv("ACM_NATS__CREDS_FILE", "/etc/acm/nats.creds")
    monkeypatch.setenv("ACM_CLICKHOUSE__HOST", "clickhouse")
    monkeypatch.setenv("ACM_STORAGE__ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("ACM_STORAGE__SECRET_ACCESS_KEY", "minio-secret")
    monkeypatch.setenv("ACM_MODELS__REASONING_MODEL", "muse-spark")
    monkeypatch.setenv("ACM_MODELS__META_API_KEY", "key")

    settings = get_settings()
    assert settings.env is Environment.PROD
    assert settings.log_level == "DEBUG"
    assert settings.log_format is LogFormat.CONSOLE
    assert settings.db.url.get_secret_value() == "postgresql+asyncpg://u:p@db:5432/acm"
    assert settings.db.pool_size == 25
    assert settings.temporal.address == "temporal:7233"
    assert settings.temporal.tls is True
    assert settings.nats.token is not None
    assert settings.nats.token.get_secret_value() == "s3cret"
    assert settings.nats.creds_file == Path("/etc/acm/nats.creds")
    assert settings.clickhouse.host == "clickhouse"
    assert settings.storage.endpoint_url == "http://minio:9000"
    assert settings.models.reasoning_model == "muse-spark"
    assert get_settings() is settings  # cached


def test_secrets_are_not_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACM_NATS__TOKEN", "s3cret")
    monkeypatch.setenv("ACM_DB__URL", "postgresql+asyncpg://u:hunter2@db/acm")
    rendered = repr(Settings()) + Settings().model_dump_json()
    assert "s3cret" not in rendered
    assert "hunter2" not in rendered


def test_invalid_values_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACM_ENV", "staging")
    with pytest.raises(ValidationError):
        Settings()


def test_invalid_nested_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACM_DB__POOL_SIZE", "0")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_are_immutable() -> None:
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.env = Environment.PROD  # type: ignore[misc]
