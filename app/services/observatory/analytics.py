"""
System Observatory Analytics Engine.
Calculates capacity-weighted cluster utilization, per-node health, model performance comparisons,
token accounting, worker efficiency, and deterministic system insights.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_db_connection
from app.services.observatory import storage

logger = logging.getLogger("qwen_collective.observatory.analytics")

# Explicit legacy node records to exclude from physical hardware capacity aggregates
LEGACY_NODE_IDS = {"node-d35568f5"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def get_cluster_overview() -> Dict[str, Any]:
    """Compute overall cluster health, node statuses, and capacity-weighted metrics."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now_dt = datetime.now(timezone.utc)

    # 1. Query registered nodes
    cursor.execute(
        """
        SELECT id, name, role, hostname, ip_address, tailscale_ip,
               tailscale_status, os_name, os_version, architecture,
               installed_ram_mb, available_ram_mb, storage_total_gb, storage_free_gb,
               health_status, last_contact_at, is_enabled
        FROM infrastructure_nodes
        ORDER BY role, name
        """
    )
    all_nodes = [dict(r) for r in cursor.fetchall()]

    # Filter out legacy records for physical hardware metrics
    physical_nodes = [n for n in all_nodes if n["id"] not in LEGACY_NODE_IDS]
    latest_samples = storage.get_all_latest_samples()

    online_count = 0
    offline_count = 0
    degraded_count = 0
    unknown_count = 0

    total_online_cores = 0
    weighted_cpu_sum = 0.0
    total_online_ram_mb = 0
    total_online_ram_used_mb = 0
    total_online_swap_total_mb = 0
    total_online_swap_used_mb = 0

    node_summaries = []
    most_recent_ts: Optional[str] = None

    for node in physical_nodes:
        nid = node["id"]
        sample = latest_samples.get(nid)

        # Determine effective health based on recent telemetry or heartbeat
        last_contact = node.get("last_contact_at")
        sample_ts = sample.get("timestamp") if sample else None
        last_ts_str = sample_ts or last_contact
        last_ts_dt = _parse_iso(last_ts_str)

        is_online = False
        health = "unknown"

        if last_ts_dt:
            age_s = (now_dt - last_ts_dt).total_seconds()
            if age_s <= 45.0:
                is_online = True
                health = "online"
                online_count += 1
            elif age_s <= 120.0:
                health = "degraded"
                degraded_count += 1
            else:
                health = "offline"
                offline_count += 1
        else:
            unknown_count += 1

        if sample_ts and (not most_recent_ts or sample_ts > most_recent_ts):
            most_recent_ts = sample_ts

        # Node hardware info
        cpu_pct = sample.get("cpu_percent") if sample and sample.get("cpu_percent") is not None else 0.0
        # Estimate or read core count
        per_core = sample.get("per_core") if sample else []
        core_count = max(1, len(per_core)) if per_core else 1

        ram_total = (sample.get("ram_total_mb") if sample and sample.get("ram_total_mb") else node.get("installed_ram_mb")) or 1024
        ram_used = sample.get("ram_used_mb") if sample and sample.get("ram_used_mb") is not None else max(0, ram_total - (node.get("available_ram_mb") or 512))
        ram_pct = round((ram_used / ram_total) * 100.0, 1) if ram_total > 0 else 0.0

        swap_total = sample.get("swap_total_mb") if sample else 0
        swap_used = sample.get("swap_used_mb") if sample else 0

        if is_online:
            total_online_cores += core_count
            weighted_cpu_sum += (cpu_pct * core_count)
            total_online_ram_mb += ram_total
            total_online_ram_used_mb += ram_used
            total_online_swap_total_mb += (swap_total or 0)
            total_online_swap_used_mb += (swap_used or 0)

        node_summaries.append({
            "id": nid,
            "name": node["name"],
            "role": node["role"],
            "health": health,
            "is_online": is_online,
            "last_contact": last_ts_str,
            "cpu_percent": cpu_pct,
            "ram_total_mb": ram_total,
            "ram_used_mb": ram_used,
            "ram_percent": ram_pct,
            "swap_total_mb": swap_total,
            "swap_used_mb": swap_used,
            "disk_free_gb": sample.get("disk_free_gb") if sample else node.get("storage_free_gb"),
            "temperature_c": sample.get("cpu_temp_c") if sample else None,
            "gpu": sample.get("gpu") if sample else None,
        })

    # 2. Defensible capacity-weighted aggregates
    aggregate_cpu_pct = round(weighted_cpu_sum / total_online_cores, 1) if total_online_cores > 0 else 0.0
    aggregate_ram_pct = round((total_online_ram_used_mb / total_online_ram_mb) * 100.0, 1) if total_online_ram_mb > 0 else 0.0
    aggregate_swap_pct = round((total_online_swap_used_mb / total_online_swap_total_mb) * 100.0, 1) if total_online_swap_total_mb > 0 else 0.0

    # 3. Task execution counters
    cursor.execute(
        """
        SELECT
            COUNT(*) as total_tasks,
            SUM(CASE WHEN status IN ('pending', 'approved') THEN 1 ELSE 0 END) as queued_tasks,
            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as active_tasks,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_tasks,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_tasks
        FROM tasks
        """
    )
    task_row = dict(cursor.fetchone() or {})
    total_terminal = (task_row.get("completed_tasks") or 0) + (task_row.get("failed_tasks") or 0)
    task_completion_rate = round(((task_row.get("completed_tasks") or 0) / total_terminal) * 100.0, 1) if total_terminal > 0 else 100.0
    task_failure_rate = round(((task_row.get("failed_tasks") or 0) / total_terminal) * 100.0, 1) if total_terminal > 0 else 0.0

    # 4. Token metrics and inference throughput (past 24h)
    cursor.execute(
        """
        SELECT
            COUNT(*) as total_inferences,
            SUM(prompt_tokens) as total_prompt_tokens,
            SUM(gen_tokens) as total_gen_tokens,
            SUM(total_tokens) as total_tokens,
            AVG(tokens_per_second) as avg_tokens_per_second,
            AVG(eval_duration_ms + prompt_eval_duration_ms) as avg_latency_ms
        FROM inference_telemetry
        WHERE created_at >= datetime('now', '-24 hours')
        """
    )
    inf_row = dict(cursor.fetchone() or {})
    tokens_24h = int(inf_row.get("total_tokens") or 0)
    avg_tps = round(float(inf_row.get("avg_tokens_per_second") or 0.0), 1)

    # Lifetime tokens
    cursor.execute("SELECT SUM(total_tokens) FROM inference_telemetry")
    lifetime_tokens = int(cursor.fetchone()[0] or 0)

    # 5. Active alerts
    cursor.execute("SELECT COUNT(*) FROM observatory_alerts WHERE state = 'firing'")
    active_alerts_count = int(cursor.fetchone()[0] or 0)

    # 6. Authenticated worker tokens count
    cursor.execute("SELECT COUNT(DISTINCT node_id) FROM worker_auth_tokens WHERE revoked = 0")
    authenticated_workers_count = int(cursor.fetchone()[0] or 0)

    conn.close()

    return {
        "timestamp": _now_iso(),
        "most_recent_telemetry": most_recent_ts,
        "nodes": {
            "total_physical": len(physical_nodes),
            "online": online_count,
            "offline": offline_count,
            "degraded": degraded_count,
            "unknown": unknown_count,
            "authenticated_workers": authenticated_workers_count,
            "summaries": node_summaries,
        },
        "cluster_capacity": {
            "total_online_cores": total_online_cores,
            "aggregate_cpu_percent": aggregate_cpu_pct,
            "total_online_ram_mb": total_online_ram_mb,
            "total_online_ram_used_mb": total_online_ram_used_mb,
            "aggregate_ram_percent": aggregate_ram_pct,
            "total_online_swap_total_mb": total_online_swap_total_mb,
            "total_online_swap_used_mb": total_online_swap_used_mb,
            "aggregate_swap_percent": aggregate_swap_pct,
        },
        "tasks": {
            "total": task_row.get("total_tasks") or 0,
            "queued": task_row.get("queued_tasks") or 0,
            "active": task_row.get("active_tasks") or 0,
            "completed": task_row.get("completed_tasks") or 0,
            "failed": task_row.get("failed_tasks") or 0,
            "completion_rate_percent": task_completion_rate,
            "failure_rate_percent": task_failure_rate,
        },
        "inference": {
            "requests_24h": inf_row.get("total_inferences") or 0,
            "tokens_24h": tokens_24h,
            "lifetime_tokens": lifetime_tokens,
            "avg_tokens_per_second": avg_tps,
            "avg_latency_ms": round(float(inf_row.get("avg_latency_ms") or 0.0), 1),
        },
        "active_alerts_count": active_alerts_count,
        "research_and_knowledge": get_research_and_knowledge_metrics(),
    }



