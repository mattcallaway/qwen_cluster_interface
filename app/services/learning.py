"""Historical Learning, Performance Evaluation, and Project Retrospectives (Milestone 5).

Provides:
- Cross-project lessons retrieval and collective pattern matching
- Model calibration vs human decisions (proposal acceptance and forecast accuracy)
- Deliverable outcome evaluations (post-delivery impact tracking)
- Automated project retrospective synthesis and persistent Markdown knowledge integration
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.database import db_transaction, get_db_connection
from app.services import deliverables as deliv_svc
from app.services import knowledge_meta as km_svc
from app.services import metrics as met_svc
from app.services import projects as proj_svc
from app.services.knowledge import write_note_atomic

logger = logging.getLogger("qwen_collective.learning")

EVALUATION_TYPES = ["immediate_acceptance", "post_delivery_impact", "operational_audit"]
OUTCOME_STATUSES = [
    "exceeded_expectations",
    "met_expectations",
    "partially_met",
    "failed_to_impact",
    "inconclusive",
]
RETROSPECTIVE_STATUSES = ["draft", "reviewed", "published"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 1. Model Calibration & Decision Performance
# ---------------------------------------------------------------------------

def calculate_model_calibration(project_id: Optional[str] = None) -> Dict[str, Any]:
    """Calculate empirical model calibration and forecasting performance.
    
    Evaluates:
      1. Human proposal acceptance rate (accepted / total proposed)
      2. Forecasting error (mean absolute error, percentage error, accuracy score)
      3. Calibration assessment category
    """
    with get_db_connection() as conn:
        # 1. Proposal statistics
        if project_id:
            props = conn.execute(
                "SELECT status, count(*) as cnt FROM project_proposals WHERE project_id = ? GROUP BY status",
                (project_id,)
            ).fetchall()
        else:
            props = conn.execute(
                "SELECT status, count(*) as cnt FROM project_proposals GROUP BY status"
            ).fetchall()

        prop_counts = {r["status"]: r["cnt"] for r in props}
        total_proposals = sum(prop_counts.values())
        accepted_proposals = prop_counts.get("accepted", 0)
        rejected_proposals = prop_counts.get("rejected", 0)
        acceptance_rate = (accepted_proposals / total_proposals) if total_proposals > 0 else 0.0

        # 2. Forecast evaluation statistics
        if project_id:
            evals = conn.execute(
                """
                SELECT fe.*, pf.method 
                FROM forecast_evaluations fe
                JOIN path_forecasts pf ON fe.forecast_id = pf.id
                WHERE fe.path_id IN (SELECT id FROM project_paths WHERE project_id = ?)
                   OR fe.metric_id IN (SELECT id FROM project_metrics WHERE project_id = ?)
                """,
                (project_id, project_id)
            ).fetchall()
        else:
            evals = conn.execute(
                """
                SELECT fe.*, pf.method 
                FROM forecast_evaluations fe
                JOIN path_forecasts pf ON fe.forecast_id = pf.id
                """
            ).fetchall()

        total_evals = len(evals)
        if total_evals > 0:
            mean_accuracy = sum(e["accuracy_score"] for e in evals) / total_evals
            mean_percentage_error = sum((e["percentage_error"] or 0.0) for e in evals) / total_evals
            on_target_count = sum(1 for e in evals if e["status_assessment"] in ("on_target", "ahead_of_forecast"))
            on_target_rate = on_target_count / total_evals
        else:
            mean_accuracy = None
            mean_percentage_error = None
            on_target_rate = None

        # 3. Method breakdown
        method_stats: Dict[str, Dict[str, Any]] = {}
        for e in evals:
            m = e["method"]
            if m not in method_stats:
                method_stats[m] = {"count": 0, "total_accuracy": 0.0}
            method_stats[m]["count"] += 1
            method_stats[m]["total_accuracy"] += e["accuracy_score"]

        for m, stats in method_stats.items():
            stats["mean_accuracy"] = (stats["total_accuracy"] / stats["count"]) if stats["count"] > 0 else 0.0

        # 4. Calibration Assessment
        if total_proposals < 2 and total_evals < 2:
            assessment = "insufficient_history"
            assessment_label = "Insufficient Historical Data"
        elif acceptance_rate >= 0.75 and (mean_accuracy is None or mean_accuracy >= 0.80):
            assessment = "well_calibrated"
            assessment_label = "Well Calibrated (High Acceptance & Accuracy)"
        elif acceptance_rate >= 0.70 and (mean_accuracy is not None and mean_accuracy < 0.60):
            assessment = "overconfident"
            assessment_label = "Overconfident (High Proposal Acceptance but Low Forecast Accuracy)"
        elif acceptance_rate < 0.40:
            assessment = "cautious_or_rejected"
            assessment_label = "Frequent Operator Rejection (Low Proposal Acceptance)"
        else:
            assessment = "moderate_calibration"
            assessment_label = "Moderate Calibration (Acceptable Progress)"

        return {
            "total_proposals": total_proposals,
            "accepted_proposals": accepted_proposals,
            "rejected_proposals": rejected_proposals,
            "proposal_acceptance_rate": round(acceptance_rate, 3),
            "total_evaluations": total_evals,
            "mean_forecast_accuracy": round(mean_accuracy, 3) if mean_accuracy is not None else None,
            "mean_percentage_error": round(mean_percentage_error, 2) if mean_percentage_error is not None else None,
            "on_target_rate": round(on_target_rate, 3) if on_target_rate is not None else None,
            "method_breakdown": method_stats,
            "calibration_assessment": assessment,
            "calibration_assessment_label": assessment_label,
        }


# ---------------------------------------------------------------------------
# 2. Deliverable Outcome Evaluations
# ---------------------------------------------------------------------------

def record_deliverable_evaluation(
    deliverable_id: str,
    evaluation_type: str,
    outcome_status: str,
    impact_summary: str,
    evaluated_by: str,
    project_id: Optional[str] = None,
    metric_observation_id: Optional[str] = None,
    evidence_reference: str = "",
    lessons_learned: str = "",
) -> Dict[str, Any]:
    """Record an empirical outcome evaluation for an accepted deliverable."""
    if evaluation_type not in EVALUATION_TYPES:
        raise proj_svc.ValidationError(f"Invalid evaluation_type '{evaluation_type}'.")
    if outcome_status not in OUTCOME_STATUSES:
        raise proj_svc.ValidationError(f"Invalid outcome_status '{outcome_status}'.")

    clean_summary = (impact_summary or "").strip()
    if not clean_summary:
        raise proj_svc.ValidationError("Impact summary is required.")

    now = _now()
    eval_id = f"deval_{uuid.uuid4().hex[:12]}"

    # Verify deliverable exists
    with get_db_connection() as conn:
        deliv = conn.execute("SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()
        if not deliv:
            raise proj_svc.NotFound("Deliverable not found.")
        actual_project_id = deliv["project_id"]
        if project_id and project_id != actual_project_id:
            raise proj_svc.ValidationError("Deliverable does not belong to specified project.")

    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO deliverable_evaluations (
                id, deliverable_id, project_id, evaluation_type, outcome_status,
                impact_summary, metric_observation_id, evidence_reference,
                lessons_learned, evaluated_by, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eval_id, deliverable_id, actual_project_id, evaluation_type, outcome_status,
                clean_summary, metric_observation_id, evidence_reference.strip(),
                lessons_learned.strip(), evaluated_by, now
            )
        )

        # Record append-only decision
        proj_svc.record_decision(
            conn=conn,
            project_id=actual_project_id,
            decision_type="deliverable_outcome_evaluation",
            rationale=f"Evaluated '{deliv['name']}': {outcome_status} - {clean_summary[:100]}",
            decided_by=evaluated_by,
            subject_id=eval_id,
            from_value=deliv["status"],
            to_value=outcome_status,
        )

    return get_deliverable_evaluation(eval_id)


def get_deliverable_evaluation(evaluation_id: str) -> Dict[str, Any]:
    """Retrieve deliverable evaluation by ID."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM deliverable_evaluations WHERE id = ?", (evaluation_id,)).fetchone()
        if not row:
            raise proj_svc.NotFound("Deliverable evaluation not found.")
        return dict(row)


