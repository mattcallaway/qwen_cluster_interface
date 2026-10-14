"""Metrics definition, actual observations, versioned forecasting, and accuracy tracking (Milestone 3).

Projects provide purpose. Paths preserve strategy. Stages organize work. Tasks produce evidence.
Deliverables demonstrate results. Metrics measure reality. Forecasts test expectations.

Rules:
- Metrics measure actual performance and project outcomes.
- Store actual measurements as immutable, timestamped records.
- Corrections create an auditable superseding record rather than silently rewriting history.
- Forecasts are predictions, not targets and not actuals.
- Preserve historical forecasts: never overwrite a previous forecast because a newer estimate exists.
- Transparent forecasting methods: linear trend, rolling average, rate extrapolation, manual estimate.
- Provide a clear 'insufficient data' state rather than generating precise guesses from inadequate data.
- Forecast accuracy is itself measurable via forecast evaluations.
"""
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import db_transaction, get_db_connection
from app.services.projects import NotFound, ValidationError, NAME_MAX, TEXT_MAX

METRIC_TYPES = [
    "count", "percentage", "rate", "duration", "currency", "quality", "reliability", "other"
]

MEASUREMENT_METHODS = [
    "automated_query", "manual_audit", "task_telemetry", "benchmark", "manual"
]

COLLECTION_FREQUENCIES = [
    "per_task", "daily", "weekly", "stage_gate", "ad_hoc"
]

DIRECTIONS_OF_IMPROVEMENT = [
    "higher_is_better", "lower_is_better", "target_is_optimal"
]

FORECAST_METHODS = [
    "linear_trend", "rolling_average", "rate_extrapolation", "manual_estimate", "model_estimate"
]

CONFIDENCE_LEVELS = ["low", "medium", "high"]

