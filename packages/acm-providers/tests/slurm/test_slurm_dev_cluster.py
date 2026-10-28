"""Acceptance tests for the containerized Slurm cluster in `deploy/slurm-dev`."""

import asyncio
import shlex
from collections.abc import Awaitable, Callable
from typing import Any

import asyncssh
import httpx
import pytest

pytestmark = pytest.mark.slurm

API = "v0.0.40"


async def sh(ssh: asyncssh.SSHClientConnection, command: str) -> str:
    result = await ssh.run(command, check=True)
    return str(result.stdout).strip()


async def eventually[T](
    probe: Callable[[], Awaitable[T]],
    done: Callable[[T], bool],
    *,
    within: float = 60.0,
    interval: float = 1.0,
) -> T:
    last: T | None = None
    try:
        async with asyncio.timeout(within):
            while True:
                last = await probe()
                if done(last):
                    return last
                await asyncio.sleep(interval)
    except TimeoutError:
        raise AssertionError(f"condition not met within {within}s; last value: {last!r}") from None


async def sbatch(ssh: asyncssh.SSHClientConnection, args: str) -> int:
    return int(await sh(ssh, f"cd /scratch && sbatch --parsable {args}"))


async def sacct_state(ssh: asyncssh.SSHClientConnection, job_id: int) -> str:
    return await sh(ssh, f"sacct -j {job_id} -X -n -P -o State")


async def wait_completed(ssh: asyncssh.SSHClientConnection, job_id: int) -> None:
    state = await eventually(
        lambda: sacct_state(ssh, job_id),
        lambda s: s not in {"", "PENDING", "RUNNING", "COMPLETING"},
    )
    assert state == "COMPLETED"


async def test_sbatch_over_ssh_is_visible_in_squeue_and_sacct(
    slurm_ssh: asyncssh.SSHClientConnection,
) -> None:
    job_id = await sbatch(slurm_ssh, "-p debug -t 1 -J ssh-job --wrap 'sleep 5'")

    queued = await sh(slurm_ssh, f"squeue -j {job_id} -h -o '%i|%j|%u|%T'")
    _, name, user, state = queued.split("|")
    assert (name, user) == ("ssh-job", "acm")
    assert state in {"PENDING", "CONFIGURING", "RUNNING"}

    await wait_completed(slurm_ssh, job_id)
    accounted = await sh(slurm_ssh, f"sacct -j {job_id} -X -n -P -o JobName,User,Partition")
    assert accounted == "ssh-job|acm|debug"


async def test_sbatch_via_slurmrestd_is_visible_in_squeue_and_sacct(
    slurm_ssh: asyncssh.SSHClientConnection, slurmrestd: httpx.AsyncClient
) -> None:
    response = await slurmrestd.post(
        f"/slurm/{API}/job/submit",
        json={
            "script": "#!/bin/bash\nsleep 5\necho hello from $SLURM_JOB_ID\n",
            "job": {
                "name": "rest-job",
                "partition": "debug",
                "current_working_directory": "/scratch",
                "environment": ["PATH=/usr/bin:/bin"],
                "standard_output": "/scratch/rest-job-%j.out",
                "time_limit": {"set": True, "number": 1},
            },
        },
    )
    response.raise_for_status()
    body = response.json()
    assert body["errors"] == []
    job_id = int(body["job_id"])

    queued = await sh(slurm_ssh, f"squeue -j {job_id} -h -o '%j|%u'")
    assert queued == "rest-job|acm"

    job = (await slurmrestd.get(f"/slurm/{API}/job/{job_id}")).json()["jobs"][0]
    assert job["user_name"] == "acm"

    async def accounted_state() -> list[str]:
        response = await slurmrestd.get(f"/slurmdb/{API}/job/{job_id}")
        jobs: list[dict[str, Any]] = response.json().get("jobs", [])
        return jobs[0]["state"]["current"] if jobs else []

    assert await eventually(accounted_state, lambda s: s == ["COMPLETED"]) == ["COMPLETED"]
    assert await sh(slurm_ssh, f"cat /scratch/rest-job-{job_id}.out") == f"hello from {job_id}"