def list_deliverable_evaluations(project_id: str, deliverable_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List evaluations for a project or specific deliverable."""
    with get_db_connection() as conn:
        if deliverable_id:
            rows = conn.execute(
                """
                SELECT de.*, d.name as deliverable_name 
                FROM deliverable_evaluations de
                JOIN deliverables d ON de.deliverable_id = d.id
                WHERE de.deliverable_id = ?
                ORDER BY de.evaluated_at DESC
                """,
                (deliverable_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT de.*, d.name as deliverable_name 
                FROM deliverable_evaluations de
                JOIN deliverables d ON de.deliverable_id = d.id
                WHERE de.project_id = ?
                ORDER BY de.evaluated_at DESC
                """,
                (project_id,)
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 3. Cross-Project Lessons & Pattern Retrieval
# ---------------------------------------------------------------------------

def search_cross_project_lessons(
    query: str = "",
    current_project_id: Optional[str] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Retrieve historical lessons, published retrospectives, and collective knowledge.
    
    Filters out the current project to ensure genuine cross-project transfer learning.
    """
    clean_q = (query or "").strip().lower()
    results: List[Dict[str, Any]] = []

    with get_db_connection() as conn:
        # 1. Published retrospectives across other projects
        if current_project_id:
            retros = conn.execute(
                """
                SELECT pr.*, p.name as project_name 
                FROM project_retrospectives pr
                JOIN projects p ON pr.project_id = p.id
                WHERE pr.status = 'published' AND pr.project_id <> ?
                ORDER BY pr.updated_at DESC
                """,
                (current_project_id,)
            ).fetchall()
        else:
            retros = conn.execute(
                """
                SELECT pr.*, p.name as project_name 
                FROM project_retrospectives pr
                JOIN projects p ON pr.project_id = p.id
                WHERE pr.status = 'published'
                ORDER BY pr.updated_at DESC
                """
            ).fetchall()

        for r in retros:
            text = f"{r['title']} {r['summary']} {r['lessons_learned']} {r['recommendations']}".lower()
            if not clean_q or clean_q in text:
                results.append({
                    "source_type": "project_retrospective",
                    "id": r["id"],
                    "project_id": r["project_id"],
                    "project_name": r["project_name"],
                    "title": r["title"],
                    "summary": r["summary"],
                    "lessons_learned": r["lessons_learned"],
                    "recommendations": r["recommendations"],
                    "knowledge_path": r["knowledge_path"],
                    "updated_at": r["updated_at"],
                })

        # 2. Promoted collective knowledge entries
        collective = km_svc.list_collective_knowledge()
        for k in collective:
            text = f"{k.get('title', '')} {k.get('summary', '')}".lower()
            if not clean_q or clean_q in text:
                results.append({
                    "source_type": "collective_knowledge",
                    "id": k["id"],
                    "project_id": k.get("project_id"),
                    "project_name": "Collective Memory",
                    "title": k.get("title") or Path(k["note_path"]).stem,
                    "summary": k.get("summary", ""),
                    "lessons_learned": k.get("summary", ""),
                    "recommendations": "",
                    "knowledge_path": k["note_path"],
                    "updated_at": k.get("updated_at", ""),
                })

    return results[:limit]


# ---------------------------------------------------------------------------
# 4. Project Retrospectives
# ---------------------------------------------------------------------------

def create_retrospective(
    project_id: str,
    title: str,
    summary: str,
    executive_summary: str = "",
    key_achievements: str = "",
    challenges_and_blockers: str = "",
    lessons_learned: str = "",
    recommendations: str = "",
    model_performance_summary: Optional[Dict[str, Any]] = None,
    created_by: str = "ai_assistant",
) -> Dict[str, Any]:
    """Create or update a draft project retrospective."""
    clean_title = (title or "").strip()
    if not clean_title or len(clean_title) > 200:
        raise proj_svc.ValidationError("Retrospective title must be between 1 and 200 characters.")
    clean_summary = (summary or "").strip()
    if not clean_summary:
        raise proj_svc.ValidationError("Retrospective summary is required.")

    retro_id = f"retro_{uuid.uuid4().hex[:12]}"
    now = _now()
    perf_json = json.dumps(model_performance_summary or {})

    with get_db_connection() as conn:
        proj = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            raise proj_svc.NotFound("Project not found.")

    with db_transaction() as conn:
        # Check if one with same title already exists
        existing = conn.execute(
            "SELECT id FROM project_retrospectives WHERE project_id = ? AND title = ?",
            (project_id, clean_title)
        ).fetchone()

        if existing:
            retro_id = existing["id"]
            conn.execute(
                """
                UPDATE project_retrospectives
                SET summary = ?, executive_summary = ?, key_achievements = ?,
                    challenges_and_blockers = ?, lessons_learned = ?, recommendations = ?,
                    model_performance_summary = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    clean_summary, executive_summary.strip(), key_achievements.strip(),
                    challenges_and_blockers.strip(), lessons_learned.strip(),
                    recommendations.strip(), perf_json, now, retro_id
                )
            )
        else:
            conn.execute(
                """
                INSERT INTO project_retrospectives (
                    id, project_id, title, summary, executive_summary,
                    key_achievements, challenges_and_blockers, lessons_learned,
                    recommendations, model_performance_summary, status,
                    knowledge_path, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', NULL, ?, ?, ?)
                """,
                (
                    retro_id, project_id, clean_title, clean_summary, executive_summary.strip(),
                    key_achievements.strip(), challenges_and_blockers.strip(), lessons_learned.strip(),
                    recommendations.strip(), perf_json, created_by, now, now
                )
            )

    return get_retrospective(retro_id)


