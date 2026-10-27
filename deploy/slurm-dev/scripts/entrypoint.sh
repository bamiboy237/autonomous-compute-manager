#!/usr/bin/env bash
# Starts one Slurm dev-cluster role. Usage: entrypoint.sh <role>
set -euo pipefail

role="${1:?role required: init|slurmdbd|slurmctld|slurmd|slurmrestd|login}"
secrets=/slurm-secrets

log() { echo "[$role] $*" >&2; }

wait_for_port() {
    local host=$1 port=$2
    until nc -z "$host" "$port" 2>/dev/null; do
        log "waiting for $host:$port"
        sleep 1
    done
}

install_config() {
    cp /etc/slurm-dev/slurm.conf /etc/slurm-dev/gres.conf /etc/slurm/
    install -o slurm -g slurm -m 0600 /etc/slurm-dev/slurmdbd.conf /etc/slurm/slurmdbd.conf
    install -o slurm -g slurm -m 0600 "$secrets/jwt_hs256.key" /etc/slurm/jwt_hs256.key
}

start_munge() {
    install -o munge -g munge -m 0400 "$secrets/munge.key" /etc/munge/munge.key
    runuser -u munge -- /usr/sbin/munged
}

prepare_scratch() {
    chown acm:acm /scratch
    chmod 1777 /scratch
}

# Idempotent: re-running against an existing database leaves it unchanged.
bootstrap_accounting() {
    local sacctmgr=(sacctmgr --immediate --quiet)
    if ! sacctmgr --noheader --parsable2 show cluster format=cluster | grep -qx acmdev; then
        "${sacctmgr[@]}" add cluster acmdev
    fi
    if ! sacctmgr --noheader --parsable2 show qos format=name | grep -qx debug; then
        "${sacctmgr[@]}" add qos debug \
            MaxJobsPerUser=4 MaxSubmitJobsPerUser=10 MaxWall=00:10:00 \
            MaxTRESPerUser=cpu=4
    fi
    if ! sacctmgr --noheader --parsable2 show account format=account | grep -qx acm; then
        "${sacctmgr[@]}" add account acm Cluster=acmdev Description="ACM dev" Organization=acm
    fi
    if ! sacctmgr --noheader --parsable2 show user format=user | grep -qx acm; then
        "${sacctmgr[@]}" add user acm Account=acm DefaultAccount=acm Cluster=acmdev
    fi
}

# slurmctld and slurmdbd start as root and drop to SlurmUser themselves.
case "$role" in
    init)
        # Generates per-checkout keys once; they live in the slurm-secrets volume.
        umask 077
        [ -s "$secrets/munge.key" ] || dd if=/dev/urandom of="$secrets/munge.key" bs=1024 count=1 status=none
        [ -s "$secrets/jwt_hs256.key" ] || dd if=/dev/urandom of="$secrets/jwt_hs256.key" bs=32 count=1 status=none
        chmod 0444 "$secrets"/*
        log "keys ready"
        ;;
    slurmdbd)
        install_config
        start_munge
        wait_for_port mariadb 3306
        exec /usr/sbin/slurmdbd -D -s
        ;;
    slurmctld)
        install_config
        start_munge
        wait_for_port slurmdbd 6819
        bootstrap_accounting
        exec /usr/sbin/slurmctld -D -s -i
        ;;
    slurmd)
        install_config
        start_munge
        prepare_scratch
        # Fake GPU device nodes (NVIDIA major 195) for gres.conf File= entries.
        for i in 0 1; do
            [ -e "/dev/nvidia$i" ] || mknod -m 0666 "/dev/nvidia$i" c 195 "$i"
        done
        wait_for_port slurmctld 6817
        exec /usr/sbin/slurmd -D -s
        ;;
    slurmrestd)
        install_config
        start_munge
        wait_for_port slurmctld 6817
        export SLURM_JWT=daemon
        # Unprivileged containers cannot create the namespaces slurmrestd isolates itself with.
        export SLURMRESTD_SECURITY=disable_unshare_sysv,disable_unshare_files
        exec runuser -u slurmrest -- /usr/sbin/slurmrestd -a rest_auth/jwt \
            -s openapi/slurmctld,openapi/slurmdbd 0.0.0.0:6820
        ;;
    login)
        install_config
        rm -f /etc/slurm/slurmdbd.conf /etc/slurm/jwt_hs256.key
        start_munge
        prepare_scratch
        install -d -o acm -g acm -m 0700 /home/acm/.ssh
        install -o acm -g acm -m 0600 /ssh/authorized_keys /home/acm/.ssh/authorized_keys
        ssh-keygen -A
        exec /usr/sbin/sshd -D -e
        ;;
    *)
        log "unknown role"
        exit 64
        ;;
esac