async def test_signal_usr1_is_delivered_before_walltime(
    slurm_ssh: asyncssh.SSHClientConnection,
) -> None:
    # The step traps USR1 and exits cleanly; without the signal it would hit its walltime.
    # Slurm may signal up to 60s earlier than requested, so a 2-minute walltime with USR1@30
    # must see the signal between 30s and 120s after the start.
    step = (
        'date +%s > "/scratch/sig-$SLURM_JOB_ID.start"; '
        "trap 'date +%s > \"/scratch/sig-$SLURM_JOB_ID.usr1\"; exit 0' USR1; "
        "while true; do sleep 1; done"
    )
    job_id = await sbatch(
        slurm_ssh,
        f"-p debug -t 2 --signal=USR1@30 --wrap {shlex.quote(f'srun bash -c {shlex.quote(step)}')}",
    )

    state = await eventually(
        lambda: sacct_state(slurm_ssh, job_id),
        lambda s: s not in {"", "PENDING", "RUNNING", "COMPLETING"},
        within=200,
        interval=2,
    )
    assert state == "COMPLETED", "job ran into its walltime instead of handling USR1"
    started = int(await sh(slurm_ssh, f"cat /scratch/sig-{job_id}.start"))
    signalled = int(await sh(slurm_ssh, f"cat /scratch/sig-{job_id}.usr1"))
    assert 29 <= signalled - started < 120


async def test_scontrol_requeue_restarts_the_job(slurm_ssh: asyncssh.SSHClientConnection) -> None:
    job_id = await sbatch(slurm_ssh, "-p debug -t 5 --requeue -J requeue-job --wrap 'sleep 300'")
    try:
        await eventually(
            lambda: sh(slurm_ssh, f"squeue -j {job_id} -h -o %T"), lambda s: s == "RUNNING"
        )

        await sh(slurm_ssh, f"scontrol requeue {job_id}")

        # The job passes through COMPLETING, then returns to the queue with its restart counted.
        await eventually(
            lambda: sh(slurm_ssh, f"scontrol show job {job_id} --oneliner"),
            lambda s: "Restarts=1" in s and ("JobState=PENDING" in s or "JobState=RUNNING" in s),
        )
        history = await sh(slurm_ssh, f"sacct -j {job_id} -X -D -n -P -o State")
        assert "REQUEUED" in history.splitlines()
    finally:
        await sh(slurm_ssh, f"scancel {job_id}")


@pytest.mark.parametrize(
    ("gres", "node", "visible_devices"),
    [("gpu:a100:2", "c1", "0,1"), ("gpu:h100:1", "c2", "0")],
)
async def test_gres_request_is_honored(
    slurm_ssh: asyncssh.SSHClientConnection, gres: str, node: str, visible_devices: str
) -> None:
    job_id = await sbatch(
        slurm_ssh,
        f"-p gpu -t 1 --gres={gres} -o /scratch/gres-%j.out "
        "--wrap 'hostname; echo $CUDA_VISIBLE_DEVICES'",
    )
    await wait_completed(slurm_ssh, job_id)

    assert (await sh(slurm_ssh, f"cat /scratch/gres-{job_id}.out")).split() == [
        node,
        visible_devices,
    ]
    _, gpu_type, count = gres.split(":")
    alloc_tres = await sh(slurm_ssh, f"sacct -j {job_id} -X -n -P -o AllocTRES")
    assert f"gres/gpu:{gpu_type}={count}" in alloc_tres.split(",")


async def test_unsatisfiable_gres_request_is_rejected(
    slurm_ssh: asyncssh.SSHClientConnection,
) -> None:
    result = await slurm_ssh.run("sbatch -p gpu -t 1 --gres=gpu:a100:3 --wrap true")
    assert result.exit_status != 0
    assert "Requested node configuration is not available" in str(result.stderr)
