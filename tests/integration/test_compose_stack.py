"""Smoke test: every service in the dev stack accepts connections (`just up` first).

Connection settings come from the environment (`just` loads `.env`), falling back to the
defaults in `.env.example`, which the Compose file mirrors.
"""

import os
import uuid
from pathlib import Path

import aioboto3
import asyncpg
import clickhouse_connect
import nats
import pytest
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.client import Client

pytestmark = pytest.mark.integration

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def _setting(name: str) -> str:
    if name in os.environ:
        return os.environ[name]
    for line in ENV_EXAMPLE.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key == name:
            return value
    raise KeyError(f"{name} is not set and not documented in .env.example")


def _host() -> str:
    bind = _setting("ACM_BIND_HOST")
    return "127.0.0.1" if bind in ("0.0.0.0", "") else bind


async def test_postgres_with_pgvector() -> None:
    conn = await asyncpg.connect(
        host=_host(),
        port=int(_setting("ACM_POSTGRES_PORT")),
        user=_setting("ACM_POSTGRES_USER"),
        password=_setting("ACM_POSTGRES_PASSWORD"),
        database=_setting("ACM_POSTGRES_DB"),
    )
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        distance = await conn.fetchval("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector")
        assert distance == pytest.approx(1.0)
        databases = {row["datname"] for row in await conn.fetch("SELECT datname FROM pg_database")}
        assert {"temporal", "temporal_visibility"} <= databases
    finally:
        await conn.close()


async def test_temporal_namespace() -> None:
    namespace = _setting("ACM_TEMPORAL_NAMESPACE")
    client = await Client.connect(f"{_host()}:{_setting('ACM_TEMPORAL_PORT')}", namespace=namespace)
    response = await client.workflow_service.describe_namespace(
        DescribeNamespaceRequest(namespace=namespace)
    )
    assert response.namespace_info.name == namespace


async def test_nats_jetstream() -> None:
    nc = await nats.connect(
        f"nats://{_host()}:{_setting('ACM_NATS_PORT')}",
        token=_setting("ACM_NATS_TOKEN"),
        connect_timeout=5,
        max_reconnect_attempts=0,
    )
    try:
        info = await nc.jetstream().account_info()
        assert info.limits is not None
    finally:
        await nc.close()


async def test_clickhouse() -> None:
    client = await clickhouse_connect.get_async_client(
        host=_host(),
        port=int(_setting("ACM_CLICKHOUSE_HTTP_PORT")),
        username=_setting("ACM_CLICKHOUSE_USER"),
        password=_setting("ACM_CLICKHOUSE_PASSWORD"),
        database=_setting("ACM_CLICKHOUSE_DB"),
    )
    try:
        result = await client.query("SELECT 1")
        assert result.result_rows == [(1,)]
    finally:
        await client.close()


async def test_minio_bucket() -> None:
    bucket = _setting("ACM_S3_BUCKET")
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=f"http://{_host()}:{_setting('ACM_MINIO_PORT')}",
        aws_access_key_id=_setting("ACM_S3_ACCESS_KEY"),
        aws_secret_access_key=_setting("ACM_S3_SECRET_KEY"),
        region_name="us-east-1",
    ) as s3:
        listing = await s3.list_buckets()
        assert bucket in {b.get("Name") for b in listing.get("Buckets", [])}

        key = f"smoke/{uuid.uuid4()}"
        await s3.put_object(Bucket=bucket, Key=key, Body=b"ok")
        try:
            obj = await s3.get_object(Bucket=bucket, Key=key)
            assert await obj["Body"].read() == b"ok"
        finally:
            await s3.delete_object(Bucket=bucket, Key=key)
