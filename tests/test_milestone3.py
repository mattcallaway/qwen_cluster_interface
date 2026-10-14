import sqlite3
import uuid
import pytest
from app.database import get_db_connection
from app.services import projects as proj_svc
from app.services import metrics as met_svc


def _name(prefix="Test"):
    return f"{prefix} {uuid.uuid4().hex[:8]}"


@pytest.fixture
def sample_project():
    return proj_svc.create_project(
        name=_name("Telemetry Pipeline"),
        description="Build high-throughput metric pipeline",
        owner="admin"
    )


@pytest.fixture
def sample_path(sample_project):
    return proj_svc.create_path(
        sample_project["id"],
        "admin",
        name=_name("Direct Streaming Path"),
        rationale="Minimize batch latency"
    )


def test_define_metric_validation(sample_project, sample_path):
    """Test metric creation, constraints, and invalid parameters."""
    metric = met_svc.define_metric(
        project_id=sample_project["id"],
        name="Latency P99",
        unit="ms",
        metric_type="duration",
        path_id=sample_path["id"],
        target_value=120.0,
        direction_of_improvement="lower_is_better",
        collection_frequency="daily",
        measurement_method="benchmark",
        owner="admin"
    )
    assert metric["name"] == "Latency P99"
    assert metric["unit"] == "ms"
    assert metric["target_value"] == 120.0
    assert metric["direction_of_improvement"] == "lower_is_better"
    assert metric["status"] == "active"

    # Unique name constraint per project
    with pytest.raises(proj_svc.ValidationError, match="already exists"):
        met_svc.define_metric(
            project_id=sample_project["id"],
            name="Latency P99",
            unit="ms",
            owner="admin"
        )

    # Invalid metric type
    with pytest.raises(proj_svc.ValidationError, match="Invalid metric_type"):
        met_svc.define_metric(
            project_id=sample_project["id"],
            name="Bad Metric",
            unit="ms",
            metric_type="nonexistent",
            owner="admin"
        )

    # Invalid direction
    with pytest.raises(proj_svc.ValidationError, match="Invalid direction_of_improvement"):
        met_svc.define_metric(
            project_id=sample_project["id"],
            name="Bad Direction",
            unit="items",
            direction_of_improvement="random",
            owner="admin"
        )


def test_metric_observations_immutability(sample_project):
    """Test observation recording and database-level immutability triggers."""
    metric = met_svc.define_metric(
        project_id=sample_project["id"],
        name="Throughput",
        unit="req/s",
        metric_type="rate",
        target_value=5000.0,
        owner="admin"
    )

    obs = met_svc.record_observation(
        metric_id=metric["id"],
        value=4200.5,
        measured_at="2026-10-14T02:00:00Z",
        source="load_test_run_1",
        collection_method="benchmark",
        verification_status="verified",
        evidence_reference="task_run_123",
        notes="First run under nominal load",
        created_by="admin"
    )
    assert obs["value"] == 4200.5
    assert obs["verification_status"] == "verified"

    # Verify database trigger prevents direct UPDATE
    with get_db_connection() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="metric_observations are immutable"):
            conn.execute("UPDATE metric_observations SET value = 9999 WHERE id = ?", (obs["id"],))

    # Verify database trigger prevents direct DELETE
    with get_db_connection() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="metric_observations are immutable"):
            conn.execute("DELETE FROM metric_observations WHERE id = ?", (obs["id"],))


def test_observation_correction_supersedes(sample_project):
    """Test recording an auditable replacement/superseding observation."""
    metric = met_svc.define_metric(
        project_id=sample_project["id"],
        name="Memory Consumption",
        unit="MB",
        metric_type="duration",
        owner="admin"
    )

    # Original observation (e.g. flawed collection)
    orig = met_svc.record_observation(
        metric_id=metric["id"],
        value=512.0,
        measured_at="2026-10-14T01:00:00Z",
        source="sensor_v1",
        notes="Collected via uncalibrated probe",
        created_by="admin"
    )

    # Correction requires reason
    with pytest.raises(proj_svc.ValidationError, match="Correction reason is required"):
        met_svc.record_observation_correction(
            metric_id=metric["id"],
            supersedes_id=orig["id"],
            correction_reason="",
            value=480.0,
            measured_at="2026-10-14T01:00:00Z",
            created_by="admin"
        )

    # Successful correction
    corr = met_svc.record_observation_correction(
        metric_id=metric["id"],
        supersedes_id=orig["id"],
        correction_reason="Calibrated sensor offsets subtracted",
        value=480.0,
        measured_at="2026-10-14T01:00:00Z",
        source="sensor_v1_calibrated",
        created_by="admin"
    )
    assert corr["supersedes_id"] == orig["id"]
    assert corr["correction_reason"] == "Calibrated sensor offsets subtracted"
    assert corr["value"] == 480.0

    # Query with include_superseded=True returns both
    all_obs = met_svc.list_metric_observations(metric["id"], include_superseded=True)
    assert len(all_obs) == 2

    # Query with include_superseded=False filters out the superseded original
    active_obs = met_svc.list_metric_observations(metric["id"], include_superseded=False)
    assert len(active_obs) == 1
    assert active_obs[0]["id"] == corr["id"]


