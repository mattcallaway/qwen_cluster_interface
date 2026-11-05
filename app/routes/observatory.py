"""
System Observatory Web & API Routes.
Provides full observability endpoints for Cluster Overview, Machines,
Models & Tokens, Tasks & Projects, Errors & Alerts, and History & Analytics.
"""

import csv
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_current_user
from app.config import settings
from app.services import operational_health
from app.services.observatory import alerting, analytics, models, storage

logger = logging.getLogger("qwen_collective.observatory_routes")

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["observatory"])


# ---------------------------------------------------------------------------
# Health & Operational Reliability Endpoints
# ---------------------------------------------------------------------------

@router.get("/health")
@router.get("/api/v1/health")
def get_liveness_health():
    """Lightweight operational liveness probe."""
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": settings.app_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/v1/observatory/snapshots/latest")
def get_latest_health_snapshot():
    """Cluster Real-Time Health Snapshot matching API Specification Section 6.1."""
    return operational_health.get_cluster_health_snapshot()


@router.get("/api/v1/system/diagnostics")
def get_system_diagnostics():
    """Comprehensive operational diagnostics, worker health, queue progress, and stall detection."""
    return operational_health.get_operational_diagnostics()


# ---------------------------------------------------------------------------
# HTML Views
# ---------------------------------------------------------------------------

@router.get("/observatory", response_class=HTMLResponse)
def observatory_page(request: Request, tab: str = "overview"):
    """Render the primary System Observatory control-room dashboard."""
    current_user = get_current_user(request)
    overview_data = analytics.get_cluster_overview()

    return templates.TemplateResponse(
        request=request,
        name="observatory.html",
        context={
            "current_user": current_user,
            "active_nav": "observatory",
            "active_tab": tab,
            "overview": overview_data,
        },
    )


# ---------------------------------------------------------------------------
# API v1 Observatory Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/v1/observatory/overview")
def get_overview_api(request: Request):
    """Retrieve near-real-time overall cluster health, node counts, and capacity."""
    return analytics.get_cluster_overview()


@router.get("/api/v1/observatory/nodes")
def list_nodes_api(request: Request):
    """List physical nodes with latest telemetry, excluding legacy virtual records."""
    overview = analytics.get_cluster_overview()
    return overview.get("nodes", {})


@router.get("/api/v1/observatory/nodes/{node_id}")
def get_node_detail_api(node_id: str, request: Request):
    """Retrieve detailed telemetry, hardware specifications, and time series for a node."""
    detail = analytics.get_node_detail(node_id)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Node '{node_id}' not found.")
    return detail


