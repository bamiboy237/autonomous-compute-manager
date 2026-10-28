#!/usr/bin/env bash
# Container healthcheck. Usage: healthcheck.sh <role>
set -euo pipefail

case "${1:?role required}" in
    slurmdbd)
        sacctmgr --noheader show cluster format=cluster >/dev/null
        ;;
    slurmctld)
        scontrol ping | grep -q 'is UP'
        ;;
    slurmd)
        scontrol show node "$(hostname)" --oneliner | grep -Eq 'State=(IDLE|MIXED|ALLOCATED)'
        ;;
    slurmrestd)
        # Any HTTP status proves the listener is up; unauthenticated calls get 401.
        curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:6820/openapi/v3 | grep -Eq '^[1-5][0-9][0-9]$'
        ;;
    login)
        # Ready for tests once sshd listens and both compute nodes accept work.
        nc -z 127.0.0.1 22
        [ "$(sinfo --noheader --nodes=c1,c2 --responding --states=idle,mixed,allocated -o %n | sort -u | wc -l)" -eq 2 ]
        ;;
esac