def get_node_detail(node_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve in-depth metrics and time series for a specific machine."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM infrastructure_nodes WHERE id = ?", (node_id,))
    node_row = cursor.fetchone()
    if not node_row:
        conn.close()
        return None

    node_data = dict(node_row)
    latest_sample = storage.get_latest_sample_for_node(node_id)
    recent_samples = storage.get_time_series_samples(node_id=node_id, limit=60)

    # Worker performance stats for this node
    cursor.execute(
        """
        SELECT
            COUNT(*) as total_executions,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_executions,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_executions,
            SUM(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END) as timeout_executions,
            AVG(runtime_seconds) as avg_runtime_s
        FROM worker_executions
        WHERE worker_id = ?
        """,
        (node_id,),
    )
    exec_row = dict(cursor.fetchone() or {})

    conn.close()

    return {
        "node": node_data,
        "latest_sample": latest_sample,
        "recent_samples": recent_samples,
        "worker_performance": exec_row,
    }


def get_model_performance_analytics() -> Dict[str, Any]:
    """Retrieve detailed per-model inference telemetry, comparisons, and latency percentiles."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            model,
            COUNT(*) as total_requests,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count,
            SUM(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END) as timeout_count,
            SUM(prompt_tokens) as total_prompt_tokens,
            SUM(gen_tokens) as total_gen_tokens,
            SUM(total_tokens) as total_tokens,
            AVG(tokens_per_second) as avg_tps,
            AVG(prompt_eval_duration_ms) as avg_prompt_eval_ms,
            AVG(eval_duration_ms) as avg_eval_ms,
            AVG(load_duration_ms) as avg_load_ms,
            AVG(total_duration_ms) as avg_latency_ms,
            MIN(created_at) as first_seen,
            MAX(created_at) as last_seen
        FROM inference_telemetry
        GROUP BY model
        ORDER BY total_requests DESC
        """
    )
    rows = cursor.fetchall()
    models_stats = []

    for r in rows:
        d = dict(r)
        reqs = d.get("total_requests") or 0
        succ = d.get("success_count") or 0
        d["success_rate_percent"] = round((succ / reqs) * 100.0, 1) if reqs > 0 else 100.0
        d["avg_tps"] = round(float(d.get("avg_tps") or 0.0), 2)
        d["avg_latency_ms"] = round(float(d.get("avg_latency_ms") or 0.0), 1)
        d["avg_load_ms"] = round(float(d.get("avg_load_ms") or 0.0), 1)
        models_stats.append(d)

    # Side-by-side comparison (e.g. Qwen vs Llama)
    qwen_stats = next((m for m in models_stats if "qwen" in m["model"].lower()), None)
    llama_stats = next((m for m in models_stats if "llama" in m["model"].lower()), None)

    comparison = {
        "qwen": qwen_stats,
        "llama": llama_stats,
        "faster_generation": None,
        "faster_loading": None,
    }
    if qwen_stats and llama_stats:
        if qwen_stats.get("avg_tps", 0) > llama_stats.get("avg_tps", 0):
            comparison["faster_generation"] = qwen_stats["model"]
        else:
            comparison["faster_generation"] = llama_stats["model"]

        if qwen_stats.get("avg_load_ms", 0) < llama_stats.get("avg_load_ms", 0):
            comparison["faster_loading"] = qwen_stats["model"]
        else:
            comparison["faster_loading"] = llama_stats["model"]

    conn.close()

    return {
        "models": models_stats,
        "comparison": comparison,
    }


