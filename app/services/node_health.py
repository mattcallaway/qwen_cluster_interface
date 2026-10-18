"""Heartbeat-derived worker health.

Recorded ``health_status`` is only trustworthy while the worker keeps
authenticating. Previously nothing ever demoted a worker to offline when its
heartbeats stopped, so a dead worker stayed "online" indefinitely.

Rule (read-time derivation, no schema change, never promotes):
  * Only worker-daemon roles are subject to staleness.
  * A node recorded "online" whose last authenticated contact is older than
    ``HEARTBEAT_STALE_SECONDS`` is reported "offline" (``stale``).
  * A node with no contact timestamp is left as recorded (no evidence either way).
  * Recorded "offline"/"unknown" is never promoted to "online" by this module.
"""
from datetime import datetime, timezone
from typing import Optional

# Workers heartbeat every 15 s (QC_HEARTBEAT_INTERVAL). 4x interval tolerates
# ~3 missed beats plus Wi-Fi jitter before the node stops receiving work.
HEARTBEAT_INTERVAL_SECONDS = 15
HEARTBEAT_STALE_SECONDS = 60

WORKER_ROLES = frozenset({"vps_worker", "inference_node", "compute_worker", "general_worker"})


def seconds_since(ts: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - dt).total_seconds()


def effective_health(role: str, recorded: str, last_contact_at: Optional[str],
                     now: Optional[datetime] = None) -> str:
    """Return the health that should be shown/used for scheduling decisions."""
    if role not in WORKER_ROLES or recorded != "online":
        return recorded
    age = seconds_since(last_contact_at, now)
    if age is None:
        return recorded
    return "offline" if age > HEARTBEAT_STALE_SECONDS else "online"
