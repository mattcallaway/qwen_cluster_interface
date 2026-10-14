"""Milestone 3 HTTP routes tests: Metrics, Projections, Forecasting, and Accuracy."""
import re
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from app.auth import create_session_token
from app.main import app
from app.services import metrics as met_svc
from app.services import projects as ps

CSRF_RE = re.compile(r'name="csrf_token" value="([0-9a-f]+)"')


@pytest.fixture
def client():
    c = TestClient(app)
    c.cookies.set("qc_session", create_session_token("admin"))
    return c


def _csrf(client, url="/projects"):
    r = client.get(url)
    assert r.status_code == 200
    m = CSRF_RE.search(r.text)
    assert m, "page should embed a CSRF token"
    return m.group(1)


@pytest.fixture
def active_project_setup():
    pid = ps.create_project(f"Route M3 {uuid.uuid4().hex[:8]}", owner="admin", objective="Metrics routes")["id"]
    path = ps.create_path(pid, "admin", name="Path Route M3", rationale="Strategy rationale")
    ps.change_path_status(pid, path["id"], "approved", "admin", "Approved")
    ps.select_path(pid, path["id"], "admin", "Selected")
    ps.change_project_status(pid, "active", "admin", "Active")
    return {"project_id": pid, "path_id": path["id"]}


def test_metrics_tab_renders_cleanly(client, active_project_setup):
    """Test GET /projects/{id}?tab=metrics returns 200 and renders tab components."""
    pid = active_project_setup["project_id"]
    res = client.get(f"/projects/{pid}?tab=metrics")
    assert res.status_code == 200
    assert "Defined Metrics" in res.text
    assert "Define New Metric" in res.text
    assert "id=\"tab-metrics\"" in res.text


def test_metric_create_and_update_route(client, active_project_setup):
    """Test defining a new metric and updating its target."""
    pid = active_project_setup["project_id"]
    path_id = active_project_setup["path_id"]
    token = _csrf(client, f"/projects/{pid}?tab=metrics")

    # 1. Define metric
    res = client.post(
        f"/projects/{pid}/metrics/create",
        data={
            "csrf_token": token,
            "name": "Pipeline Throughput",
            "unit": "req/s",
            "metric_type": "rate",
            "path_id": path_id,
            "target_value": "2500",
            "target_date": "2026-12-01",
            "direction_of_improvement": "higher_is_better",
            "collection_frequency": "daily",
            "measurement_method": "benchmark",
            "description": "Peak sustained throughput",
        },
        follow_redirects=False
    )
    assert res.status_code == 302
    assert "msg=" in res.headers["location"]

    # Verify metric exists
    metrics = met_svc.list_project_metrics(pid)
    assert len(metrics) == 1
    m = metrics[0]
    assert m["name"] == "Pipeline Throughput"
    assert m["target_value"] == 2500.0

    # 2. Update metric
    token = _csrf(client, f"/projects/{pid}?tab=metrics")
    res_update = client.post(
        f"/projects/{pid}/metrics/{m['id']}/update",
        data={
            "csrf_token": token,
            "target_value": "3000",
            "description": "Updated throughput target",
            "direction_of_improvement": "higher_is_better",
            "collection_frequency": "daily",
            "target_date": "",
        },
        follow_redirects=False
    )
    assert res_update.status_code == 302
    m_updated = met_svc.get_metric(m["id"])
    assert m_updated["target_value"] == 3000.0
    assert m_updated["description"] == "Updated throughput target"


def test_observation_create_and_correction_flow(client, active_project_setup):
    """Test logging an actual observation and subsequently recording an auditable correction."""
    pid = active_project_setup["project_id"]
    metric = met_svc.define_metric(
        project_id=pid,
        name="Latency Test",
        unit="ms",
        metric_type="duration",
        owner="admin"
    )

    token = _csrf(client, f"/projects/{pid}?tab=metrics")

    # 1. Log observation
    res_obs = client.post(
        f"/projects/{pid}/metrics/{metric['id']}/observations/create",
        data={
            "csrf_token": token,
            "value": "150.5",
            "measured_at": "2026-10-14T02:00:00Z",
            "source": "automated_probe",
            "collection_method": "telemetry",
            "verification_status": "verified",
            "evidence_reference": "run_99",
            "notes": "First measurement",
        },
        follow_redirects=False
    )
    assert res_obs.status_code == 302
    obs_list = met_svc.list_metric_observations(metric["id"])
    assert len(obs_list) == 1
    first_obs = obs_list[0]
    assert first_obs["value"] == 150.5

    # 2. Record auditable correction
    token = _csrf(client, f"/projects/{pid}?tab=metrics")
    res_corr = client.post(
        f"/projects/{pid}/metrics/{metric['id']}/observations/correct",
        data={
            "csrf_token": token,
            "supersedes_id": first_obs["id"],
            "correction_reason": "Clock skew adjustment",
            "value": "142.0",
            "measured_at": "2026-10-14T02:00:00Z",
            "source": "calibrated_probe",
            "collection_method": "telemetry",
            "verification_status": "verified",
            "evidence_reference": "run_99_recalibrated",
            "notes": "Corrected for clock offset",
        },
        follow_redirects=False
    )
    assert res_corr.status_code == 302
    all_obs = met_svc.list_metric_observations(metric["id"], include_superseded=True)
    assert len(all_obs) == 2
    active_obs = met_svc.list_metric_observations(metric["id"], include_superseded=False)
    assert len(active_obs) == 1
    assert active_obs[0]["value"] == 142.0
    assert active_obs[0]["supersedes_id"] == first_obs["id"]