def get_token_accounting() -> Dict[str, Any]:
    """Calculate token accounting across time windows, models, nodes, and projects."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Time window totals
    windows = {
        "today": "datetime('now', 'start of day')",
        "last_7d": "datetime('now', '-7 days')",
        "last_30d": "datetime('now', '-30 days')",
        "lifetime": "1970-01-01",
    }
    window_results = {}
    for key, cut in windows.items():
        cursor.execute(
            f"""
            SELECT
                COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                COALESCE(SUM(gen_tokens), 0) as gen_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens
            FROM inference_telemetry
            WHERE created_at >= {cut}
            """
        )
        row = dict(cursor.fetchone() or {})
        window_results[key] = row

    # By model
    cursor.execute(
        """
        SELECT model, SUM(prompt_tokens) as prompt, SUM(gen_tokens) as gen, SUM(total_tokens) as total
        FROM inference_telemetry
        GROUP BY model
        ORDER BY total DESC
        """
    )
    by_model = [dict(r) for r in cursor.fetchall()]

    # By node
    cursor.execute(
        """
        SELECT node_id, SUM(prompt_tokens) as prompt, SUM(gen_tokens) as gen, SUM(total_tokens) as total
        FROM inference_telemetry
        GROUP BY node_id
        ORDER BY total DESC
        """
    )
    by_node = [dict(r) for r in cursor.fetchall()]

    # By project
    cursor.execute(
        """
        SELECT COALESCE(project_id, 'unassigned') as project_id,
               SUM(prompt_tokens) as prompt, SUM(gen_tokens) as gen, SUM(total_tokens) as total
        FROM inference_telemetry
        GROUP BY project_id
        ORDER BY total DESC
        LIMIT 20
        """
    )
    by_project = [dict(r) for r in cursor.fetchall()]

    conn.close()

    return {
        "summary": window_results,
        "by_model": by_model,
        "by_node": by_node,
        "by_project": by_project,
        "cost_accounting": {
            "api_billing_cost_usd": 0.0,
            "billing_model": "Local Private Compute (Zero External API Fees)",
            "hardware_note": "Inference executed on local physical nodes (Linux laptop, VPS, Windows).",
        },
    }


def get_task_and_worker_efficiency() -> Dict[str, Any]:
    """Compute worker performance, scheduling latency, useful-time ratio, and orchestrator metrics."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Per-worker execution history
    cursor.execute(
        """
        SELECT
            worker_id,
            COUNT(*) as total_executions,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count,
            SUM(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END) as timeout_count,
            SUM(runtime_seconds) as total_runtime_s,
            AVG(runtime_seconds) as avg_runtime_s
        FROM worker_executions
        GROUP BY worker_id
        """
    )
    worker_stats = []
    for r in cursor.fetchall():
        d = dict(r)
        tot = d["total_executions"]
        succ = d["success_count"]
        d["success_rate_pct"] = round((succ / tot) * 100.0, 1) if tot > 0 else 100.0
        d["avg_runtime_s"] = round(float(d.get("avg_runtime_s") or 0.0), 2)
        d["total_runtime_m"] = round(float(d.get("total_runtime_s") or 0.0) / 60.0, 1)
        worker_stats.append(d)

    # Project orchestrator stats if available
    cursor.execute(
        """
        SELECT
            COUNT(*) as total_runs,
            SUM(CASE WHEN state IN ('READY', 'RUNNING') THEN 1 ELSE 0 END) as active_runs,
            SUM(CASE WHEN state = 'COMPLETED' THEN 1 ELSE 0 END) as completed_runs,
            SUM(CASE WHEN state = 'FAILED' THEN 1 ELSE 0 END) as failed_runs,
            SUM(CASE WHEN state = 'BLOCKED' THEN 1 ELSE 0 END) as blocked_runs
        FROM orchestration_runs
        """
    )
    orch_summary = dict(cursor.fetchone() or {})

    # Orchestrator planned vs executed tasks
    cursor.execute(
        """
        SELECT
            COUNT(*) as total_orch_tasks,
            SUM(CASE WHEN state = 'accepted' THEN 1 ELSE 0 END) as accepted_tasks,
            SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END) as failed_tasks,
            SUM(CASE WHEN state = 'pending' THEN 1 ELSE 0 END) as pending_tasks,
            SUM(CASE WHEN attempts > 1 THEN 1 ELSE 0 END) as retried_tasks
        FROM orchestration_tasks
        """
    )
    orch_tasks = dict(cursor.fetchone() or {})
    total_orch = orch_tasks.get("total_orch_tasks") or 0
    retried_orch = orch_tasks.get("retried_orch_tasks") or 0
    retry_overhead_pct = round((retried_orch / total_orch) * 100.0, 1) if total_orch > 0 else 0.0

    conn.close()

    return {
        "workers": worker_stats,
        "orchestrator": {
            "summary": orch_summary,
            "tasks": orch_tasks,
            "task_retry_overhead_percent": retry_overhead_pct,
        },
    }


