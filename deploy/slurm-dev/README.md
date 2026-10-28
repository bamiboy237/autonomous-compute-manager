# Slurm dev cluster

A small, real Slurm cluster in Docker Compose for developing and testing the Slurm provider
adapter and `acm-edge` without access to a site cluster.

## Quick start

```sh
just up-slurm      # build images, start the cluster, wait until both nodes accept jobs
just test-slurm    # run tests marked `slurm`
just down-slurm -v # stop and drop all state (accounting DB, keys, /scratch)
```

`just up-slurm` creates an SSH key pair for the test user in `deploy/slurm-dev/.ssh/`
(git-ignored) the first time it runs. Munge and JWT keys are generated per checkout into the
`slurm-secrets` volume; nothing secret is committed.

## What runs

| Service | Hostname | Role |
|---|---|---|
| `init` | — | one-shot: generates the munge key and JWT signing key |
| `mariadb` | `mariadb` | accounting database (MariaDB 11.4) |
| `slurmdbd` | `slurmdbd` | accounting daemon, port 6819 |
| `slurmctld` | `slurmctld` | controller, port 6817; bootstraps cluster, account, user and QoS |
| `c1` | `c1` | `slurmd`, 2 CPUs, 1 GiB, fake `gpu:a100:2` |
| `c2` | `c2` | `slurmd`, 2 CPUs, 1 GiB, fake `gpu:h100:1` |
| `slurmrestd` | `slurmrestd` | REST API with JWT auth, port 6820 |
| `login` | `login` | `sshd` and the Slurm client commands, for the `acm` user |

All Slurm services use one image (`acm/slurm-dev:23.11.4`): Ubuntu 24.04 pinned by digest with
Slurm `23.11.4-1.2ubuntu5` pinned from the noble release pocket. The container command picks
the role (`scripts/entrypoint.sh`). Configuration lives in `etc/`.

All services sit behind the Compose profile `slurm`, so this file can be included into the main
stack and only starts with `--profile slurm`.

## Connecting

| What | From the host | Inside the cluster network |
|---|---|---|
| SSH (login node) | `ssh -p 2222 -i deploy/slurm-dev/.ssh/id_ed25519 acm@127.0.0.1` | `login:22` |
| slurmrestd | `http://127.0.0.1:6820` | `http://slurmrestd:6820` |

Host ports bind to `127.0.0.1` and can be changed with `SLURM_DEV_SSH_PORT` and
`SLURM_DEV_RESTD_PORT` (see `.env.example`). Host keys are regenerated on every start, so skip
host-key checking (`known_hosts=None` in asyncssh, `-o StrictHostKeyChecking=no` for OpenSSH).

### slurmrestd

slurmrestd accepts JWTs only. Mint one as the job's user on the login node, then send it with
the user name:

```sh
TOKEN=$(ssh -p 2222 -i deploy/slurm-dev/.ssh/id_ed25519 acm@127.0.0.1 scontrol token lifespan=3600 | cut -d= -f2)
curl -H "X-SLURM-USER-NAME: acm" -H "X-SLURM-USER-TOKEN: $TOKEN" \
  http://127.0.0.1:6820/slurm/v0.0.40/ping
```

The only OpenAPI version served is `v0.0.40`, for both `/slurm/` (controller) and `/slurmdb/`
(accounting). `POST /slurm/v0.0.40/job/submit` takes the script at the top level: `{"script": "#!/bin/bash\n...", "job": {"partition": "debug", ...}}`. The job
`environment` must be set explicitly (at least `PATH`).

## Cluster layout

- **User and account:** `acm` (uid/gid 2000) in account `acm`. Home is `/home/acm` on the
  login node only; use `/scratch` for anything a job reads or writes.
- **Shared filesystem:** the `scratch` volume is mounted at `/scratch` on `login`, `c1` and
  `c2` (mode 1777).
- **Partitions:**
  - `debug` (default): `MaxTime=00:10:00`, `DefaultTime=00:05:00`, partition QoS `debug`.
  - `gpu`: `MaxTime=04:00:00`, `DefaultTime=01:00:00`, QoS `normal`.
- **QoS `debug`:** `MaxJobsPerUser=4`, `MaxSubmitJobsPerUser=10`, `MaxWall=00:10:00`,
  `MaxTRESPerUser=cpu=4`.
- **GPUs:** `AutoDetect=off` with fake NVIDIA device nodes (`/dev/nvidia0`, `/dev/nvidia1`).
  Slurm schedules and accounts them like real GPUs and sets `CUDA_VISIBLE_DEVICES`; nothing can
  run CUDA. Accounting tracks `gres/gpu`, `gres/gpu:a100` and `gres/gpu:h100`.
- **No cgroups:** `proctrack/linuxproc` and `task/none`, so memory and device limits are not
  enforced.

## Behaviour to know about

- `--signal=<sig>@<secs>` may arrive up to 60 seconds earlier than requested (documented Slurm
  behaviour). With a 1-minute walltime the signal is sent almost immediately; tests use
  `-t 2 --signal=USR1@30`. Without `B:`, the signal goes to job steps (`srun`), not the batch
  shell.
- After `scontrol requeue` the job goes through `COMPLETING`, then returns to `PENDING` with
  `Restarts` incremented; `sacct -D` shows a `REQUEUED` record.
- An unsatisfiable GRES request (for example `--gres=gpu:a100:3`) is rejected at submit time
  with `Requested node configuration is not available`.

## Tests

Tests marked `slurm` live in `packages/acm-providers/tests/slurm/`. Its `conftest.py` provides
two fixtures that adapter tests in that directory can reuse:

- `slurm_ssh`: an `asyncssh.SSHClientConnection` to the login node as `acm`.
- `slurmrestd`: an `httpx.AsyncClient` with the base URL and JWT headers already set.

CI runs them in the `slurm` job (not a required check yet).

## Troubleshooting

- `docker compose -f deploy/slurm-dev/compose.yaml --profile slurm logs <service>` shows daemon
  logs; full logs are in `/var/log/slurm/` inside each container.
- A node stuck in `DOWN` or `DRAIN`: `docker compose -f deploy/slurm-dev/compose.yaml --profile slurm
  exec slurmctld scontrol update nodename=c1 state=resume`.
- To reset everything, including the generated keys and accounting history: `just down-slurm -v`.
