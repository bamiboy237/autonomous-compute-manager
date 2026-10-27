"""Process configuration loaded from environment variables.

All variables use the ``ACM_`` prefix. Nested sections use a double underscore, so
``Settings().db.url`` comes from ``ACM_DB__URL``. Defaults target the local Docker Compose
stack; production overrides them. Every variable is documented in ``.env.example``.

Secrets are ``SecretStr``: they never appear in ``repr`` or logs; read them with
``.get_secret_value()`` at the point of use.

Use ``get_settings()`` in application code (cached per process) and construct
``Settings(...)`` directly in tests.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "ClickHouseSettings",
    "DatabaseSettings",
    "Environment",
    "LogFormat",
    "LogLevel",
    "ModelsSettings",
    "NatsSettings",
    "Settings",
    "StorageSettings",
    "TemporalSettings",
    "get_settings",
]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Environment(StrEnum):
    """Deployment environment (``ACM_ENV``)."""

    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class LogFormat(StrEnum):
    """Log renderer (``ACM_LOG_FORMAT``)."""

    JSON = "json"
    CONSOLE = "console"


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DatabaseSettings(_Section):
    """Postgres (system of record). Env: ``ACM_DB__*``."""

    url: SecretStr = SecretStr("postgresql+asyncpg://acm:acm@localhost:5432/acm")
    """SQLAlchemy async URL (``postgresql+asyncpg://user:pass@host:port/db``)."""
    pool_size: int = Field(default=10, ge=1)
    echo: bool = False
    """Log every SQL statement (debugging only)."""


class TemporalSettings(_Section):
    """Temporal server and task queues. Env: ``ACM_TEMPORAL__*``."""

    address: str = "localhost:7233"
    namespace: str = "default"
    tls: bool = False
    api_key: SecretStr | None = None
    task_queue_control: str = "acm-control"
    """Workflows and cheap activities."""
    task_queue_agents: str = "acm-agents"
    """LLM activities."""
    task_queue_io: str = "acm-io"
    """Provider and storage activities."""


class NatsSettings(_Section):
    """NATS JetStream event bus. Env: ``ACM_NATS__*``."""

    url: str = "nats://localhost:4222"
    """Comma-separated server URLs are allowed."""
    token: SecretStr | None = None
    creds_file: Path | None = None
    """NATS ``.creds`` file (JWT + nkey) for decentralized auth."""


class ClickHouseSettings(_Section):
    """ClickHouse (metrics, logs, events). Env: ``ACM_CLICKHOUSE__*``."""

    host: str = "localhost"
    port: int = Field(default=8123, ge=1, le=65535)
    """HTTP interface port."""
    database: str = "acm"
    username: str = "acm"
    password: SecretStr = SecretStr("")
    secure: bool = False


class StorageSettings(_Section):
    """S3-compatible object store. Env: ``ACM_STORAGE__*``."""

    endpoint_url: str | None = "http://localhost:9000"
    """``None`` uses the provider's default endpoint (AWS S3)."""
    bucket: str = "acm"
    region: str = "us-east-1"
    access_key_id: str | None = None
    secret_access_key: SecretStr | None = None


class ModelsSettings(_Section):
    """Model endpoints used by ``acm_agents.models``. Env: ``ACM_MODELS__*``.

    Reasoning and code roles use the Meta Model API (Muse); triage uses a Llama endpoint
    (Baseten or Modal) that speaks the OpenAI-compatible chat API. Model names are left
    unset here so the agents package owns their defaults.
    """

    meta_api_base_url: str | None = None
    meta_api_key: SecretStr | None = None
    reasoning_model: str | None = None
    """Planner, Diagnostician and Critic."""
    code_model: str | None = None
    """Engineer."""
    triage_base_url: str | None = None
    triage_api_key: SecretStr | None = None
    triage_model: str | None = None


class Settings(BaseSettings):
    """Root settings object. See the module docstring for the env var naming scheme."""

    model_config = SettingsConfigDict(
        env_prefix="ACM_",
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    env: Environment = Environment.DEV
    log_level: LogLevel = "INFO"
    log_format: LogFormat = LogFormat.JSON

    db: DatabaseSettings = DatabaseSettings()
    temporal: TemporalSettings = TemporalSettings()
    nats: NatsSettings = NatsSettings()
    clickhouse: ClickHouseSettings = ClickHouseSettings()
    storage: StorageSettings = StorageSettings()
    models: ModelsSettings = ModelsSettings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings from the environment once per process.

    Call ``get_settings.cache_clear()`` after changing the environment (tests only).
    """
    return Settings()