def generate_system_insights() -> List[Dict[str, Any]]:
    """Produce deterministic, evidence-based recommendations citing actual telemetry."""
    insights: List[Dict[str, Any]] = []
    latest_samples = storage.get_all_latest_samples()

    # 1. Check for swap thrashing on nodes with heavy swap
    for nid, s in latest_samples.items():
        swap_pct = s.get("swap_percent") or 0.0
        swap_used_mb = s.get("swap_used_mb") or 0
        if swap_pct >= 50.0:
            insights.append({
                "observation": f"Node '{nid}' exhibits significant swap utilization ({swap_pct}% used, {swap_used_mb} MB).",
                "category": "memory_pressure",
                "node_id": nid,
                "likely_explanation": "Physical RAM is constrained, causing OS paging. On inference nodes like the Linux laptop, heavy swapping can throttle token generation throughput.",
                "confidence": "high",
                "recommended_action": "Avoid co-scheduling multiple memory-intensive tasks on this node; prefer lightweight quantized models (e.g. 1.5B or 1B) or pin model residency.",
                "supporting_metrics": {"swap_percent": swap_pct, "swap_used_mb": swap_used_mb, "ram_percent": s.get("ram_percent")},
            })

    # 2. Check for sustained thermal throttling
    for nid, s in latest_samples.items():
        temp = s.get("cpu_temp_c")
        if temp and temp >= 78.0:
            insights.append({
                "observation": f"Node '{nid}' CPU temperature is elevated at {temp}°C.",
                "category": "thermal",
                "node_id": nid,
                "likely_explanation": "Prolonged high compute workload causing thermal rise; CPU may engage hardware frequency scaling/throttling.",
                "confidence": "high",
                "recommended_action": "Check cooling ventilation and consider inserting brief cooldown pauses between batch inference requests.",
                "supporting_metrics": {"cpu_temp_c": temp, "cpu_percent": s.get("cpu_percent")},
            })

    # 3. Model comparison insight
    model_perf = get_model_performance_analytics()
    qwen = model_perf.get("comparison", {}).get("qwen")
    llama = model_perf.get("comparison", {}).get("llama")
    if qwen and llama:
        q_tps = qwen.get("avg_tps", 0)
        l_tps = llama.get("avg_tps", 0)
        if q_tps > 0 and l_tps > 0:
            diff = round(abs(q_tps - l_tps), 1)
            faster = "Qwen" if q_tps > l_tps else "Llama"
            insights.append({
                "observation": f"{faster} demonstrates higher token throughput ({max(q_tps, l_tps)} tok/s vs {min(q_tps, l_tps)} tok/s, +{diff} tok/s).",
                "category": "model_efficiency",
                "node_id": "cluster",
                "likely_explanation": f"Model architecture, weight quantization, and context window efficiency favor {faster} on current CPU/worker hardware.",
                "confidence": "high",
                "recommended_action": f"Prioritize {faster} for high-throughput or latency-sensitive background generation tasks.",
                "supporting_metrics": {"qwen_tps": q_tps, "llama_tps": l_tps},
            })

    # 4. Storage headroom insight
    for nid, s in latest_samples.items():
        disk_free = s.get("disk_free_gb")
        if disk_free is not None and disk_free < 8.0:
            insights.append({
                "observation": f"Node '{nid}' has low disk free headroom ({disk_free} GB remaining).",
                "category": "storage",
                "node_id": nid,
                "likely_explanation": "Accumulation of model weights, temporary execution artifacts, or database growth.",
                "confidence": "high",
                "recommended_action": "Run evidence storage cleanup or purge unused cached models.",
                "supporting_metrics": {"disk_free_gb": disk_free, "disk_total_gb": s.get("disk_total_gb")},
            })

    return insights


