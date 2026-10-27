"""JetStream stream names. Stream configuration lives in ``acm_telemetry.bus``."""

from __future__ import annotations

__all__ = ["ATTEMPTS_STREAM", "DECISIONS_STREAM", "EDGE_STREAM", "RUNS_STREAM"]

RUNS_STREAM = "ACM_RUNS"
"""``acm.run.>``: metrics, heartbeats, logs, checkpoints."""
ATTEMPTS_STREAM = "ACM_ATTEMPTS"
"""``acm.attempt.>``: attempt state changes."""
EDGE_STREAM = "ACM_EDGE"
"""``acm.edge.>``: edge command RPC."""
DECISIONS_STREAM = "ACM_DECISIONS"
"""``acm.decision.>``: decision lifecycle."""
