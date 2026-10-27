# acm-edge

Outbound-only site daemon for Slurm login nodes and GPU VMs. It dials out to NATS, runs an allowlisted command set, and tails workload telemetry. Design: [docs/ARCHITECTURE.md §6](../docs/ARCHITECTURE.md).

```sh
cd edge && go build ./... && go test ./...
go run ./cmd/acm-edge -version
```
