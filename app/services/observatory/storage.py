"""
System Observatory Storage Layer.
Handles time-series sample persistence, rollups, inference accounting, alert tracking,
and configurable retention pruning in SQLite.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_db_connection

logger = logging.getLogger("qwen_collective.observatory.storage")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_telemetry_sample(node_id: str, sample: Dict[str, Any]) -> int:
    """Insert a raw telemetry sample for a registered physical node."""
    ts = sample.get("timestamp") or _now_iso()
    per_core_json = json.dumps(sample.get("per_core") or [])
    gpu_data = sample.get("gpu") or {}
    if hasattr(gpu_data, "model_dump"):
        gpu_data = gpu_data.model_dump()
    gpu_info_json = json.dumps(gpu_data)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO telemetry_samples (
                node_id, timestamp, cpu_percent, per_core_json, cpu_freq_mhz,
                load_1m, load_5m, load_15m, ram_total_mb, ram_used_mb,
                ram_available_mb, ram_percent, swap_total_mb, swap_used_mb, swap_percent,
                disk_total_gb, disk_used_gb, disk_free_gb, disk_read_mbs, disk_write_mbs,
                disk_iops, net_rx_kbs, net_tx_kbs, cpu_temp_c, battery_percent,
                is_ac_online, worker_res_mb, ollama_res_mb, worker_cpu_percent, ollama_cpu_percent,
                gpu_info_json, latency_to_controller_ms, os_uptime_s, worker_service_status, ollama_service_status
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )
            """,
            (
                node_id,
                ts,
                sample.get("cpu_percent"),
                per_core_json,
                sample.get("cpu_freq_mhz"),
                sample.get("load_1m"),
                sample.get("load_5m"),
                sample.get("load_15m"),
                sample.get("ram_total_mb"),
                sample.get("ram_used_mb"),
                sample.get("ram_available_mb"),
                sample.get("ram_percent"),
                sample.get("swap_total_mb"),
                sample.get("swap_used_mb"),
                sample.get("swap_percent"),
                sample.get("disk_total_gb"),
                sample.get("disk_used_gb"),
                sample.get("disk_free_gb"),
                sample.get("disk_read_mbs"),
                sample.get("disk_write_mbs"),
                sample.get("disk_iops"),
                sample.get("net_rx_kbs"),
                sample.get("net_tx_kbs"),
                sample.get("cpu_temp_c"),
                sample.get("battery_percent"),
                sample.get("is_ac_online", -1),
                sample.get("worker_res_mb"),
                sample.get("ollama_res_mb"),
                sample.get("worker_cpu_percent"),
                sample.get("ollama_cpu_percent"),
                gpu_info_json,
                sample.get("latency_to_controller_ms"),
                sample.get("os_uptime_s"),
                sample.get("worker_service_status", "running"),
                sample.get("ollama_service_status", "unknown"),
            ),
        )
        sample_id = cursor.lastrowid
        conn.commit()
        return sample_id
    finally:
        conn.close()