def get_research_and_knowledge_metrics() -> Dict[str, Any]:
    """Calculate observatory telemetry for continuous research and persistent knowledge."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Checkpoint stats
    cursor.execute("""
        SELECT 
            COUNT(*) as total_checkpoints,
            SUM(CASE WHEN research_needed = 1 AND status = 'completed' THEN 1 ELSE 0 END) as searches_executed,
            SUM(CASE WHEN research_needed = 0 THEN 1 ELSE 0 END) as searches_skipped,
            SUM(CASE WHEN status = 'degraded' THEN 1 ELSE 0 END) as degraded_modes
        FROM stage_research_checkpoints
    """)

    cp_row = dict(cursor.fetchone() or {})
    total_cp = cp_row.get("total_checkpoints") or 0
    searches_exec = cp_row.get("searches_executed") or 0
    searches_skipped = cp_row.get("searches_skipped") or 0
    degraded = cp_row.get("degraded_modes") or 0
    reuse_rate = round((searches_skipped / max(1, total_cp)) * 100.0, 1)

    # Knowledge record counts
    cursor.execute("""
        SELECT 
            COUNT(*) as total_records,
            SUM(CASE WHEN scope = 'collective' THEN 1 ELSE 0 END) as collective_records,
            SUM(CASE WHEN scope = 'project' THEN 1 ELSE 0 END) as project_records,
            SUM(CASE WHEN verification_status IN ('supported', 'corroborated') OR confidence_assessment = 'verified' THEN 1 ELSE 0 END) as verified_records,
            SUM(CASE WHEN verification_status = 'corroborated' THEN 1 ELSE 0 END) as corroborated_records,
            SUM(CASE WHEN verification_status = 'contradicted' OR knowledge_type = 'contradiction' THEN 1 ELSE 0 END) as contradictions,
            SUM(CASE WHEN superseded_by IS NOT NULL THEN 1 ELSE 0 END) as superseded,
            SUM(CASE WHEN source_ids_json != '[]' THEN 1 ELSE 0 END) as with_sources,
            SUM(CASE WHEN verification_status = 'outdated' THEN 1 ELSE 0 END) as stale_records
        FROM knowledge_records
    """)

    kr_row = dict(cursor.fetchone() or {})
    total_kr = kr_row.get("total_records") or 0
    verified_kr = kr_row.get("verified_records") or 0
    with_sources = kr_row.get("with_sources") or 0
    evidence_coverage = round((with_sources / max(1, total_kr)) * 100.0, 1) if total_kr > 0 else 0.0

    # Promotions
    cursor.execute("""
        SELECT 
            COUNT(*) as total_promotions,
            SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved_promotions,
            SUM(CASE WHEN status = 'proposed' THEN 1 ELSE 0 END) as pending_promotions
        FROM knowledge_promotions
    """)
    pr_row = dict(cursor.fetchone() or {})

    # Impacts
    cursor.execute("""
        SELECT 
            COUNT(*) as total_impacts,
            SUM(CASE WHEN is_affected = 1 THEN 1 ELSE 0 END) as affected_impacts
        FROM knowledge_impacts
    """)
    imp_row = dict(cursor.fetchone() or {})

    research_efficiency = round(total_kr / max(1, searches_exec), 2) if searches_exec > 0 else round(float(total_kr), 2)

    conn.close()

    return {
        "checkpoints": {
            "total": total_cp,
            "searches_executed": searches_exec,
            "searches_skipped": searches_skipped,
            "degraded_modes": degraded,
            "reuse_rate_percent": reuse_rate,
        },
        "knowledge_base": {
            "total_records": total_kr,
            "collective_records": kr_row.get("collective_records") or 0,
            "project_records": kr_row.get("project_records") or 0,
            "verified_records": verified_kr,
            "corroborated_records": kr_row.get("corroborated_records") or 0,
            "contradictions_detected": kr_row.get("contradictions") or 0,
            "superseded_records": kr_row.get("superseded") or 0,
            "stale_records": kr_row.get("stale_records") or 0,
            "evidence_coverage_percent": evidence_coverage,
            "research_efficiency": research_efficiency,
        },
        "promotions": {
            "total": pr_row.get("total_promotions") or 0,
            "approved": pr_row.get("approved_promotions") or 0,
            "pending": pr_row.get("pending_promotions") or 0,
        },
        "impacts": {
            "tracked": imp_row.get("total_impacts") or 0,
            "affected": imp_row.get("affected_impacts") or 0,
        },
    }

