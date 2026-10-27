"""Static checks on the dev stack definition (no Docker needed)."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "deploy" / "compose" / "compose.yaml"
ENV_EXAMPLE = ROOT / ".env.example"

# `${NAME}` or `${NAME:-default}`; `$${...}` is an escaped literal for the container shell.
INTERPOLATION = re.compile(r"(?<!\$)\$\{(?P<name>[A-Z0-9_]+)(?::-(?P<default>[^}]*))?\}")

# `<bind host>:<published port>:<container port>`, both host parts from the environment.
_VAR = r"\$\{[A-Z0-9_]+:-[^}]*\}"
PORT_MAPPING = re.compile(rf"(?P<host>{_VAR}):{_VAR}:\d+")

# One-shot jobs run explicitly by `just up`; they exit instead of staying healthy.
ONE_SHOT_PROFILES = {"bootstrap"}


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key] = value
    return values


def _services() -> dict[str, dict[str, Any]]:
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    return compose["services"]


def _long_running() -> dict[str, dict[str, Any]]:
    return {
        name: svc
        for name, svc in _services().items()
        if not ONE_SHOT_PROFILES.intersection(svc.get("profiles", []))
    }


def test_every_interpolated_variable_is_documented_with_the_same_default() -> None:
    documented = _env_example()
    matches = list(INTERPOLATION.finditer(COMPOSE_FILE.read_text()))
    assert matches
    for match in matches:
        name, default = match["name"], match["default"]
        assert name in documented, f"{name} is not documented in .env.example"
        assert default is not None, f"{name} has no default in compose.yaml"
        assert default == documented[name], f"{name}: compose default differs from .env.example"


@pytest.mark.parametrize("name", sorted(_long_running()))
def test_long_running_services_have_healthchecks(name: str) -> None:
    healthcheck = _long_running()[name].get("healthcheck")
    assert healthcheck, f"{name} has no healthcheck"
    assert healthcheck.get("test"), f"{name} healthcheck has no test"


@pytest.mark.parametrize("name", sorted(_services()))
def test_images_are_pinned(name: str) -> None:
    image: str = _services()[name]["image"]
    _, _, tag = image.rpartition(":")
    assert ":" in image, f"{name} image {image} has no tag"
    assert tag != "latest", f"{name} image {image} is not pinned"
    assert re.search(r"\d", tag), f"{name} image {image} tag has no version"


@pytest.mark.parametrize("name", sorted(_services()))
def test_published_ports_are_configurable_and_bound_to_bind_host(name: str) -> None:
    for port in _services()[name].get("ports", []):
        assert isinstance(port, str)
        match = PORT_MAPPING.fullmatch(port)
        assert match, f"{name}: {port} must be ${{ACM_BIND_HOST}}:${{ACM_*_PORT}}:<container>"
        assert match["host"] == "${ACM_BIND_HOST:-127.0.0.1}", f"{name}: {port} ignores bind host"


def test_scope_services_present() -> None:
    services = _services()
    expected_default = {"postgres", "temporal", "temporal-ui", "nats", "clickhouse", "minio"}
    assert expected_default <= services.keys()
    for name in expected_default:
        assert "profiles" not in services[name], f"{name} must start by default"
    for name in ("otel-collector", "grafana"):
        assert services[name]["profiles"] == ["observability"]
    assert services["minio-init"]["profiles"] == ["bootstrap"]


def test_temporal_uses_separate_postgres_databases() -> None:
    env = _services()["temporal"]["environment"]
    assert env["DB"] == "postgres12"
    assert env["DBNAME"] == "temporal"
    assert env["VISIBILITY_DBNAME"] == "temporal_visibility"
