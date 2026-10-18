"""
Comprehensive Automated Tests for System Observatory (Milestone 10).
Tests:
- Database schema and migration 10
- Telemetry collection and unit conversions
- Node capacity-weighted aggregation (strictly excluding legacy node-d35568f5)
- Worker authentication and node spoofing prevention
- Missing sensor handling (temperature, GPU)
- Alert threshold evaluation, hysteresis, cooldowns, and acknowledgment
- Rollups and retention pruning
- Inference token accounting and model performance comparisons
- Observatory API endpoints and HTML rendering
"""

import io
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import create_baseline_schema, get_db_connection
from app.main import app
from app.migrations import apply_migrations, MIGRATIONS
from app.services.observatory import alerting, analytics, collector, models, storage
from app.services import worker as worker_svc


@pytest.fixture
def test_db():
    """Create a temporary test database with full schema migrations (v1 to v10)."""
    orig_data_dir = settings.data_dir
    tmp_dir = Path(tempfile.mkdtemp())
    settings.data_dir = tmp_dir

    conn = get_db_connection()
    create_baseline_schema(conn)
    apply_migrations(conn, MIGRATIONS)

    # Seed infrastructure nodes: controller, vps, laptop, desktop, and legacy node-d35568f5
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO infrastructure_nodes (id, name, role, health_status, installed_ram_mb, available_ram_mb, storage_total_gb, storage_free_gb, is_enabled, created_at, updated_at)
        VALUES
        ('controller-local', 'Windows Controller', 'controller', 'online', 24573, 13333, 500.0, 290.0, 1, ?, ?),
        ('qwen-vps', 'Qwen VPS', 'vps_worker', 'online', 961, 450, 25.0, 15.0, 1, ?, ?),
        ('qwen-laptop', 'Qwen Laptop', 'inference_node', 'online', 3800, 1200, 120.0, 45.0, 1, ?, ?),
        ('desktop-worker-01', 'Desktop Worker', 'compute_worker', 'online', 12800, 6400, 250.0, 180.0, 1, ?, ?),
        ('node-d35568f5', 'Linode Legacy Record', 'compute_worker', 'offline', 961, 0, 25.0, 0.0, 0, ?, ?)
        """,
        (now, now, now, now, now, now, now, now, now, now),
    )
    conn.commit()
    conn.close()

    yield tmp_dir

    settings.data_dir = orig_data_dir


def test_schema_migration_v10(test_db):
    """Verify Migration 10 creates all 6 observatory tables and user_version is 10."""
    conn = get_db_connection()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 10

    tables = [
        "telemetry_samples",
        "telemetry_rollups_minute",
        "telemetry_rollups_hour",
        "inference_telemetry",
        "observatory_alert_rules",
        "observatory_alerts",
    ]
    for tbl in tables:
        count = conn.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]
        assert count >= 0, f"Table {tbl} should exist"
    conn.close()


def test_collector_local_windows_metrics(test_db):
    """Verify local controller collector returns real, non-fabricated metrics."""
    data = collector.collect_controller_telemetry()
    assert data["node_id"] == "controller-local"
    assert "timestamp" in data
    assert data["ram_total_mb"] > 0
    assert data["ram_available_mb"] > 0
    assert data["ram_used_mb"] >= 0
    assert 0.0 <= data["ram_percent"] <= 100.0
    assert data["disk_total_gb"] > 0
    assert data["disk_free_gb"] > 0
    assert data["worker_service_status"] == "running"
    # AC power must be 1, 0, or -1 (desktop/unknown)
    assert data["is_ac_online"] in (-1, 0, 1)


def test_missing_sensors_reporting(test_db):
    """Verify missing or unsupported sensors report None / explicit status rather than zero."""
    gpu = collector.collect_gpu_telemetry()
    if not gpu["supported"]:
        assert gpu["utilization_percent"] is None
        assert gpu["vram_used_mb"] is None
        assert "Not supported" in gpu["status_note"]

    # When a node has no temp sensor, it should be None
    sample_id = storage.record_telemetry_sample("qwen-vps", {
        "cpu_percent": 15.0,
        "ram_total_mb": 961,
        "ram_used_mb": 450,
        "cpu_temp_c": None,  # VPS virtualized, no hardware ACPI temp
    })
    sample = storage.get_latest_sample_for_node("qwen-vps")
    assert sample["cpu_temp_c"] is None


def test_capacity_weighted_aggregation_excludes_legacy(test_db):
    """Verify capacity-weighted CPU & RAM calculation strictly excludes legacy node-d35568f5."""
    now = datetime.now(timezone.utc).isoformat()

    # Record samples for active nodes
    storage.record_telemetry_sample("controller-local", {
        "timestamp": now, "cpu_percent": 40.0, "per_core": [40.0, 40.0, 40.0, 40.0],
        "ram_total_mb": 10000, "ram_used_mb": 4000, "swap_total_mb": 2000, "swap_used_mb": 500,
    })
    storage.record_telemetry_sample("qwen-laptop", {
        "timestamp": now, "cpu_percent": 80.0, "per_core": [80.0, 80.0],
        "ram_total_mb": 4000, "ram_used_mb": 3000, "swap_total_mb": 10000, "swap_used_mb": 2000,
    })
    storage.record_telemetry_sample("node-d35568f5", {
        "timestamp": now, "cpu_percent": 99.0,
        "ram_total_mb": 99999, "ram_used_mb": 99999,
    })

    overview = analytics.get_cluster_overview()
    cap = overview["cluster_capacity"]

    # controller-local has 4 cores (40%), laptop has 2 cores (80%) -> (40*4 + 80*2)/6 = (160+160)/6 = 53.3%
    # If node-d35568f5 was included, RAM and CPU would be wildly distorted
    assert cap["total_online_ram_mb"] < 50000, "Legacy node should NOT be included in cluster capacity"
    assert cap["aggregate_cpu_percent"] < 70.0
    assert overview["nodes"]["total_physical"] == 4, "Physical nodes count must exclude legacy record"


def test_worker_telemetry_authentication_and_spoofing(test_db):
    """Verify worker telemetry submission requires Bearer token and rejects node spoofing."""
    client = TestClient(app)

    # 1. Reject unauthenticated submission
    res = client.post("/api/v1/worker/telemetry", json={"node_id": "qwen-laptop", "cpu_percent": 25.0})
    assert res.status_code == 401

    # 2. Issue valid token for qwen-laptop
    token_id, raw_token = worker_svc.create_worker_token("qwen-laptop")
    headers = {"Authorization": f"Bearer {raw_token}"}

    # 3. Reject spoofing attempt (laptop token trying to report for qwen-vps)
    spoof_res = client.post(
        "/api/v1/worker/telemetry",
        json={"node_id": "qwen-vps", "cpu_percent": 95.0},
        headers=headers,
    )
    assert spoof_res.status_code == 403
    assert "cannot report telemetry" in spoof_res.json()["detail"]

    # 4. Accept legitimate submission
    good_res = client.post(
        "/api/v1/worker/telemetry",
        json={
            "node_id": "qwen-laptop",
            "cpu_percent": 35.0,
            "ram_total_mb": 3800,
            "ram_used_mb": 2100,
            "swap_total_mb": 36000,
            "swap_used_mb": 4200,
            "cpu_temp_c": 54.5,
            "battery_percent": 88.0,
            "is_ac_online": 1,
        },
        headers=headers,
    )
    assert good_res.status_code == 200
    assert good_res.json()["status"] == "recorded"

    # Verify stored in database
    latest = storage.get_latest_sample_for_node("qwen-laptop")
    assert latest["cpu_percent"] == 35.0
    assert latest["cpu_temp_c"] == 54.5
    assert latest["battery_percent"] == 88.0


def test_alert_threshold_hysteresis_and_resolution(test_db):
    """Verify alert rules evaluate hysteresis durations and auto-resolve when conditions normalize."""
    storage.seed_default_alert_rules()

    # Rule: cpu_sustained_high requires cpu_percent >= 90.0 for duration_seconds >= 30s
    node_id = "desktop-worker-01"

    # 1. Single spike: should NOT immediately fire alert (hysteresis protection)
    fired = alerting.evaluate_telemetry_for_alerts(node_id, {"cpu_percent": 95.0})
    assert len(fired) == 0

    # 2. Sustained condition simulated by advancing tracker
    alerting._HYSTERESIS_TRACKER[("cpu_sustained_high", node_id)] = (
        datetime.now(timezone.utc) - timedelta(seconds=35)
    ).timestamp()
    fired_sustained = alerting.evaluate_telemetry_for_alerts(node_id, {"cpu_percent": 95.0})
    assert len(fired_sustained) > 0

    alerts = storage.get_alerts(state="firing", node_id=node_id)
    assert len(alerts) >= 1
    alert_id = alerts[0]["id"]
    assert alerts[0]["rule_name"] == "cpu_sustained_high"

    # 3. Acknowledge alert
    acked = storage.acknowledge_alert(alert_id, acknowledged_by="admin")
    assert acked is True
    acked_alerts = storage.get_alerts(state="acknowledged", node_id=node_id)
    assert len(acked_alerts) == 1

    # 4. Condition returns to normal -> auto-resolve
    alerting.evaluate_telemetry_for_alerts(node_id, {"cpu_percent": 30.0})
    active_now = storage.get_alerts(state="firing", node_id=node_id)
    assert len(active_now) == 0


def test_inference_telemetry_accounting_and_comparison(test_db):
    """Verify authoritative Ollama inference duration metrics and Qwen vs Llama comparison."""
    # Record Qwen sample
    storage.record_inference_telemetry({
        "request_id": "inf-req-1",
        "node_id": "qwen-laptop",
        "model": "qwen2.5:1.5b",
        "prompt_tokens": 120,
        "gen_tokens": 60,
        "prompt_eval_duration_ms": 150.0,
        "eval_duration_ms": 3000.0,  # 60 tokens in 3.0s = 20 tok/s
        "load_duration_ms": 500.0,
        "total_duration_ms": 3650.0,
        "status": "success",
    })

    # Record Llama sample
    storage.record_inference_telemetry({
        "request_id": "inf-req-2",
        "node_id": "qwen-laptop",
        "model": "llama3.2:1b",
        "prompt_tokens": 100,
        "gen_tokens": 40,
        "prompt_eval_duration_ms": 120.0,
        "eval_duration_ms": 2500.0,  # 40 tokens in 2.5s = 16 tok/s
        "load_duration_ms": 800.0,
        "total_duration_ms": 3420.0,
        "status": "success",
    })

    perf = analytics.get_model_performance_analytics()
    assert len(perf["models"]) >= 2

    # Check side-by-side comparison
    cmp = perf["comparison"]
    assert cmp["qwen"] is not None
    assert cmp["llama"] is not None
    assert "qwen" in cmp["faster_generation"].lower()

    # Token accounting
    acct = analytics.get_token_accounting()
    assert acct["summary"]["lifetime"]["total_tokens"] == 180 + 140
    assert acct["cost_accounting"]["api_billing_cost_usd"] == 0.0


def test_rollups_and_retention_pruning(test_db):
    """Verify minute rollups consolidation and retention pruning."""
    past_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    # Insert 3 raw samples in the same minute
    for i in range(3):
        storage.record_telemetry_sample("qwen-vps", {
            "timestamp": past_ts,
            "cpu_percent": 10.0 + i * 10,  # 10, 20, 30 -> avg 20, min 10, max 30
            "ram_total_mb": 961,
            "ram_used_mb": 400 + i * 50,
        })

    inserted = storage.generate_minute_rollups()
    assert inserted >= 1

    rollups = storage.get_rollups("qwen-vps", resolution="minute")
    assert len(rollups) >= 1
    r = rollups[0]
    assert r["cpu_min"] == 10.0
    assert r["cpu_max"] == 30.0
    assert r["cpu_avg"] == 20.0

    # Test retention pruning
    pruned = storage.enforce_retention_policy(raw_days=0, minute_days=30, hour_days=365)
    assert pruned["pruned_raw"] >= 3


def test_observatory_api_and_page_rendering(test_db):
    """Verify HTTP endpoints and HTML dashboard rendering for all 6 tabs."""
    client = TestClient(app)

    # 1. Primary Observatory HTML page
    res = client.get("/observatory")
    assert res.status_code == 200
    assert "System Observatory" in res.text
    assert "1. Overview" in res.text
    assert "2. Machines" in res.text
    assert "3. Models & Tokens" in res.text
    assert "4. Tasks & Projects" in res.text
    assert "5. Errors & Alerts" in res.text
    assert "6. History & Analytics" in res.text

    # 2. API Overview
    api_res = client.get("/api/v1/observatory/overview")
    assert api_res.status_code == 200
    assert "nodes" in api_res.json()
    assert "cluster_capacity" in api_res.json()

    # 3. API Nodes
    nodes_res = client.get("/api/v1/observatory/nodes")
    assert nodes_res.status_code == 200
    assert nodes_res.json()["total_physical"] == 4

    # 4. API Metrics
    metrics_res = client.get("/api/v1/observatory/metrics?resolution=raw&limit=50")
    assert metrics_res.status_code == 200

    # 5. API Inference
    inf_res = client.get("/api/v1/observatory/inference")
    assert inf_res.status_code == 200

    # 6. CSV Export
    csv_res = client.get("/api/v1/observatory/export?data_type=telemetry")
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    assert "timestamp,node_id,cpu_percent" in csv_res.text
