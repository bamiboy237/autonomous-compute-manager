# One image for every Slurm role (slurmctld, slurmd, slurmdbd, slurmrestd, login).
# The role is the container command; see scripts/entrypoint.sh.
#
# Slurm comes from Ubuntu noble's release pocket, which never changes, so the
# pinned version below stays installable for the lifetime of the release.
FROM ubuntu:24.04@sha256:24737cef17f4e22b1977df7d9a05e77c4e28b7da5b8911f8d8ccda136bf3573c

ARG SLURM_VERSION=23.11.4-1.2ubuntu5

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        "slurmctld=${SLURM_VERSION}" \
        "slurmd=${SLURM_VERSION}" \
        "slurmdbd=${SLURM_VERSION}" \
        "slurmrestd=${SLURM_VERSION}" \
        "slurm-client=${SLURM_VERSION}" \
        "slurm-wlm-basic-plugins=${SLURM_VERSION}" \
        "slurm-wlm-jwt-plugin=${SLURM_VERSION}" \
        "slurm-wlm-mysql-plugin=${SLURM_VERSION}" \
        munge \
        openssh-server \
        ca-certificates \
        curl \
        jq \
        netcat-openbsd \
        python3 \
    && rm -rf /var/lib/apt/lists/*

# Fixed ids so every container (and the shared /scratch volume) agrees on ownership.
RUN groupadd --gid 2000 acm \
    && useradd --uid 2000 --gid acm --create-home --shell /bin/bash acm \
    && useradd --system --no-create-home --shell /usr/sbin/nologin slurmrest \
    && mkdir -p /etc/slurm /var/spool/slurmctld /var/spool/slurmd /var/log/slurm /run/munge \
        /run/sshd /scratch \
    && chown slurm:slurm /var/spool/slurmctld /var/spool/slurmd /var/log/slurm \
    && chown munge:munge /run/munge

COPY etc/ /etc/slurm-dev/
COPY scripts/ /usr/local/bin/

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
