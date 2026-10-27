"""Fixtures for tests against the containerized Slurm cluster (`just up-slurm`).

Ports come from the same variables `deploy/slurm-dev/compose.yaml` reads: SLURM_DEV_SSH_PORT
(default 2222) and SLURM_DEV_RESTD_PORT (default 6820). `just up-slurm` creates the SSH key.
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import asyncssh
import httpx
import pytest

SSH_KEY = Path(__file__).resolve().parents[4] / "deploy/slurm-dev/.ssh/id_ed25519"
SLURM_USER = "acm"


@pytest.fixture
async def slurm_ssh() -> AsyncIterator[asyncssh.SSHClientConnection]:
    """SSH session to the login node as the `acm` user."""
    async with asyncssh.connect(
        "127.0.0.1",
        port=int(os.environ.get("SLURM_DEV_SSH_PORT", "2222")),
        username=SLURM_USER,
        client_keys=[str(SSH_KEY)],
        known_hosts=None,  # host keys are regenerated on every container start
    ) as conn:
        yield conn


@pytest.fixture
async def slurmrestd(slurm_ssh: asyncssh.SSHClientConnection) -> AsyncIterator[httpx.AsyncClient]:
    """slurmrestd client authenticated as `acm` with a JWT minted on the login node."""
    result = await slurm_ssh.run("scontrol token lifespan=3600", check=True)
    token = str(result.stdout).strip().removeprefix("SLURM_JWT=")
    port = os.environ.get("SLURM_DEV_RESTD_PORT", "6820")
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}",
        headers={"X-SLURM-USER-NAME": SLURM_USER, "X-SLURM-USER-TOKEN": token},
        timeout=10.0,
    ) as client:
        yield client