def test_versioned_forecasts_and_immutability(sample_project, sample_path):
    """Test forecast versioning, mandatory revision reasons, and immutability."""
    metric = met_svc.define_metric(
        project_id=sample_project["id"],
        name="Token Cost",
        unit="USD",
        metric_type="currency",
        target_value=50.0,
        direction_of_improvement="lower_is_better",
        owner="admin"
    )

    # Version 1 initial forecast
    f1 = met_svc.record_forecast(
        metric_id=metric["id"],
        path_id=sample_path["id"],
        predicted_value=75.0,
        method="manual_estimate",
        assumptions="Assumes 15M input tokens at standard pricing",
        confidence_level="medium",
        created_by="admin"
    )
    assert f1["version"] == 1
    assert f1["predicted_value"] == 75.0

    # Revision to Version 2 requires revision_reason
    with pytest.raises(proj_svc.ValidationError, match="revision_reason is required"):
        met_svc.record_forecast(
            metric_id=metric["id"],
            path_id=sample_path["id"],
            predicted_value=60.0,
            method="rolling_average",
            revision_reason="",
            created_by="admin"
        )

    # Successful Version 2
    f2 = met_svc.record_forecast(
        metric_id=metric["id"],
        path_id=sample_path["id"],
        predicted_value=60.0,
        method="rolling_average",
        revision_reason="Prompt compression reduced token volume by 20%",
        created_by="admin"
    )
    assert f2["version"] == 2
    assert f2["predicted_value"] == 60.0

    # Both versions preserved
    versions = met_svc.list_path_forecasts(metric["id"], sample_path["id"])
    assert len(versions) == 2
    assert [v["version"] for v in versions] == [1, 2]

    # Verify forecast immutability via DB triggers
    with get_db_connection() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="path_forecasts are versioned and immutable"):
            conn.execute("UPDATE path_forecasts SET predicted_value = 10.0 WHERE id = ?", (f1["id"],))

    with get_db_connection() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="path_forecasts are versioned and immutable"):
            conn.execute("DELETE FROM path_forecasts WHERE id = ?", (f1["id"],))


def test_transparent_forecasting_methods(sample_project):
    """Test linear trend, rolling average, and rate extrapolation forecasting."""
    metric = met_svc.define_metric(
        project_id=sample_project["id"],
        name="Processed Records",
        unit="records",
        metric_type="count",
        owner="admin"
    )

    # 1. Insufficient data state (<2 observations)
    res_empty = met_svc.calculate_linear_trend_forecast(metric["id"])
    assert res_empty["status"] == "insufficient_data"
    assert "at least 2 observations" in res_empty["reason"]

    # Log 1 observation
    met_svc.record_observation(
        metric_id=metric["id"],
        value=100.0,
        measured_at="2026-10-14T00:00:00Z",
        created_by="admin"
    )
    res_one = met_svc.calculate_linear_trend_forecast(metric["id"])
    assert res_one["status"] == "insufficient_data"

    # Log additional observations with linear progression:
    # 0h: 100, 1h: 200, 2h: 300 -> rate = 100/hour
    met_svc.record_observation(
        metric_id=metric["id"],
        value=200.0,
        measured_at="2026-10-14T01:00:00Z",
        created_by="admin"
    )
    met_svc.record_observation(
        metric_id=metric["id"],
        value=300.0,
        measured_at="2026-10-14T02:00:00Z",
        created_by="admin"
    )

    # Test Linear Trend: project to 2026-10-14T04:00:00Z (4h from start -> expect 500)
    trend_res = met_svc.calculate_linear_trend_forecast(
        metric["id"], target_date_iso="2026-10-14T04:00:00Z"
    )
    assert trend_res["status"] == "ok"
    assert abs(trend_res["predicted_value"] - 500.0) < 0.1

    # Test Rolling Average over window of 2 (should be (200 + 300) / 2 = 250)
    avg_res = met_svc.calculate_rolling_average_forecast(metric["id"], window_size=2)
    assert avg_res["status"] == "ok"
    assert avg_res["predicted_value"] == 250.0

    # Test Rate Extrapolation to 2026-10-14T05:00:00Z (5h from start -> expect 600)
    rate_res = met_svc.calculate_rate_extrapolation_forecast(
        metric["id"], target_date_iso="2026-10-14T05:00:00Z"
    )
    assert rate_res["status"] == "ok"
    assert abs(rate_res["predicted_value"] - 600.0) < 0.1


