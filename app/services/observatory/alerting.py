"""
System Observatory Alerting Engine.
Evaluates configured rules against incoming telemetry with hysteresis,
deduplication, cooldown periods, and state transitions (firing, acknowledged, resolved).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_db_connection
from app.services.observatory import storage

logger = logging.getLogger("qwen_collective.observatory.alerting")

# Track sustained condition start times in memory for hysteresis evaluation
# key: (rule_name, node_id) -> start_timestamp
_HYSTERESIS_TRACKER: Dict[Tuple[str, Optional[str]], float] = {}


def evaluate_telemetry_for_alerts(node_id: str, sample: Dict[str, Any]) -> List[str]:
    """Evaluate active alert rules against a fresh telemetry sample.
    Fires new alerts if conditions are sustained past duration_seconds.
    Resolves alerts if conditions return to safe ranges.
    Returns list of newly fired alert IDs.
    """
    rules = storage.get_all_alert_rules()
    now_dt = datetime.now(timezone.utc)
    now_epoch = now_dt.timestamp()
    fired_alert_ids = []

    for rule in rules:
        if not rule.get("is_enabled", 1):
            continue

        rule_name = rule["name"]
        metric_name = rule["metric_name"]
        threshold = float(rule["threshold"])
        op = rule["operator"]
        duration_s = int(rule.get("duration_seconds") or 0)
        severity = rule.get("severity", "warning")
        category = rule.get("category", "system")

        val = sample.get(metric_name)
        if val is None:
            continue

        try:
            val = float(val)
        except (ValueError, TypeError):
            continue

        # Check operator
        condition_met = False
        if op == ">=":
            condition_met = (val >= threshold)
        elif op == ">":
            condition_met = (val > threshold)
        elif op == "<=":
            condition_met = (val <= threshold)
        elif op == "<":
            condition_met = (val < threshold)
        elif op == "==":
            condition_met = (val == threshold)
        elif op == "!=":
            condition_met = (val != threshold)

        tracker_key = (rule_name, node_id)

        if condition_met:
            # Sustained duration check (hysteresis)
            if tracker_key not in _HYSTERESIS_TRACKER:
                _HYSTERESIS_TRACKER[tracker_key] = now_epoch

            elapsed_s = now_epoch - _HYSTERESIS_TRACKER[tracker_key]
            if elapsed_s >= duration_s:
                # Trigger or maintain alert
                title = f"{rule_name.replace('_', ' ').title()} on {node_id}"
                desc = (
                    f"Metric '{metric_name}' is {val} (threshold: {op} {threshold}) "
                    f"sustained for {int(elapsed_s)}s on node '{node_id}'."
                )
                aid = storage.create_alert(
                    rule_name=rule_name,
                    category=category,
                    node_id=node_id,
                    severity=severity,
                    title=title,
                    description=desc,
                    metrics={metric_name: val, "threshold": threshold, "duration_s": elapsed_s},
                    cooldown_seconds=300,
                )
                if aid:
                    fired_alert_ids.append(aid)
        else:
            # Condition cleared: reset hysteresis and resolve any firing alert
            if tracker_key in _HYSTERESIS_TRACKER:
                del _HYSTERESIS_TRACKER[tracker_key]
            storage.resolve_alert(rule_name, node_id)

    return fired_alert_ids


def evaluate_cluster_heartbeats() -> List[str]:
    """Check for missed heartbeats or silent workers across all registered nodes."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, last_contact_at FROM infrastructure_nodes WHERE is_enabled = 1")
    nodes = [dict(r) for r in cursor.fetchall()]
    conn.close()

    now_dt = datetime.now(timezone.utc)
    fired = []

    for n in nodes:
        nid = n["id"]
        # Skip legacy nodes
        if nid in ("node-d35568f5",):
            continue

        last_str = n.get("last_contact_at")
        if not last_str:
            continue

        try:
            if last_str.endswith("Z"):
                last_str = last_str[:-1] + "+00:00"
            last_dt = datetime.fromisoformat(last_str)
            age_s = (now_dt - last_dt).total_seconds()
            if age_s >= 45.0:
                aid = storage.create_alert(
                    rule_name="node_heartbeat_timeout",
                    category="worker",
                    node_id=nid,
                    severity="critical",
                    title=f"Worker Heartbeat Timeout on {nid}",
                    description=f"Node '{nid}' ({n.get('name')}) has not reported contact for {int(age_s)} seconds (threshold: >= 45s).",
                    metrics={"heartbeat_age_s": age_s},
                    cooldown_seconds=180,
                )
                if aid:
                    fired.append(aid)
            else:
                storage.resolve_alert("node_heartbeat_timeout", nid)
        except Exception:
            pass

    return fired