def get_latest_sample_for_node(node_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve the most recent telemetry sample for a given node."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT * FROM telemetry_samples
            WHERE node_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (node_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            res["per_core"] = json.loads(res.get("per_core_json") or "[]")
            res["gpu"] = json.loads(res.get("gpu_info_json") or "{}")
        except Exception:
            res["per_core"] = []
            res["gpu"] = {}
        return res
    finally:
        conn.close()


def get_all_latest_samples() -> Dict[str, Dict[str, Any]]:
    """Retrieve the latest telemetry sample for every node."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT s.* FROM telemetry_samples s
            INNER JOIN (
                SELECT node_id, MAX(timestamp) as max_ts
                FROM telemetry_samples
                GROUP BY node_id
            ) latest ON s.node_id = latest.node_id AND s.timestamp = latest.max_ts
            """
        )
        rows = cursor.fetchall()
        result = {}
        for r in rows:
            d = dict(r)
            try:
                d["per_core"] = json.loads(d.get("per_core_json") or "[]")
                d["gpu"] = json.loads(d.get("gpu_info_json") or "{}")
            except Exception:
                d["per_core"] = []
                d["gpu"] = {}
            result[d["node_id"]] = d
        return result
    finally:
        conn.close()


def get_time_series_samples(
    node_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Query time-series samples within a time range, ordered chronologically."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        query = "SELECT * FROM telemetry_samples WHERE 1=1"
        params: List[Any] = []
        if node_id:
            query += " AND node_id = ?"
            params.append(node_id)
        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        query += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["per_core"] = json.loads(d.get("per_core_json") or "[]")
                d["gpu"] = json.loads(d.get("gpu_info_json") or "{}")
            except Exception:
                d["per_core"] = []
                d["gpu"] = {}
            result.append(d)
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rollups & Downsampling
# ---------------------------------------------------------------------------


def generate_minute_rollups() -> int:
    """Consolidate samples into 1-minute rollup buckets. Returns count of new rollups."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Group raw samples by node_id and minute bucket (YYYY-MM-DDTHH:MM:00)
        cursor.execute(
            """
            INSERT OR IGNORE INTO telemetry_rollups_minute (
                node_id, bucket_time, sample_count,
                cpu_avg, cpu_min, cpu_max,
                ram_used_avg, ram_used_min, ram_used_max,
                swap_used_avg, swap_used_min, swap_used_max,
                temp_avg, temp_max,
                net_rx_avg, net_tx_avg,
                disk_read_avg, disk_write_avg, latency_avg
            )
            SELECT
                node_id,
                substr(timestamp, 1, 16) || ':00' AS bucket_time,
                count(*) AS sample_count,
                avg(cpu_percent), min(cpu_percent), max(cpu_percent),
                avg(ram_used_mb), min(ram_used_mb), max(ram_used_mb),
                avg(swap_used_mb), min(swap_used_mb), max(swap_used_mb),
                avg(cpu_temp_c), max(cpu_temp_c),
                avg(net_rx_kbs), avg(net_tx_kbs),
                avg(disk_read_mbs), avg(disk_write_mbs), avg(latency_to_controller_ms)
            FROM telemetry_samples
            WHERE timestamp < replace(datetime('now', '-2 minutes'), ' ', 'T')
            GROUP BY node_id, bucket_time
            """
        )
        inserted = cursor.rowcount
        conn.commit()
        return max(0, inserted)
    finally:
        conn.close()


def generate_hourly_rollups() -> int:
    """Consolidate 1-minute rollups into 1-hour rollup buckets. Returns count of new rollups."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT OR IGNORE INTO telemetry_rollups_hour (
                node_id, bucket_time, sample_count,
                cpu_avg, cpu_min, cpu_max,
                ram_used_avg, ram_used_min, ram_used_max,
                swap_used_avg, swap_used_min, swap_used_max,
                temp_avg, temp_max,
                net_rx_avg, net_tx_avg,
                disk_read_avg, disk_write_avg, latency_avg
            )
            SELECT
                node_id,
                substr(bucket_time, 1, 13) || ':00:00' AS bucket_time,
                sum(sample_count) AS sample_count,
                avg(cpu_avg), min(cpu_min), max(cpu_max),
                avg(ram_used_avg), min(ram_used_min), max(ram_used_max),
                avg(swap_used_avg), min(swap_used_min), max(swap_used_max),
                avg(temp_avg), max(temp_max),
                avg(net_rx_avg), avg(net_tx_avg),
                avg(disk_read_avg), avg(disk_write_avg), avg(latency_avg)
            FROM telemetry_rollups_minute
            WHERE bucket_time < replace(datetime('now', '-2 hours'), ' ', 'T')
            GROUP BY node_id, substr(bucket_time, 1, 13)
            """
        )
        inserted = cursor.rowcount
        conn.commit()
        return max(0, inserted)
    finally:
        conn.close()


def enforce_retention_policy(raw_days: int = 7, minute_days: int = 30, hour_days: int = 365) -> Dict[str, int]:
    """Prune expired telemetry while preserving aggregate rollups.
    Default: raw samples 7 days, 1-minute rollups 30 days, 1-hour rollups 365 days.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    stats = {"pruned_raw": 0, "pruned_minute": 0, "pruned_hour": 0}
    try:
        # 1. Prune raw samples
        raw_cutoff = (datetime.now(timezone.utc) - timedelta(days=raw_days)).isoformat()
        cursor.execute("DELETE FROM telemetry_samples WHERE timestamp < ?", (raw_cutoff,))
        stats["pruned_raw"] = cursor.rowcount

        # 2. Prune minute rollups
        min_cutoff = (datetime.now(timezone.utc) - timedelta(days=minute_days)).isoformat()
        cursor.execute("DELETE FROM telemetry_rollups_minute WHERE bucket_time < ?", (min_cutoff,))
        stats["pruned_minute"] = cursor.rowcount

        # 3. Prune hour rollups
        hour_cutoff = (datetime.now(timezone.utc) - timedelta(days=hour_days)).isoformat()
        cursor.execute("DELETE FROM telemetry_rollups_hour WHERE bucket_time < ?", (hour_cutoff,))
        stats["pruned_hour"] = cursor.rowcount

        conn.commit()
        return stats
    finally:
        conn.close()


def get_rollups(
    node_id: Optional[str] = None,
    resolution: str = "minute",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    """Retrieve pre-computed rollups for smooth historical charts over longer time spans."""
    table = "telemetry_rollups_hour" if resolution == "hour" else "telemetry_rollups_minute"
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        query = f"SELECT * FROM {table} WHERE 1=1"
        params: List[Any] = []
        if node_id:
            query += " AND node_id = ?"
            params.append(node_id)
        if start_time:
            query += " AND bucket_time >= ?"
            params.append(start_time)
        if end_time:
            query += " AND bucket_time <= ?"
            params.append(end_time)

        query += " ORDER BY bucket_time ASC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Inference Telemetry & Token Accounting
# ---------------------------------------------------------------------------


def record_inference_telemetry(record: Dict[str, Any]) -> str:
    """Store authoritative inference telemetry from Ollama / worker engine."""
    rec_id = record.get("id") or f"inf-tel-{uuid.uuid4().hex[:10]}"
    req_id = record.get("request_id") or rec_id
    now = _now_iso()
    created_at = record.get("created_at") or now
    completed_at = record.get("completed_at") or now

    prompt_tokens = int(record.get("prompt_tokens") or 0)
    gen_tokens = int(record.get("gen_tokens") or 0)
    total_tokens = prompt_tokens + gen_tokens

    prompt_eval_ms = float(record.get("prompt_eval_duration_ms") or 0.0)
    eval_ms = float(record.get("eval_duration_ms") or 0.0)
    load_ms = float(record.get("load_duration_ms") or 0.0)
    total_ms = float(record.get("total_duration_ms") or 0.0)

    # Compute tokens/s if generation duration > 0
    tps = record.get("tokens_per_second")
    if tps is None and eval_ms > 0 and gen_tokens > 0:
        tps = round(gen_tokens / (eval_ms / 1000.0), 2)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO inference_telemetry (
                id, request_id, run_id, project_id, task_id,
                node_id, model, provider, status, prompt_tokens,
                gen_tokens, total_tokens, prompt_eval_duration_ms, eval_duration_ms,
                load_duration_ms, total_duration_ms, tokens_per_second, time_to_first_token_ms,
                error_category, retry_count, created_at, completed_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?
            )
            """,
            (
                rec_id,
                req_id,
                record.get("run_id"),
                record.get("project_id"),
                record.get("task_id"),
                record.get("node_id"),
                record.get("model"),
                record.get("provider", "ollama"),
                record.get("status", "success"),
                prompt_tokens,
                gen_tokens,
                total_tokens,
                prompt_eval_ms,
                eval_ms,
                load_ms,
                total_ms,
                tps,
                record.get("time_to_first_token_ms"),
                record.get("error_category"),
                int(record.get("retry_count") or 0),
                created_at,
                completed_at,
            ),
        )
        conn.commit()
        return rec_id
    finally:
        conn.close()


def query_inference_telemetry(
    node_id: Optional[str] = None,
    model: Optional[str] = None,
    project_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Retrieve inference telemetry records for performance and token auditing."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        query = "SELECT * FROM inference_telemetry WHERE 1=1"
        params: List[Any] = []
        if node_id:
            query += " AND node_id = ?"
            params.append(node_id)
        if model:
            query += " AND model = ?"
            params.append(model)
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if start_time:
            query += " AND created_at >= ?"
            params.append(start_time)
        if end_time:
            query += " AND created_at <= ?"
            params.append(end_time)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Alerts & Rules Storage
# ---------------------------------------------------------------------------


def get_all_alert_rules() -> List[Dict[str, Any]]:
    """Retrieve all alert rules."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM observatory_alert_rules ORDER BY category, name")
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def seed_default_alert_rules() -> int:
    """Ensure baseline alert rules are initialized."""
    rules = [
        ("cpu_sustained_high", "cpu", "cpu_percent", 90.0, ">=", 30, "warning"),
        ("ram_exhaustion_warning", "memory", "ram_percent", 92.0, ">=", 30, "warning"),
        ("swap_pressure_warning", "swap", "swap_percent", 50.0, ">=", 30, "warning"),
        ("cpu_temp_critical", "temperature", "cpu_temp_c", 82.0, ">=", 20, "critical"),
        ("disk_space_low", "storage", "disk_free_gb", 5.0, "<=", 60, "warning"),
        ("node_heartbeat_timeout", "worker", "heartbeat_age_s", 45.0, ">=", 45, "critical"),
        ("inference_latency_high", "model", "latency_ms", 60000.0, ">=", 1, "warning"),
        ("task_failure_spike", "worker", "consecutive_task_failures", 3.0, ">=", 1, "critical"),
    ]
    conn = get_db_connection()
    cursor = conn.cursor()
    seeded = 0
    now = _now_iso()
    try:
        for name, category, metric_name, threshold, op, duration_s, severity in rules:
            rule_id = f"rule-{uuid.uuid4().hex[:8]}"
            cursor.execute(
                """
                INSERT OR IGNORE INTO observatory_alert_rules (
                    id, name, category, metric_name, threshold, operator, duration_seconds, severity, is_enabled, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (rule_id, name, category, metric_name, threshold, op, duration_s, severity, now),
            )
            if cursor.rowcount > 0:
                seeded += 1
        conn.commit()
        return seeded
    finally:
        conn.close()


def create_alert(
    rule_name: str,
    category: str,
    node_id: Optional[str],
    severity: str,
    title: str,
    description: str,
    metrics: Dict[str, Any],
    cooldown_seconds: int = 300,
) -> Optional[str]:
    """Create a new firing alert instance if not already firing or in cooldown."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    try:
        # Check if an active alert for this rule and node already exists
        cursor.execute(
            """
            SELECT id, cooldown_until FROM observatory_alerts
            WHERE rule_name = ? AND (node_id = ? OR (node_id IS NULL AND ? IS NULL)) AND state = 'firing'
            LIMIT 1
            """,
            (rule_name, node_id, node_id),
        )
        existing = cursor.fetchone()
        if existing:
            # Already firing, do not duplicate
            return existing["id"]

        # Check if recently resolved alert is still in cooldown
        cursor.execute(
            """
            SELECT id FROM observatory_alerts
            WHERE rule_name = ? AND (node_id = ? OR (node_id IS NULL AND ? IS NULL))
              AND cooldown_until IS NOT NULL AND cooldown_until > ?
            LIMIT 1
            """,
            (rule_name, node_id, node_id, now_iso),
        )
        if cursor.fetchone():
            # In cooldown, suppress
            return None

        alert_id = f"alt-{uuid.uuid4().hex[:10]}"
        cooldown_until = (now_dt + timedelta(seconds=cooldown_seconds)).isoformat()
        cursor.execute(
            """
            INSERT INTO observatory_alerts (
                id, rule_name, category, node_id, severity, title, description,
                metrics_json, state, fired_at, cooldown_until
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'firing', ?, ?)
            """,
            (
                alert_id,
                rule_name,
                category,
                node_id,
                severity,
                title,
                description,
                json.dumps(metrics),
                now_iso,
                cooldown_until,
            ),
        )
        conn.commit()
        return alert_id
    finally:
        conn.close()


def resolve_alert(rule_name: str, node_id: Optional[str]) -> bool:
    """Mark a firing alert as resolved when conditions return to normal."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = _now_iso()
    try:
        cursor.execute(
            """
            UPDATE observatory_alerts
            SET state = 'resolved', resolved_at = ?
            WHERE rule_name = ? AND (node_id = ? OR (node_id IS NULL AND ? IS NULL)) AND state = 'firing'
            """,
            (now, rule_name, node_id, node_id),
        )
        resolved = cursor.rowcount > 0
        conn.commit()
        return resolved
    finally:
        conn.close()


def acknowledge_alert(alert_id: str, acknowledged_by: str = "admin") -> bool:
    """Acknowledge an active alert."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = _now_iso()
    try:
        cursor.execute(
            """
            UPDATE observatory_alerts
            SET state = 'acknowledged', acknowledged_at = ?, acknowledged_by = ?
            WHERE id = ? AND state = 'firing'
            """,
            (now, acknowledged_by, alert_id),
        )
        acked = cursor.rowcount > 0
        conn.commit()
        return acked
    finally:
        conn.close()


def get_alerts(
    state: Optional[str] = None,
    node_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Retrieve alerts by state or node."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        query = "SELECT * FROM observatory_alerts WHERE 1=1"
        params: List[Any] = []
        if state:
            query += " AND state = ?"
            params.append(state)
        if node_id:
            query += " AND node_id = ?"
            params.append(node_id)

        query += " ORDER BY fired_at DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["metrics"] = json.loads(d.get("metrics_json") or "{}")
            except Exception:
                d["metrics"] = {}
            result.append(d)
        return result
    finally:
        conn.close()