def test_forecast_evaluation_and_variance(sample_project, sample_path):
    """Test comparing forecast to actual observation and evaluating accuracy."""
    metric = met_svc.define_metric(
        project_id=sample_project["id"],
        name="Inference P90",
        unit="ms",
        metric_type="duration",
        target_value=250.0,
        direction_of_improvement="lower_is_better",
        owner="admin"
    )

    # Record forecast: predicted 300 ms
    forecast = met_svc.record_forecast(
        metric_id=metric["id"],
        path_id=sample_path["id"],
        predicted_value=300.0,
        method="linear_trend",
        assumptions="Based on prelim batch benchmarks",
        created_by="admin"
    )

    # Actual observation: 240 ms (better than target and ahead of forecast!)
    obs = met_svc.record_observation(
        metric_id=metric["id"],
        value=240.0,
        measured_at="2026-10-14T03:00:00Z",
        verification_status="verified",
        created_by="admin"
    )

    # Evaluate forecast against this observation
    evaluation = met_svc.evaluate_forecast(
        forecast_id=forecast["id"],
        actual_observation_id=obs["id"],
        evaluated_by="admin",
        notes="First post-optimization verification"
    )

    assert evaluation["predicted_value"] == 300.0
    assert evaluation["actual_value"] == 240.0
    assert evaluation["variance"] == -60.0
    assert evaluation["absolute_error"] == 60.0
    assert evaluation["percentage_error"] == 20.0  # 60 / 300 = 20%
    assert evaluation["accuracy_score"] == 0.8  # 1.0 - 0.2 = 0.8
    # Lower is better, and 240 <= target 250 -> on_target
    assert evaluation["status_assessment"] == "on_target"


def test_metric_summary_aggregation(sample_project, sample_path):
    """Test get_metric_summary bundles definition, target, actuals, forecasts, and status."""
    metric = met_svc.define_metric(
        project_id=sample_project["id"],
        name="Model Accuracy",
        unit="%",
        metric_type="percentage",
        target_value=95.0,
        direction_of_improvement="higher_is_better",
        owner="admin"
    )

    # Initially: insufficient data
    summary0 = met_svc.get_metric_summary(metric["id"])
    assert summary0["status_assessment"] == "insufficient_data"
    assert summary0["latest_observation"] is None

    # Add forecast: 90%
    fct = met_svc.record_forecast(
        metric_id=metric["id"],
        path_id=sample_path["id"],
        predicted_value=90.0,
        method="manual_estimate",
        created_by="admin"
    )

    # Add actual: 85% (behind forecast)
    obs = met_svc.record_observation(
        metric_id=metric["id"],
        value=85.0,
        created_by="admin"
    )

    summary = met_svc.get_metric_summary(metric["id"])
    assert summary["latest_observation"]["id"] == obs["id"]
    assert summary["latest_forecast"]["id"] == fct["id"]
    assert summary["current_variance"] == -5.0
    assert summary["status_assessment"] == "behind_forecast"


def test_update_and_archive_metric(sample_project):
    """Test modifying targets and archiving metrics."""
    metric = met_svc.define_metric(
        project_id=sample_project["id"],
        name="Error Rate",
        unit="%",
        metric_type="percentage",
        target_value=2.0,
        direction_of_improvement="lower_is_better",
        owner="admin"
    )

    updated = met_svc.update_metric(
        metric_id=metric["id"],
        target_value=1.5,
        description="Tightened error budget"
    )
    assert updated["target_value"] == 1.5
    assert updated["description"] == "Tightened error budget"

    archived = met_svc.archive_metric(metric["id"])
    assert archived["status"] == "archived"

    # Default list excludes archived
    active_metrics = met_svc.list_project_metrics(sample_project["id"], include_archived=False)
    assert not any(m["id"] == metric["id"] for m in active_metrics)

    # include_archived returns it
    all_metrics = met_svc.list_project_metrics(sample_project["id"], include_archived=True)
    assert any(m["id"] == metric["id"] for m in all_metrics)


def test_target_is_optimal_direction(sample_project, sample_path):
    """Test target_is_optimal direction assessment within and outside tolerance."""
    metric = met_svc.define_metric(
        project_id=sample_project["id"],
        name="GPU Temperature",
        unit="C",
        metric_type="other",
        target_value=65.0,
        direction_of_improvement="target_is_optimal",
        owner="admin"
    )

    fct = met_svc.record_forecast(
        metric_id=metric["id"],
        path_id=sample_path["id"],
        predicted_value=66.0,
        method="manual_estimate",
        created_by="admin"
    )

    # Observation right on target (65.5 C is within 10% of 65.0 -> on_target)
    obs_good = met_svc.record_observation(
        metric_id=metric["id"],
        value=65.5,
        created_by="admin"
    )
    eval_good = met_svc.evaluate_forecast(
        forecast_id=fct["id"],
        actual_observation_id=obs_good["id"],
        evaluated_by="admin"
    )
    assert eval_good["status_assessment"] == "on_target"

    # Observation far off (85.0 C is outside tolerance -> at_risk)
    obs_bad = met_svc.record_observation(
        metric_id=metric["id"],
        value=85.0,
        created_by="admin"
    )
    eval_bad = met_svc.evaluate_forecast(
        forecast_id=fct["id"],
        actual_observation_id=obs_bad["id"],
        evaluated_by="admin"
    )
    assert eval_bad["status_assessment"] == "at_risk"