def get_retrospective(retrospective_id: str) -> Dict[str, Any]:
    """Retrieve retrospective by ID with parsed JSON."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM project_retrospectives WHERE id = ?", (retrospective_id,)).fetchone()
        if not row:
            raise proj_svc.NotFound("Retrospective not found.")
        d = dict(row)
        try:
            d["model_performance"] = json.loads(d.get("model_performance_summary") or "{}")
        except Exception:
            d["model_performance"] = {}
        return d


def list_retrospectives(project_id: str) -> List[Dict[str, Any]]:
    """List all retrospectives for a project."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM project_retrospectives WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,)
        ).fetchall()
        res = []
        for r in rows:
            d = dict(r)
            try:
                d["model_performance"] = json.loads(d.get("model_performance_summary") or "{}")
            except Exception:
                d["model_performance"] = {}
            res.append(d)
        return res


def generate_project_retrospective(
    project_id: str,
    user_guidance: str = "",
    created_by: str = "ai_assistant"
) -> Dict[str, Any]:
    """Deterministically synthesize a comprehensive project retrospective from persisted artifacts."""
    project = proj_svc.get_project(project_id)
    if not project:
        raise proj_svc.NotFound("Project not found.")

    paths = proj_svc.list_paths(project_id)
    selected_path = next((p for p in paths if p["id"] == project.get("selected_path_id")), None)
    stages = proj_svc.list_stages(selected_path["id"]) if selected_path else []
    delivs = deliv_svc.list_deliverables(project_id)
    accepted_delivs = [d for d in delivs if d["status"] == "accepted"]
    devals = list_deliverable_evaluations(project_id)
    metrics = met_svc.list_project_metrics(project_id)
    calibration = calculate_model_calibration(project_id)

    title = f"Project Retrospective: {project['name']}"
    summary = (
        f"Lifecycle retrospective evaluating '{project['name']}' against objective: '{project.get('objective', '')}'. "
        f"Analyzed {len(accepted_delivs)}/{len(delivs)} accepted deliverables, {len(metrics)} tracked metrics, "
        f"and {calibration['total_proposals']} AI planning proposals."
    )

    exec_summary = (
        f"Project '{project['name']}' operated under strategy '{selected_path['name'] if selected_path else 'Default'}' "
        f"with status '{project['status']}'. "
        f"Scope covered: {project.get('scope', 'Standard operations')}. "
        f"Constraints observed: {project.get('constraints_text', 'Standard bounds')}."
    )

    achievements = (
        f"- Accepted {len(accepted_delivs)} of {len(delivs)} planned project deliverables.\n"
        f"- Tracked {len(metrics)} quantitative metrics with {calibration['total_evaluations']} verified forecast evaluations.\n"
        f"- Successfully executed work across {len(stages)} strategic delivery stages."
    )

    challenges = (
        f"- Handled {len(delivs) - len(accepted_delivs)} pending or disputed deliverables.\n"
        f"- Encountered {calibration['rejected_proposals']} rejected planning alternatives requiring human redirection.\n"
        f"- Evaluated variance on metrics with an average percentage error of {calibration.get('mean_percentage_error', 0.0)}%."
    )

    lessons = (
        f"1. Early prerequisite diagnostics significantly reduce downstream stage transition delays.\n"
        f"2. Transparent forecasting with versioned models prevents silent scope and schedule drift.\n"
        f"3. Strict human authorization gates ensure AI proposals remain aligned with approved objectives."
    )

    recommendations = (
        f"1. Re-use proven stage breakdown templates for similar upcoming projects.\n"
        f"2. Promote verified deliverable artifacts and operational logs to collective memory.\n"
        f"3. Calibrate next cycle's initial forecasts using actual observation trends from this project."
    )

    return create_retrospective(
        project_id=project_id,
        title=title,
        summary=summary,
        executive_summary=exec_summary,
        key_achievements=achievements,
        challenges_and_blockers=challenges,
        lessons_learned=lessons,
        recommendations=recommendations,
        model_performance_summary=calibration,
        created_by=created_by,
    )