STATUS_ASSESSMENTS = [
    "on_target", "ahead_of_forecast", "behind_forecast", "at_risk", "insufficient_data"
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(val: Optional[str], field: str, max_len: int = TEXT_MAX, required: bool = False) -> str:
    v = (val or "").strip()
    if required and not v:
        raise ValidationError(f"{field} is required.")
    if len(v) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters.")
    return v


def _parse_iso(timestamp_str: str) -> datetime:
    try:
        # Standard Python 3.11+ ISO parser handles 'Z' and offsets
        return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except Exception as e:
        raise ValidationError(f"Invalid timestamp format '{timestamp_str}': must be ISO 8601.") from e


# ---------------------------------------------------------------------------
# Metric Definitions
# ---------------------------------------------------------------------------

def define_metric(
    project_id: str,
    name: str,
    unit: str,
    metric_type: str = "count",
    path_id: Optional[str] = None,
    description: str = "",
    measurement_method: str = "manual",
    source: str = "",
    collection_frequency: str = "ad_hoc",
    target_value: Optional[float] = None,
    target_date: Optional[str] = None,
    direction_of_improvement: str = "higher_is_better",
    owner: str = "admin",
) -> Dict[str, Any]:
    """Define a new project metric with target, units, and measurement parameters."""
    clean_name = _clean(name, "Metric name", NAME_MAX, required=True)
    clean_unit = _clean(unit, "Unit", 50, required=True)
    clean_desc = _clean(description, "Description", TEXT_MAX)
    clean_source = _clean(source, "Source", TEXT_MAX)
    clean_owner = _clean(owner, "Owner", NAME_MAX, required=True)

    if metric_type not in METRIC_TYPES:
        raise ValidationError(f"Invalid metric_type '{metric_type}'. Must be one of {METRIC_TYPES}.")
    if measurement_method not in MEASUREMENT_METHODS:
        raise ValidationError(f"Invalid measurement_method '{measurement_method}'.")
    if collection_frequency not in COLLECTION_FREQUENCIES:
        raise ValidationError(f"Invalid collection_frequency '{collection_frequency}'.")
    if direction_of_improvement not in DIRECTIONS_OF_IMPROVEMENT:
        raise ValidationError(f"Invalid direction_of_improvement '{direction_of_improvement}'.")

    if target_value is not None:
        try:
            target_value = float(target_value)
            if math.isnan(target_value) or math.isinf(target_value):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValidationError("target_value must be a valid numeric value.")

    if target_date:
        _parse_iso(target_date)

    metric_id = f"met_{uuid.uuid4().hex[:12]}"
    now = _now()

    with db_transaction() as conn:
        # Verify project exists
        proj = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            raise NotFound("Project not found.")

        # If path_id is supplied, verify it belongs to this project
        if path_id:
            path = conn.execute("SELECT id, project_id FROM project_paths WHERE id = ?", (path_id,)).fetchone()
            if not path or path["project_id"] != project_id:
                raise ValidationError("path_id must reference a path in the same project.")

        try:
            conn.execute(
                """
                INSERT INTO project_metrics (
                    id, project_id, path_id, name, description, metric_type, unit,
                    measurement_method, source, collection_frequency, target_value,
                    target_date, direction_of_improvement, owner, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    metric_id, project_id, path_id, clean_name, clean_desc, metric_type,
                    clean_unit, measurement_method, clean_source, collection_frequency,
                    target_value, target_date, direction_of_improvement, clean_owner, now, now
                )
            )
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                raise ValidationError(f"A metric named '{clean_name}' already exists in this project.")
            raise

    return get_metric(metric_id)


def get_metric(metric_id: str) -> Dict[str, Any]:
    """Retrieve metric definition by ID."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM project_metrics WHERE id = ?", (metric_id,)).fetchone()
        if not row:
            raise NotFound("Metric not found.")
        return dict(row)


def list_project_metrics(
    project_id: str,
    path_id: Optional[str] = None,
    include_archived: bool = False
) -> List[Dict[str, Any]]:
    """List metrics for a project, optionally filtered by path."""
    with get_db_connection() as conn:
        query = "SELECT * FROM project_metrics WHERE project_id = ?"
        params: List[Any] = [project_id]

        if not include_archived:
            query += " AND status = 'active'"
        if path_id is not None:
            query += " AND (path_id = ? OR path_id IS NULL)"
            params.append(path_id)

        query += " ORDER BY name ASC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_metric(
    metric_id: str,
    description: Optional[str] = None,
    target_value: Optional[float] = None,
    target_date: Optional[str] = None,
    direction_of_improvement: Optional[str] = None,
    collection_frequency: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """Update non-identifying fields of a metric definition."""
    now = _now()
    with db_transaction() as conn:
        row = conn.execute("SELECT * FROM project_metrics WHERE id = ?", (metric_id,)).fetchone()
        if not row:
            raise NotFound("Metric not found.")

        updates: List[str] = []
        params: List[Any] = []

        if description is not None:
            updates.append("description = ?")
            params.append(_clean(description, "Description", TEXT_MAX))
        if target_value is not None:
            try:
                tv = float(target_value)
                if math.isnan(tv) or math.isinf(tv):
                    raise ValueError()
                updates.append("target_value = ?")
                params.append(tv)
            except (ValueError, TypeError):
                raise ValidationError("target_value must be numeric.")
        if target_date is not None:
            if target_date:
                _parse_iso(target_date)
            updates.append("target_date = ?")
            params.append(target_date or None)
        if direction_of_improvement is not None:
            if direction_of_improvement not in DIRECTIONS_OF_IMPROVEMENT:
                raise ValidationError("Invalid direction_of_improvement.")
            updates.append("direction_of_improvement = ?")
            params.append(direction_of_improvement)
        if collection_frequency is not None:
            if collection_frequency not in COLLECTION_FREQUENCIES:
                raise ValidationError("Invalid collection_frequency.")
            updates.append("collection_frequency = ?")
            params.append(collection_frequency)
        if status is not None:
            if status not in ("active", "archived"):
                raise ValidationError("Status must be active or archived.")
            updates.append("status = ?")
            params.append(status)

        if updates:
            updates.append("updated_at = ?")
            params.append(now)
            params.append(metric_id)
            conn.execute(f"UPDATE project_metrics SET {', '.join(updates)} WHERE id = ?", params)

    return get_metric(metric_id)


def archive_metric(metric_id: str) -> Dict[str, Any]:
    """Mark a metric as archived."""
    return update_metric(metric_id, status="archived")


# ---------------------------------------------------------------------------
# Actual Observations (Immutable & Auditable Corrections)
# ---------------------------------------------------------------------------

def record_observation(
    metric_id: str,
    value: float,
    measured_at: Optional[str] = None,
    source: str = "",
    collection_method: str = "",
    verification_status: str = "unverified",
    evidence_reference: str = "",
    notes: str = "",
    created_by: str = "admin",
) -> Dict[str, Any]:
    """Record an immutable actual measurement for a metric.
    
    A completed task must not automatically manufacture an actual measurement.
    Measurements are verified records of reality.
    """
    try:
        val = float(value)
        if math.isnan(val) or math.isinf(val):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValidationError("Observation value must be a valid numeric value.")

    if verification_status not in ("unverified", "verified", "rejected"):
        raise ValidationError(f"Invalid verification_status '{verification_status}'.")

    meas_time = measured_at or _now()
    _parse_iso(meas_time)
    rec_time = _now()

    clean_source = _clean(source, "Source", TEXT_MAX)
    clean_method = _clean(collection_method, "Collection method", 100)
    clean_evidence = _clean(evidence_reference, "Evidence reference", 500)
    clean_notes = _clean(notes, "Notes", TEXT_MAX)
    clean_creator = _clean(created_by, "Created by", NAME_MAX, required=True)

    obs_id = f"obs_{uuid.uuid4().hex[:12]}"

    with db_transaction() as conn:
        # Check metric exists and is active
        metric = conn.execute("SELECT id, status FROM project_metrics WHERE id = ?", (metric_id,)).fetchone()
        if not metric:
            raise NotFound("Metric not found.")

        conn.execute(
            """
            INSERT INTO metric_observations (
                id, metric_id, value, measured_at, recorded_at, source,
                collection_method, verification_status, evidence_reference,
                notes, supersedes_id, correction_reason, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '', ?)
            """,
            (
                obs_id, metric_id, val, meas_time, rec_time, clean_source,
                clean_method, verification_status, clean_evidence,
                clean_notes, clean_creator
            )
        )

    return get_observation(obs_id)


def record_observation_correction(
    metric_id: str,
    supersedes_id: str,
    correction_reason: str,
    value: float,
    measured_at: Optional[str] = None,
    source: str = "",
    collection_method: str = "",
    verification_status: str = "verified",
    evidence_reference: str = "",
    notes: str = "",
    created_by: str = "admin",
) -> Dict[str, Any]:
    """Record an auditable replacement/superseding observation rather than silently rewriting history.
    
    The original observation remains permanently in the database.
    """
    clean_reason = _clean(correction_reason, "Correction reason", TEXT_MAX, required=True)
    try:
        val = float(value)
        if math.isnan(val) or math.isinf(val):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValidationError("Correction value must be a valid numeric value.")

    meas_time = measured_at or _now()
    _parse_iso(meas_time)
    rec_time = _now()

    clean_source = _clean(source, "Source", TEXT_MAX)
    clean_method = _clean(collection_method, "Collection method", 100)
    clean_evidence = _clean(evidence_reference, "Evidence reference", 500)
    clean_notes = _clean(notes, "Notes", TEXT_MAX)
    clean_creator = _clean(created_by, "Created by", NAME_MAX, required=True)

    obs_id = f"obs_{uuid.uuid4().hex[:12]}"

    with db_transaction() as conn:
        orig = conn.execute(
            "SELECT id, metric_id FROM metric_observations WHERE id = ?",
            (supersedes_id,)
        ).fetchone()
        if not orig:
            raise NotFound("Original observation to supersede not found.")
        if orig["metric_id"] != metric_id:
            raise ValidationError("supersedes_id must belong to the same metric.")

        conn.execute(
            """
            INSERT INTO metric_observations (
                id, metric_id, value, measured_at, recorded_at, source,
                collection_method, verification_status, evidence_reference,
                notes, supersedes_id, correction_reason, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obs_id, metric_id, val, meas_time, rec_time, clean_source,
                clean_method, verification_status, clean_evidence,
                clean_notes, supersedes_id, clean_reason, clean_creator
            )
        )

    return get_observation(obs_id)


def get_observation(observation_id: str) -> Dict[str, Any]:
    """Retrieve an observation record by ID."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM metric_observations WHERE id = ?", (observation_id,)).fetchone()
        if not row:
            raise NotFound("Observation not found.")
        return dict(row)


def list_metric_observations(
    metric_id: str,
    include_superseded: bool = True,
    verified_only: bool = False
) -> List[Dict[str, Any]]:
    """List observations for a metric in chronological order.
    
    If include_superseded is False, any observation whose ID is referenced
    by another observation's supersedes_id is filtered out.
    """
    with get_db_connection() as conn:
        query = "SELECT * FROM metric_observations WHERE metric_id = ?"
        params: List[Any] = [metric_id]

        if verified_only:
            query += " AND verification_status = 'verified'"

        query += " ORDER BY measured_at ASC, recorded_at ASC"
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]

        if not include_superseded and rows:
            superseded_ids = {
                r["supersedes_id"] for r in rows if r.get("supersedes_id")
            }
            rows = [r for r in rows if r["id"] not in superseded_ids]

        return rows


