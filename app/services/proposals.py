"""AI-Assisted Planning, Proposals, and Human Approval Workflow (Milestone 4).

The model proposes; the human decides.
The model must not silently:
- Change approved project goals
- Switch active Paths
- Delete previous decisions
- Alter actual measurements
- Approve its own deliverables
- Expand external execution permissions
- Publish content
- Perform administrative system changes
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.database import db_transaction, get_db_connection
from app.services import deliverables as deliv_svc
from app.services import metrics as met_svc
from app.services import projects as proj_svc
from app.services import tasks as task_svc
from app.services.context_packet import build_project_context_packet

logger = logging.getLogger("qwen_collective.proposals")

PROPOSAL_TYPES = [
    "path_proposal", "task_decomposition", "metric_suggestion", "path_review"
]

PROPOSAL_STATUSES = [
    "proposed", "under_review", "accepted", "rejected", "cancelled"
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_proposal(
    project_id: str,
    proposal_type: str,
    title: str,
    proposed_data: Dict[str, Any],
    summary: str = "",
    path_id: Optional[str] = None,
    stage_id: Optional[str] = None,
    ai_model: str = "qwen",
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    context_packet_summary: str = "",
    created_by: str = "ai_assistant",
) -> Dict[str, Any]:
    """Record a structured draft proposal requiring human review and approval."""
    if proposal_type not in PROPOSAL_TYPES:
        raise proj_svc.ValidationError(f"Invalid proposal_type '{proposal_type}'.")

    clean_title = (title or "").strip()
    if not clean_title or len(clean_title) > 200:
        raise proj_svc.ValidationError("Proposal title must be between 1 and 200 characters.")

    prop_id = f"prop_{uuid.uuid4().hex[:12]}"
    now = _now()
    payload_json = json.dumps(proposed_data)

    with db_transaction() as conn:
        # Check project exists
        proj = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            raise proj_svc.NotFound("Project not found.")

        conn.execute(
            """
            INSERT INTO project_proposals (
                id, project_id, path_id, stage_id, proposal_type, title,
                summary, proposed_data_json, status, ai_model, prompt_tokens,
                completion_tokens, context_packet_summary, review_notes,
                reviewed_by, reviewed_at, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, '', NULL, NULL, ?, ?, ?)
            """,
            (
                prop_id, project_id, path_id, stage_id, proposal_type, clean_title,
                summary.strip(), payload_json, ai_model, prompt_tokens, completion_tokens,
                context_packet_summary.strip(), created_by, now, now
            )
        )

    return get_proposal(prop_id)


def get_proposal(proposal_id: str) -> Dict[str, Any]:
    """Retrieve proposal record by ID with parsed JSON data."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM project_proposals WHERE id = ?", (proposal_id,)).fetchone()
        if not row:
            raise proj_svc.NotFound("Proposal not found.")
        d = dict(row)
        try:
            d["proposed_data"] = json.loads(d.get("proposed_data_json") or "{}")
        except Exception:
            d["proposed_data"] = {}
        return d