def publish_project_retrospective(
    retrospective_id: str,
    reviewed_by: str,
    promote_to_collective: bool = True
) -> Dict[str, Any]:
    """Publish a retrospective to the project knowledge vault and optionally promote lessons to collective memory."""
    retro = get_retrospective(retrospective_id)
    project_id = retro["project_id"]
    project = proj_svc.get_project(project_id)
    now = _now()

    # 1. Format Markdown document
    k_dir = project.get("knowledge_dir") or f"projects/{proj_svc.slugify(project['name'])}-{project_id[-8:]}"
    vault_path = f"{k_dir}/retrospective.md"

    md_content = f"""# {retro['title']}

> Status: **Published** | Author: {retro['created_by']} | Reviewer: {reviewed_by} | Date: {now[:10]}

## Summary
{retro['summary']}

## Executive Summary
{retro['executive_summary']}

## Key Achievements & Deliverables
{retro['key_achievements']}

## Challenges & Blockers
{retro['challenges_and_blockers']}

## Reusable Lessons Learned
{retro['lessons_learned']}

## Strategic Recommendations
{retro['recommendations']}

## Model Calibration & Planning Performance
- **Proposals Generated:** {retro['model_performance'].get('total_proposals', 0)}
- **Proposals Accepted:** {retro['model_performance'].get('accepted_proposals', 0)} ({round(retro['model_performance'].get('proposal_acceptance_rate', 0.0) * 100, 1)}%)
- **Forecast Accuracy:** {retro['model_performance'].get('mean_forecast_accuracy', 'N/A')}
- **Calibration Assessment:** {retro['model_performance'].get('calibration_assessment_label', 'Standard')}
"""

    # 2. Write Markdown note to vault
    write_note_atomic(vault_path, md_content)

    # 3. Register or update knowledge metadata and relations
    with get_db_connection() as conn:
        existing_km = conn.execute("SELECT id FROM knowledge_metadata WHERE note_path = ?", (vault_path,)).fetchone()

    if not existing_km:
        km_entry = km_svc.register_existing_note(
            note_path=vault_path,
            knowledge_type="lesson_learned",
            origin="system",
            author=reviewed_by,
            project_id=project_id,
            title=retro["title"],
        )
        km_id = km_entry["id"]
        # Promote to verified
        km_svc.review_knowledge(km_id, "verified", reviewer=reviewed_by, note="Published retrospective")
        # Link relation to project
        km_svc.add_relation(kn_id=km_id, target_type="project", target_id=project_id, relation_type="about", user=reviewed_by)
    else:
        km_id = existing_km["id"]

    # 4. If promote_to_collective: Write collective lessons note and promote
    if promote_to_collective:
        coll_path = f"system/{proj_svc.slugify(project['name'])}_lessons.md"
        coll_content = f"""# Lessons Learned: {project['name']}

> Origin: Project Retrospective ({project_id}) | Promoted: {now[:10]}

## Key Lessons
{retro['lessons_learned']}

## Strategic Recommendations
{retro['recommendations']}
"""
        write_note_atomic(coll_path, coll_content)
        with get_db_connection() as conn:
            existing_coll = conn.execute("SELECT id FROM knowledge_metadata WHERE note_path = ?", (coll_path,)).fetchone()
        if not existing_coll:
            coll_km = km_svc.register_existing_note(
                note_path=coll_path,
                knowledge_type="lesson_learned",
                origin="system",
                author=reviewed_by,
                project_id=project_id,
                title=f"Lessons Learned: {project['name']}",
            )
            km_svc.review_knowledge(coll_km["id"], "verified", reviewer=reviewed_by, note="Promoted to collective memory")
            km_svc.promote_to_collective(coll_km["id"], user=reviewed_by, rationale="Valuable cross-project lessons.")

    # 5. Mark retrospective published and log decision
    with db_transaction() as conn:
        conn.execute(
            """
            UPDATE project_retrospectives
            SET status = 'published', knowledge_path = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (vault_path, reviewed_by, now, now, retrospective_id)
        )

        proj_svc.record_decision(
            conn=conn,
            project_id=project_id,
            decision_type="retrospective_publication",
            rationale=f"Published project retrospective '{retro['title']}' to knowledge vault and collective memory.",
            decided_by=reviewed_by,
            subject_id=retrospective_id,
            from_value="draft",
            to_value="published",
            evidence_knowledge_id=km_id,
        )

    return get_retrospective(retrospective_id)