def get_latest_observation(
    metric_id: str,
    verified_only: bool = False,
    active_only: bool = True
) -> Optional[Dict[str, Any]]:
    """Retrieve the most recent observation for a metric (filtering out superseded if active_only=True)."""
    observations = list_metric_observations(
        metric_id, include_superseded=not active_only, verified_only=verified_only
    )
    if not observations:
        return None
    return observations[-1]


# ---------------------------------------------------------------------------
# Versioned Forecasts (Predictions, Never Overwritten)
# ---------------------------------------------------------------------------

def record_forecast(
    metric_id: str,
    predicted_value: float,
    method: str,
    assumptions: str = "",
    evidence: str = "",
    confidence_score: Optional[float] = None,
    confidence_level: str = "medium",
    revision_reason: str = "",
    created_by: str = "admin",
    path_id: Optional[str] = None,
    prediction_period: str = "",
    target_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Store a versioned prediction for a metric and optional path.
    
    Never overwrites the previous forecast: auto-increments version.
    If version > 1, revision_reason is mandatory.
    """
    try:
        val = float(predicted_value)
        if math.isnan(val) or math.isinf(val):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValidationError("predicted_value must be a valid numeric value.")

    if method not in FORECAST_METHODS:
        raise ValidationError(f"Invalid method '{method}'. Must be one of {FORECAST_METHODS}.")

    if confidence_level not in CONFIDENCE_LEVELS:
        raise ValidationError(f"Invalid confidence_level '{confidence_level}'.")

    if confidence_score is not None:
        try:
            cs = float(confidence_score)
            if cs < 0.0 or cs > 1.0 or math.isnan(cs):
                raise ValueError()
            confidence_score = cs
        except (ValueError, TypeError):
            raise ValidationError("confidence_score must be a float between 0.0 and 1.0.")

    if target_date:
        _parse_iso(target_date)

    clean_assumptions = _clean(assumptions, "Assumptions", TEXT_MAX)
    clean_evidence = _clean(evidence, "Evidence", TEXT_MAX)
    clean_period = _clean(prediction_period, "Prediction period", 200)
    clean_creator = _clean(created_by, "Created by", NAME_MAX, required=True)
    clean_rev_reason = _clean(revision_reason, "Revision reason", TEXT_MAX)

    now = _now()
    forecast_id = f"fct_{uuid.uuid4().hex[:12]}"

    with db_transaction() as conn:
        # Check metric exists
        metric = conn.execute("SELECT id, project_id FROM project_metrics WHERE id = ?", (metric_id,)).fetchone()
        if not metric:
            raise NotFound("Metric not found.")

        # If path_id is supplied, verify it exists and matches project
        if path_id:
            path = conn.execute("SELECT id, project_id FROM project_paths WHERE id = ?", (path_id,)).fetchone()
            if not path or path["project_id"] != metric["project_id"]:
                raise ValidationError("path_id must reference a path in the same project.")

        # Determine next version for (metric_id, path_id)
        if path_id:
            v_row = conn.execute(
                "SELECT MAX(version) AS max_v FROM path_forecasts WHERE metric_id = ? AND path_id = ?",
                (metric_id, path_id)
            ).fetchone()
        else:
            v_row = conn.execute(
                "SELECT MAX(version) AS max_v FROM path_forecasts WHERE metric_id = ? AND path_id IS NULL",
                (metric_id,)
            ).fetchone()

        max_v = v_row["max_v"] if (v_row and v_row["max_v"] is not None) else 0
        new_version = max_v + 1

        if new_version > 1 and not clean_rev_reason:
            raise ValidationError("revision_reason is required when revising an existing forecast (version > 1).")

        conn.execute(
            """
            INSERT INTO path_forecasts (
                id, metric_id, path_id, version, prediction_period, target_date,
                predicted_value, method, assumptions, evidence, confidence_score,
                confidence_level, revision_reason, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                forecast_id, metric_id, path_id, new_version, clean_period, target_date,
                val, method, clean_assumptions, clean_evidence, confidence_score,
                confidence_level, clean_rev_reason, clean_creator, now
            )
        )

    return get_forecast(forecast_id)