@router.get("/api/v1/observatory/metrics")
def get_metrics_time_series(
    node_id: Optional[str] = None,
    resolution: str = Query("raw", pattern="^(raw|minute|hour)$"),
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = Query(500, ge=10, le=2000),
    request: Request = None,
):
    """Query time-series telemetry samples or consolidated rollups."""
    if resolution == "raw":
        samples = storage.get_time_series_samples(
            node_id=node_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        return {"resolution": "raw", "count": len(samples), "data": samples}
    else:
        rollups = storage.get_rollups(
            node_id=node_id,
            resolution=resolution,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        return {"resolution": resolution, "count": len(rollups), "data": rollups}


@router.get("/api/v1/observatory/inference")
def get_inference_analytics_api(request: Request):
    """Retrieve model performance statistics, side-by-side comparison, and token usage."""
    models_perf = analytics.get_model_performance_analytics()
    tokens_acct = analytics.get_token_accounting()
    return {
        "models": models_perf.get("models", []),
        "comparison": models_perf.get("comparison", {}),
        "token_accounting": tokens_acct,
    }


@router.get("/api/v1/observatory/tasks")
def get_task_and_worker_metrics_api(request: Request):
    """Retrieve worker task execution rates, useful-time ratios, and orchestrator metrics."""
    return analytics.get_task_and_worker_efficiency()


@router.get("/api/v1/observatory/projects")
def get_project_resource_metrics_api(request: Request):
    """Retrieve resource and token consumption attributed to individual projects."""
    tokens_acct = analytics.get_token_accounting()
    task_eff = analytics.get_task_and_worker_efficiency()
    return {
        "project_token_usage": tokens_acct.get("by_project", []),
        "orchestrator_runs": task_eff.get("orchestrator", {}),
    }


@router.get("/api/v1/observatory/alerts")
def get_alerts_api(
    state: Optional[str] = Query(None, pattern="^(firing|acknowledged|resolved)$"),
    node_id: Optional[str] = None,
    limit: int = 100,
    request: Request = None,
):
    """List active or historical alerts and configured alert rules."""
    alert_list = storage.get_alerts(state=state, node_id=node_id, limit=limit)
    rules = storage.get_all_alert_rules()
    return {
        "alerts": alert_list,
        "rules": rules,
        "active_count": sum(1 for a in alert_list if a.get("state") == "firing"),
    }


@router.post("/api/v1/observatory/alerts/{alert_id}/acknowledge")
def acknowledge_alert_api(
    alert_id: str,
    req: Optional[models.AlertAcknowledgeRequest] = None,
    request: Request = None,
):
    """Acknowledge an active alert."""
    admin_user = get_current_user(request) or "operator"
    success = storage.acknowledge_alert(alert_id, acknowledged_by=admin_user)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert '{alert_id}' not found or already acknowledged/resolved.",
        )
    return {"status": "acknowledged", "alert_id": alert_id, "acknowledged_by": admin_user}


@router.get("/api/v1/observatory/insights")
def get_system_insights_api(request: Request):
    """Retrieve deterministic evidence-backed system health insights and optimizations."""
    return {"insights": analytics.generate_system_insights()}


@router.get("/api/v1/observatory/research")
def get_research_observatory_api(request: Request):
    """Retrieve continuous research, stage checkpoint metrics, and persistent knowledge telemetry."""
    return analytics.get_research_and_knowledge_metrics()


@router.get("/api/v1/observatory/artifacts")
def get_artifacts_observatory_api(request: Request):
    """Retrieve unified deliverables and artifacts management telemetry."""
    return analytics.get_deliverables_and_artifacts_metrics()



@router.get("/api/v1/observatory/export")
def export_telemetry_csv(
    data_type: str = Query("telemetry", pattern="^(telemetry|inference)$"),
    node_id: Optional[str] = None,
    limit: int = Query(1000, ge=10, le=10000),
    request: Request = None,
):
    """Export raw telemetry samples or inference metrics to downloadable CSV."""
    output = io.StringIO()
    writer = csv.writer(output)

    if data_type == "telemetry":
        samples = storage.get_time_series_samples(node_id=node_id, limit=limit)
        writer.writerow([
            "timestamp", "node_id", "cpu_percent", "ram_total_mb", "ram_used_mb",
            "ram_percent", "swap_total_mb", "swap_used_mb", "swap_percent",
            "disk_free_gb", "cpu_temp_c", "battery_percent", "is_ac_online",
            "worker_res_mb", "latency_ms"
        ])
        for s in samples:
            writer.writerow([
                s.get("timestamp"), s.get("node_id"), s.get("cpu_percent"),
                s.get("ram_total_mb"), s.get("ram_used_mb"), s.get("ram_percent"),
                s.get("swap_total_mb"), s.get("swap_used_mb"), s.get("swap_percent"),
                s.get("disk_free_gb"), s.get("cpu_temp_c"), s.get("battery_percent"),
                s.get("is_ac_online"), s.get("worker_res_mb"), s.get("latency_to_controller_ms")
            ])
    else:
        records = storage.query_inference_telemetry(node_id=node_id, limit=limit)
        writer.writerow([
            "created_at", "request_id", "node_id", "model", "status",
            "prompt_tokens", "gen_tokens", "total_tokens", "tokens_per_second",
            "prompt_eval_duration_ms", "eval_duration_ms", "total_duration_ms"
        ])
        for r in records:
            writer.writerow([
                r.get("created_at"), r.get("request_id"), r.get("node_id"),
                r.get("model"), r.get("status"), r.get("prompt_tokens"),
                r.get("gen_tokens"), r.get("total_tokens"), r.get("tokens_per_second"),
                r.get("prompt_eval_duration_ms"), r.get("eval_duration_ms"), r.get("total_duration_ms")
            ])

    output.seek(0)
    filename = f"qwen_observatory_{data_type}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