def test_forecast_create_and_automated_generation(client, active_project_setup):
    """Test manual versioned forecast creation and automated linear trend generation."""
    pid = active_project_setup["project_id"]
    path_id = active_project_setup["path_id"]
    metric = met_svc.define_metric(
        project_id=pid,
        name="Disk Usage",
        unit="GB",
        metric_type="duration",
        path_id=path_id,
        target_value=100.0,
        direction_of_improvement="lower_is_better",
        owner="admin"
    )

    token = _csrf(client, f"/projects/{pid}?tab=metrics")

    # 1. Manual Forecast v1
    res_f1 = client.post(
        f"/projects/{pid}/metrics/{metric['id']}/forecasts/create",
        data={
            "csrf_token": token,
            "predicted_value": "85.0",
            "method": "manual_estimate",
            "path_id": path_id,
            "confidence_level": "medium",
            "assumptions": "Conservative baseline usage",
            "evidence": "Historical trend notes",
            "revision_reason": "",
            "target_date": "",
            "prediction_period": "Q4",
            "confidence_score": "0.75",
        },
        follow_redirects=False
    )
    assert res_f1.status_code == 302
    forecasts = met_svc.list_path_forecasts(metric["id"], path_id)
    assert len(forecasts) == 1
    assert forecasts[0]["version"] == 1
    assert forecasts[0]["predicted_value"] == 85.0

    # 2. Automated Generation with Insufficient Data (<2 observations)
    token = _csrf(client, f"/projects/{pid}?tab=metrics")
    res_gen_fail = client.post(
        f"/projects/{pid}/metrics/{metric['id']}/forecasts/generate",
        data={
            "csrf_token": token,
            "method": "linear_trend",
            "path_id": path_id,
            "target_date": "2026-10-15",
            "window_size": "3",
            "revision_reason": "Automated update",
        },
        follow_redirects=False
    )
    assert res_gen_fail.status_code == 302
    # Verify redirected with error parameter
    parsed = urlparse(res_gen_fail.headers["location"])
    params = parse_qs(parsed.query)
    assert "err" in params
    assert "Insufficient data" in params["err"][0]

    # 3. Add observations and test successful automated rolling average
    met_svc.record_observation(metric["id"], 50.0, measured_at="2026-10-14T01:00:00Z")
    met_svc.record_observation(metric["id"], 60.0, measured_at="2026-10-14T02:00:00Z")

    token = _csrf(client, f"/projects/{pid}?tab=metrics")
    res_gen_ok = client.post(
        f"/projects/{pid}/metrics/{metric['id']}/forecasts/generate",
        data={
            "csrf_token": token,
            "method": "rolling_average",
            "path_id": path_id,
            "target_date": "",
            "window_size": "2",
            "revision_reason": "Recalculated with fresh observations",
        },
        follow_redirects=False
    )
    assert res_gen_ok.status_code == 302
    forecasts2 = met_svc.list_path_forecasts(metric["id"], path_id)
    assert len(forecasts2) == 2
    f2 = forecasts2[-1]
    assert f2["version"] == 2
    assert f2["method"] == "rolling_average"
    assert f2["predicted_value"] == 55.0  # mean of 50 and 60


def test_forecast_evaluate_route(client, active_project_setup):
    """Test evaluating a forecast against actual observations."""
    pid = active_project_setup["project_id"]
    path_id = active_project_setup["path_id"]
    metric = met_svc.define_metric(
        project_id=pid,
        name="Response Time",
        unit="ms",
        target_value=200.0,
        direction_of_improvement="lower_is_better",
        owner="admin"
    )

    fct = met_svc.record_forecast(
        metric_id=metric["id"],
        path_id=path_id,
        predicted_value=180.0,
        method="manual_estimate",
        created_by="admin"
    )

    obs = met_svc.record_observation(
        metric_id=metric["id"],
        value=175.0,
        created_by="admin"
    )

    token = _csrf(client, f"/projects/{pid}?tab=metrics")
    res = client.post(
        f"/projects/{pid}/metrics/{metric['id']}/forecasts/{fct['id']}/evaluate",
        data={
            "csrf_token": token,
            "actual_observation_id": obs["id"],
            "evaluation_notes": "Benchmark verification run",
        },
        follow_redirects=False
    )
    assert res.status_code == 302
    evals = met_svc.list_forecast_evaluations(metric_id=metric["id"])
    assert len(evals) == 1
    ev = evals[0]
    assert ev["forecast_id"] == fct["id"]
    assert ev["predicted_value"] == 180.0
    assert ev["actual_value"] == 175.0
    assert ev["status_assessment"] == "on_target"
    assert ev["accuracy_score"] > 0.95


def test_csrf_protection_on_metric_routes(client, active_project_setup):
    """Verify that omitting or tampering with CSRF tokens is blocked."""
    pid = active_project_setup["project_id"]
    res = client.post(
        f"/projects/{pid}/metrics/create",
        data={
            "csrf_token": "invalid-token",
            "name": "Bypassed Metric",
            "unit": "req",
        },
        follow_redirects=False
    )
    assert res.status_code == 302
    assert "err=" in res.headers["location"]
    # Verify no metric created
    assert len(met_svc.list_project_metrics(pid)) == 0