def get_forecast(forecast_id: str) -> Dict[str, Any]:
    """Retrieve forecast record by ID."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM path_forecasts WHERE id = ?", (forecast_id,)).fetchone()
        if not row:
            raise NotFound("Forecast not found.")
        return dict(row)


def list_path_forecasts(
    metric_id: str,
    path_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List all versioned forecasts for a metric (and optional path) in version order."""
    with get_db_connection() as conn:
        if path_id:
            query = "SELECT * FROM path_forecasts WHERE metric_id = ? AND path_id = ? ORDER BY version ASC"
            params = [metric_id, path_id]
        else:
            query = "SELECT * FROM path_forecasts WHERE metric_id = ? ORDER BY version ASC"
            params = [metric_id]

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_latest_forecast(
    metric_id: str,
    path_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Get the latest version forecast for a metric."""
    forecasts = list_path_forecasts(metric_id, path_id)
    if not forecasts:
        return None
    return forecasts[-1]


# ---------------------------------------------------------------------------
# Transparent Forecasting Algorithms
# ---------------------------------------------------------------------------

def calculate_linear_trend_forecast(
    metric_id: str,
    target_date_iso: Optional[str] = None,
    verified_only: bool = False
) -> Dict[str, Any]:
    """Calculate transparent linear trend extrapolation over active observations.
    
    If observations < 2, returns an explicit 'insufficient_data' state.
    """
    observations = list_metric_observations(metric_id, include_superseded=False, verified_only=verified_only)
    if len(observations) < 2:
        return {
            "status": "insufficient_data",
            "reason": f"Linear trend requires at least 2 observations; found {len(observations)}.",
            "method": "linear_trend",
            "observations_count": len(observations),
        }

    # Extract timestamps and values
    t_vals: List[float] = []
    y_vals: List[float] = []
    base_t: Optional[float] = None

    for obs in observations:
        try:
            dt = _parse_iso(obs["measured_at"])
            t = dt.timestamp()
            if base_t is None:
                base_t = t
            t_vals.append(t - base_t)
            y_vals.append(float(obs["value"]))
        except Exception:
            continue

    n = len(t_vals)
    if n < 2:
        return {
            "status": "insufficient_data",
            "reason": "Could not parse sufficient observation timestamps for trend calculation.",
            "method": "linear_trend",
            "observations_count": n,
        }

    # Standard least-squares linear regression: y = m * t + b
    mean_t = sum(t_vals) / n
    mean_y = sum(y_vals) / n

    denom = sum((t - mean_t) ** 2 for t in t_vals)
    if denom == 0:
        # All timestamps identical, fall back to mean
        slope = 0.0
        intercept = mean_y
    else:
        numer = sum((t - mean_t) * (y - mean_y) for t, y in zip(t_vals, y_vals))
        slope = numer / denom
        intercept = mean_y - slope * mean_t

    # Determine target time
    if target_date_iso:
        target_t = _parse_iso(target_date_iso).timestamp() - (base_t or 0.0)
    else:
        # Extrapolate one average observation interval into the future
        avg_interval = (t_vals[-1] - t_vals[0]) / max(1, n - 1)
        target_t = t_vals[-1] + max(avg_interval, 86400.0)

    predicted = slope * target_t + intercept
    return {
        "status": "ok",
        "predicted_value": round(predicted, 4),
        "method": "linear_trend",
        "observations_count": n,
        "assumptions": f"Least-squares linear trend fitted across {n} observations (slope: {slope:.6f}/sec)",
        "confidence_level": "medium" if n >= 4 else "low",
        "target_date": target_date_iso,
    }


def calculate_rolling_average_forecast(
    metric_id: str,
    window_size: int = 3,
    verified_only: bool = False
) -> Dict[str, Any]:
    """Calculate rolling average forecast across the last N active observations."""
    observations = list_metric_observations(metric_id, include_superseded=False, verified_only=verified_only)
    if not observations:
        return {
            "status": "insufficient_data",
            "reason": "Rolling average requires at least 1 observation; found 0.",
            "method": "rolling_average",
            "observations_count": 0,
        }

    window = observations[-max(1, window_size):]
    values = [float(o["value"]) for o in window]
    mean_val = sum(values) / len(values)

    return {
        "status": "ok",
        "predicted_value": round(mean_val, 4),
        "method": "rolling_average",
        "observations_count": len(observations),
        "window_size": len(window),
        "assumptions": f"Simple rolling average over latest {len(window)} observations",
        "confidence_level": "medium" if len(window) >= 3 else "low",
    }


def calculate_rate_extrapolation_forecast(
    metric_id: str,
    target_date_iso: str,
    verified_only: bool = False
) -> Dict[str, Any]:
    """Calculate rate extrapolation between first and last observation projected to target date."""
    if not target_date_iso:
        raise ValidationError("target_date is required for rate extrapolation.")

    target_dt = _parse_iso(target_date_iso)
    observations = list_metric_observations(metric_id, include_superseded=False, verified_only=verified_only)
    if len(observations) < 2:
        return {
            "status": "insufficient_data",
            "reason": f"Rate extrapolation requires at least 2 observations; found {len(observations)}.",
            "method": "rate_extrapolation",
            "observations_count": len(observations),
        }

    first = observations[0]
    last = observations[-1]
    t0 = _parse_iso(first["measured_at"]).timestamp()
    t1 = _parse_iso(last["measured_at"]).timestamp()
    dt = t1 - t0

    if dt <= 0:
        return {
            "status": "insufficient_data",
            "reason": "Earliest and latest observations share identical timestamps; cannot determine rate.",
            "method": "rate_extrapolation",
            "observations_count": len(observations),
        }

    rate_per_sec = (float(last["value"]) - float(first["value"])) / dt
    t_target = target_dt.timestamp()
    predicted = float(last["value"]) + rate_per_sec * (t_target - t1)

    return {
        "status": "ok",
        "predicted_value": round(predicted, 4),
        "method": "rate_extrapolation",
        "observations_count": len(observations),
        "rate_per_day": round(rate_per_sec * 86400.0, 4),
        "assumptions": f"Extrapolated from rate between {first['measured_at']} and {last['measured_at']}",
        "confidence_level": "medium",
        "target_date": target_date_iso,
    }


# ---------------------------------------------------------------------------
# Forecast Evaluation, Variance & Accuracy Tracking
# ---------------------------------------------------------------------------

def evaluate_forecast(
    forecast_id: str,
    actual_observation_id: Optional[str] = None,
    evaluated_by: str = "admin",
    notes: str = ""
) -> Dict[str, Any]:
    """Evaluate a forecast against an actual observation, recording variance and accuracy.
    
    If actual_observation_id is None, the latest active observation for the metric is used.
    Calculates:
      - variance: actual - predicted
      - absolute_error: abs(actual - predicted)
      - percentage_error: (abs(actual - predicted) / abs(predicted)) * 100
      - accuracy_score: 1.0 - normalized error, clamped [0.0, 1.0]
      - status_assessment: on_target, ahead_of_forecast, behind_forecast, at_risk
    """
    clean_evaluator = _clean(evaluated_by, "Evaluator", NAME_MAX, required=True)
    clean_notes = _clean(notes, "Evaluation notes", TEXT_MAX)

    now = _now()
    eval_id = f"fev_{uuid.uuid4().hex[:12]}"

    with db_transaction() as conn:
        f_row = conn.execute("SELECT * FROM path_forecasts WHERE id = ?", (forecast_id,)).fetchone()
        if not f_row:
            raise NotFound("Forecast not found.")
        forecast = dict(f_row)

        metric_id = forecast["metric_id"]
        path_id = forecast.get("path_id")

        m_row = conn.execute("SELECT * FROM project_metrics WHERE id = ?", (metric_id,)).fetchone()
        if not m_row:
            raise NotFound("Metric not found.")
        metric = dict(m_row)

        # Get observation
        if actual_observation_id:
            o_row = conn.execute(
                "SELECT * FROM metric_observations WHERE id = ? AND metric_id = ?",
                (actual_observation_id, metric_id)
            ).fetchone()
            if not o_row:
                raise NotFound("Specified observation not found for this metric.")
            obs = dict(o_row)
        else:
            # Query latest active observation
            active_obs = list_metric_observations(metric_id, include_superseded=False)
            if not active_obs:
                raise ValidationError("Cannot evaluate forecast: no observations exist for this metric.")
            obs = active_obs[-1]
            actual_observation_id = obs["id"]

        predicted = float(forecast["predicted_value"])
        actual = float(obs["value"])
        target = float(metric["target_value"]) if metric.get("target_value") is not None else None
        direction = metric.get("direction_of_improvement", "higher_is_better")

        variance = actual - predicted
        abs_err = abs(actual - predicted)

        if predicted != 0:
            pct_err = round((abs_err / abs(predicted)) * 100.0, 4)
            acc_score = max(0.0, min(1.0, 1.0 - (abs_err / abs(predicted))))
        else:
            pct_err = 0.0 if actual == 0 else 100.0
            acc_score = 1.0 if actual == 0 else 0.0

        acc_score = round(acc_score, 4)

        # Determine status assessment
        if direction == "higher_is_better":
            if target is not None and actual >= target:
                status_assessment = "on_target"
            elif actual >= predicted:
                status_assessment = "ahead_of_forecast"
            elif pct_err and pct_err > 25.0:
                status_assessment = "at_risk"
            else:
                status_assessment = "behind_forecast"
        elif direction == "lower_is_better":
            if target is not None and actual <= target:
                status_assessment = "on_target"
            elif actual <= predicted:
                status_assessment = "ahead_of_forecast"
            elif pct_err and pct_err > 25.0:
                status_assessment = "at_risk"
            else:
                status_assessment = "behind_forecast"
        else:  # target_is_optimal
            tolerance = abs(target * 0.1) if target else 1.0
            if target is not None and abs(actual - target) <= tolerance:
                status_assessment = "on_target"
            elif abs_err <= tolerance:
                status_assessment = "ahead_of_forecast"
            else:
                status_assessment = "at_risk"

        conn.execute(
            """
            INSERT INTO forecast_evaluations (
                id, forecast_id, metric_id, path_id, actual_observation_id,
                target_value, predicted_value, actual_value, variance,
                absolute_error, percentage_error, accuracy_score,
                status_assessment, evaluation_notes, evaluated_by, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eval_id, forecast_id, metric_id, path_id, actual_observation_id,
                target, predicted, actual, variance,
                abs_err, pct_err, acc_score,
                status_assessment, clean_notes, clean_evaluator, now
            )
        )

    return get_forecast_evaluation(eval_id)