def list_project_proposals(
    project_id: str,
    status: Optional[str] = None,
    proposal_type: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List proposals for a project, optionally filtered by status or type."""
    with get_db_connection() as conn:
        query = "SELECT * FROM project_proposals WHERE project_id = ?"
        params: List[Any] = [project_id]

        if status:
            query += " AND status = ?"
            params.append(status)
        if proposal_type:
            query += " AND proposal_type = ?"
            params.append(proposal_type)

        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["proposed_data"] = json.loads(d.get("proposed_data_json") or "{}")
            except Exception:
                d["proposed_data"] = {}
            result.append(d)
        return result


def review_proposal(
    proposal_id: str,
    action: str,
    reviewed_by: str,
    review_notes: str = "",
    edited_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Human review action: accept, reject, edit, or cancel a proposal.
    
    Accepting a proposal creates durable application records:
      - path_proposal -> creates project_paths and path_stages
      - task_decomposition -> creates tasks under the stage
      - metric_suggestion -> creates project_metrics
      - path_review -> logs a review decision
    Logs a proposal_acceptance decision in project_decisions.
    """
    if action not in ("accept", "reject", "cancel", "edit"):
        raise proj_svc.ValidationError(f"Invalid review action '{action}'.")

    now = _now()
    clean_notes = (review_notes or "").strip()

    # Read proposal first
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM project_proposals WHERE id = ?", (proposal_id,)).fetchone()
        if not row:
            raise proj_svc.NotFound("Proposal not found.")
        prop = dict(row)

    if prop["status"] in ("accepted", "rejected", "cancelled") and action != "edit":
        raise proj_svc.ValidationError(f"Proposal is already {prop['status']}.")

    data = edited_data if edited_data is not None else json.loads(prop.get("proposed_data_json") or "{}")
    project_id = prop["project_id"]

    if action == "reject":
        with db_transaction() as conn:
            conn.execute(
                """
                UPDATE project_proposals
                SET status = 'rejected', review_notes = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (clean_notes, reviewed_by, now, now, proposal_id)
            )
    elif action == "cancel":
        with db_transaction() as conn:
            conn.execute(
                """
                UPDATE project_proposals
                SET status = 'cancelled', review_notes = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (clean_notes, reviewed_by, now, now, proposal_id)
            )
    elif action == "edit":
        with db_transaction() as conn:
            conn.execute(
                """
                UPDATE project_proposals
                SET proposed_data_json = ?, review_notes = ?, reviewed_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(data), clean_notes, reviewed_by, now, proposal_id)
            )
    elif action == "accept":
        # 1. Apply durable application records using individual domain service calls
        p_type = prop["proposal_type"]

        if p_type == "path_proposal":
            created_path = proj_svc.create_path(
                project_id=project_id,
                user=reviewed_by,
                name=data.get("name", prop["title"]),
                rationale=data.get("rationale", "Proposed by AI and accepted by human review."),
                description=data.get("description", ""),
                assumptions=data.get("assumptions", ""),
                risks=data.get("risks", ""),
                estimated_cost=data.get("estimated_cost"),
                estimated_effort_hours=data.get("estimated_effort_hours"),
            )
            stages = data.get("stages", [])
            for s in stages:
                proj_svc.create_stage(
                    project_id=project_id,
                    path_id=created_path["id"],
                    user=reviewed_by,
                    name=s.get("name", "New Stage"),
                    objective=s.get("objective", ""),
                    description=s.get("description", ""),
                    prerequisites=s.get("prerequisites", ""),
                    expected_outputs=s.get("expected_outputs", ""),
                    completion_criteria=s.get("completion_criteria", ""),
                    estimated_effort_hours=s.get("estimated_effort_hours"),
                )

        elif p_type == "task_decomposition":
            stage_id = prop.get("stage_id")
            path_id = prop.get("path_id")
            tasks = data.get("tasks", [])
            for t in tasks:
                task_svc.create_task(
                    title=t.get("title", "New Subtask"),
                    description=t.get("description", ""),
                    task_type=t.get("task_type", "general"),
                    project_id=project_id,
                    path_id=path_id,
                    stage_id=stage_id,
                    purpose=t.get("purpose", ""),
                    expected_output=t.get("expected_output", ""),
                    completion_criteria=t.get("completion_criteria", ""),
                )

        elif p_type == "metric_suggestion":
            met_svc.define_metric(
                project_id=project_id,
                name=data.get("name", prop["title"]),
                unit=data.get("unit", "units"),
                metric_type=data.get("metric_type", "count"),
                path_id=prop.get("path_id"),
                description=data.get("description", ""),
                target_value=data.get("target_value"),
                target_date=data.get("target_date"),
                direction_of_improvement=data.get("direction_of_improvement", "higher_is_better"),
                owner=reviewed_by,
            )

        # Record append-only decision and mark proposal accepted
        with db_transaction() as conn:
            proj_svc.record_decision(
                conn=conn,
                project_id=project_id,
                decision_type="proposal_acceptance",
                rationale=clean_notes or f"Accepted AI proposal: '{prop['title']}'",
                decided_by=reviewed_by,
                from_value="proposed",
                to_value="accepted",
                subject_id=proposal_id,
            )
            conn.execute(
                """
                UPDATE project_proposals
                SET status = 'accepted', review_notes = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (clean_notes, reviewed_by, now, now, proposal_id)
            )

    return get_proposal(proposal_id)


# ---------------------------------------------------------------------------
# AI Planning Generators with Deterministic Fallback
# ---------------------------------------------------------------------------

def generate_path_proposals(
    project_id: str,
    user_guidance: str = "",
    token_budget: int = 4000,
    created_by: str = "ai_assistant"
) -> Dict[str, Any]:
    """Generate strategic Path alternatives with deterministic fallback when no LLM is ready."""
    packet = build_project_context_packet(
        project_id, user_instruction=user_guidance, token_budget=token_budget
    )
    project = proj_svc.get_project(project_id)

    # Deterministic fallback proposal content (structured, bounded, and transparent)
    prop_name = f"Iterative Delivery Path ({datetime.now().strftime('%b %d')})"
    proposed_data = {
        "name": prop_name,
        "rationale": f"Deconstructs '{project['name']}' into rapid iterative stages with early verification.",
        "description": "Sequential delivery strategy emphasizing early evidence and bounded task execution.",
        "assumptions": "Resources available for incremental testing at each stage boundary.",
        "risks": "Dependencies between stage handoffs must be managed explicitly.",
        "estimated_effort_hours": 40.0,
        "stages": [
            {
                "name": "Phase 1: Architecture & Baseline Validation",
                "objective": "Establish foundational technical design and verify environment baseline.",
                "prerequisites": "Project scope and constraints defined.",
                "expected_outputs": "Technical specification and baseline verification report.",
                "completion_criteria": "All environment checks pass.",
                "estimated_effort_hours": 12.0
            },
            {
                "name": "Phase 2: Core Implementation & Deliverables",
                "objective": "Build core capabilities and verify deliverable artifacts.",
                "prerequisites": "Phase 1 architecture approved.",
                "expected_outputs": "Functional software artifacts and verification test suite.",
                "completion_criteria": "Deliverables accepted and all tests pass.",
                "estimated_effort_hours": 20.0
            },
            {
                "name": "Phase 3: Hardening & Operational Review",
                "objective": "Measure outcomes against target metrics and capture reusable lessons.",
                "prerequisites": "Core deliverables accepted.",
                "expected_outputs": "Measurement audit and collective knowledge entries.",
                "completion_criteria": "Target metrics verified.",
                "estimated_effort_hours": 8.0
            }
        ]
    }

    return create_proposal(
        project_id=project_id,
        proposal_type="path_proposal",
        title=prop_name,
        proposed_data=proposed_data,
        summary="Strategic alternative proposing a 3-stage delivery lifecycle with explicit completion criteria.",
        context_packet_summary=f"Assembled context with {packet['estimated_tokens']} estimated tokens.",
        created_by=created_by,
        prompt_tokens=packet["estimated_tokens"],
    )


def generate_task_decomposition(
    project_id: str,
    stage_id: str,
    user_guidance: str = "",
    token_budget: int = 4000,
    created_by: str = "ai_assistant"
) -> Dict[str, Any]:
    """Decompose a stage into bounded tasks with deterministic fallback."""
    stage = proj_svc.get_stage(stage_id)
    if not stage:
        raise proj_svc.NotFound("Stage not found.")

    path_id = stage["path_id"]
    packet = build_project_context_packet(
        project_id, user_instruction=user_guidance, path_id=path_id, stage_id=stage_id, token_budget=token_budget
    )

    title = f"Task Decomposition: {stage['name']}"
    proposed_tasks = [
        {
            "title": f"Verify prerequisites for {stage['name']}",
            "description": f"Audit environment readiness against prerequisite: '{stage.get('prerequisites', 'Standard baseline')}'.",
            "task_type": "diagnostic",
            "purpose": "Ensure prerequisites are satisfied before execution begins.",
            "expected_output": "Prerequisite checklist and diagnostic log.",
            "completion_criteria": "No blocking dependency defects found."
        },
        {
            "title": f"Execute implementation for {stage['name']}",
            "description": f"Perform core work to achieve objective: '{stage.get('objective', 'Stage execution')}'.",
            "task_type": "general",
            "purpose": "Generate planned stage outputs and evidence.",
            "expected_output": stage.get("expected_outputs", "Stage deliverables."),
            "completion_criteria": stage.get("completion_criteria", "Verification tests pass.")
        },
        {
            "title": f"Inspect deliverables & evidence for {stage['name']}",
            "description": "Review artifacts, verify completion gates, and log task execution results.",
            "task_type": "audit",
            "purpose": "Prepare evidence for formal stage completion gate.",
            "expected_output": "Stage completion audit report.",
            "completion_criteria": "All deliverables submitted and ready for human acceptance."
        }
    ]

    return create_proposal(
        project_id=project_id,
        path_id=path_id,
        stage_id=stage_id,
        proposal_type="task_decomposition",
        title=title,
        proposed_data={"tasks": proposed_tasks},
        summary=f"Proposes 3 bounded tasks for '{stage['name']}' covering prerequisites, execution, and verification.",
        context_packet_summary=f"Assembled stage packet with {packet['estimated_tokens']} estimated tokens.",
        created_by=created_by,
        prompt_tokens=packet["estimated_tokens"],
    )


def generate_path_review(
    project_id: str,
    path_id: Optional[str] = None,
    stage_id: Optional[str] = None,
    token_budget: int = 4000,
    created_by: str = "ai_assistant"
) -> Dict[str, Any]:
    """Generate a structured Path Review proposal at a stage boundary with recommendations."""
    packet = build_project_context_packet(
        project_id, path_id=path_id, stage_id=stage_id, token_budget=token_budget
    )
    project = proj_svc.get_project(project_id)
    target_path = proj_svc.get_path(path_id) if path_id else (
        proj_svc.get_path(project["selected_path_id"]) if project.get("selected_path_id") else None
    )

    path_name = target_path["name"] if target_path else "Selected Strategy"
    title = f"Path Review: {path_name} (Stage Boundary Evaluation)"

    # Assess deliverables and metrics to formulate recommendations
    delivs = deliv_svc.list_deliverables(project_id)
    accepted_delivs = [d for d in delivs if d["status"] == "accepted"]
    metrics = met_svc.list_project_metrics(project_id)

    recommendation = "continue"
    rationale = "Current stage deliverables and metrics are progressing within acceptable bounds."

    if any(m.get("status_assessment") == "at_risk" for m in [met_svc.get_metric_summary(x["id"]) for x in metrics]):
        recommendation = "reforecast"
        rationale = "At-risk metrics indicate strategic assumptions or forecast targets should be revised."
    elif len(delivs) > 0 and len(accepted_delivs) == len(delivs):
        recommendation = "advance"
        rationale = "All expected stage deliverables have been formally accepted. Ready to advance stage."

    review_data = {
        "path_id": target_path["id"] if target_path else None,
        "path_name": path_name,
        "recommendation": recommendation,
        "summary": rationale,
        "deliverables_total": len(delivs),
        "deliverables_accepted": len(accepted_delivs),
        "metrics_tracked": len(metrics),
        "suggested_actions": [
            "Review accepted deliverable evidence against project success criteria.",
            "Verify actual observations match current forecast expectations.",
            "Confirm next stage prerequisites before dispatching new work."
        ]
    }

    return create_proposal(
        project_id=project_id,
        path_id=target_path["id"] if target_path else None,
        stage_id=stage_id,
        proposal_type="path_review",
        title=title,
        proposed_data=review_data,
        summary=f"Path review recommending '{recommendation}': {rationale}",
        context_packet_summary=f"Assembled context with {packet['estimated_tokens']} estimated tokens.",
        created_by=created_by,
        prompt_tokens=packet["estimated_tokens"],
    )