def get_forecast_evaluation(evaluation_id: str) -> Dict[str, Any]:
    """Retrieve a forecast evaluation record by ID."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM forecast_evaluations WHERE id = ?", (evaluation_id,)).fetchone()
        if not row:
            raise NotFound("Forecast evaluation not found.")
        return dict(row)


def list_forecast_evaluations(
    metric_id: Optional[str] = None,
    path_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List historical forecast evaluations."""
    with get_db_connection() as conn:
        query = "SELECT * FROM forecast_evaluations WHERE 1=1"
        params: List[Any] = []
        if metric_id:
            query += " AND metric_id = ?"
            params.append(metric_id)
        if path_id:
            query += " AND path_id = ?"
            params.append(path_id)

        query += " ORDER BY evaluated_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Metric Summary & Visual Reporting Aggregate
# ---------------------------------------------------------------------------

def get_metric_summary(metric_id: str) -> Dict[str, Any]:
    """Bundle metric definition, latest observation, forecasts, variance, and status assessment."""
    metric = get_metric(metric_id)
    observations = list_metric_observations(metric_id, include_superseded=False)
    all_observations = list_metric_observations(metric_id, include_superseded=True)
    forecasts = list_path_forecasts(metric_id)
    evaluations = list_forecast_evaluations(metric_id=metric_id)

    latest_obs = observations[-1] if observations else None
    latest_fct = forecasts[-1] if forecasts else None
    original_fct = forecasts[0] if forecasts else None
    latest_eval = evaluations[0] if evaluations else None

    # Calculate status assessment and variance dynamically if latest observation & forecast exist
    variance: Optional[float] = None
    percentage_error: Optional[float] = None
    accuracy_score: Optional[float] = None
    status_assessment = "insufficient_data"

    if latest_obs and latest_fct:
        actual = float(latest_obs["value"])
        predicted = float(latest_fct["predicted_value"])
        target = float(metric["target_value"]) if metric.get("target_value") is not None else None
        direction = metric.get("direction_of_improvement", "higher_is_better")

        variance = actual - predicted
        abs_err = abs(actual - predicted)
        if predicted != 0:
            percentage_error = round((abs_err / abs(predicted)) * 100.0, 2)
            accuracy_score = round(max(0.0, min(1.0, 1.0 - (abs_err / abs(predicted)))), 4)
        else:
            percentage_error = 0.0 if actual == 0 else 100.0
            accuracy_score = 1.0 if actual == 0 else 0.0

        if direction == "higher_is_better":
            if target is not None and actual >= target:
                status_assessment = "on_target"
            elif actual >= predicted:
                status_assessment = "ahead_of_forecast"
            elif percentage_error and percentage_error > 25.0:
                status_assessment = "at_risk"
            else:
                status_assessment = "behind_forecast"
        elif direction == "lower_is_better":
            if target is not None and actual <= target:
                status_assessment = "on_target"
            elif actual <= predicted:
                status_assessment = "ahead_of_forecast"
            elif percentage_error and percentage_error > 25.0:
                status_assessment = "at_risk"
            else:
                status_assessment = "behind_forecast"
        else:
            tolerance = abs(target * 0.1) if target else 1.0
            if target is not None and abs(actual - target) <= tolerance:
                status_assessment = "on_target"
            else:
                status_assessment = "at_risk"
    elif latest_obs:
        # Have observations but no forecast
        actual = float(latest_obs["value"])
        target = float(metric["target_value"]) if metric.get("target_value") is not None else None
        direction = metric.get("direction_of_improvement", "higher_is_better")
        if target is not None:
            if direction == "higher_is_better" and actual >= target:
                status_assessment = "on_target"
            elif direction == "lower_is_better" and actual <= target:
                status_assessment = "on_target"
            else:
                status_assessment = "behind_forecast"
        else:
            status_assessment = "insufficient_data"

    return {
        **metric,
        "latest_observation": latest_obs,
        "observations_count": len(observations),
        "total_observations_recorded": len(all_observations),
        "latest_forecast": latest_fct,
        "original_forecast": original_fct,
        "forecasts_count": len(forecasts),
        "latest_evaluation": latest_eval,
        "evaluations_count": len(evaluations),
        "current_variance": variance,
        "percentage_error": percentage_error,
        "accuracy_score": accuracy_score,
        "status_assessment": status_assessment,
    }
