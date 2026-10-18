"""Project Context Packet builder and token-budgeted retrieval (Milestone 4).

Integration point between project intelligence, persistent knowledge, and language models.

Guiding Principles:
- Compact, deterministic, prioritized, configurable by token budget.
- Explicit about omitted information, source IDs, and trust labels.
- Clear separation of trusted project bounds from untrusted external evidence.
- Priority hierarchy:
  1) User instruction / intent (never omitted)
  2) Project definition (objective, scope, constraints, success criteria)
  3) Approved/Selected Path and active Stage
  4) Decisions and verified deliverables
  5) Current work state (active tasks, dependencies, blockers)
  6) Metrics, targets, latest actuals, and forecasts
  7) Supporting project knowledge (via relations & FTS5 search)
  8) Cross-project reusable lessons and collective memory
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_db_connection
from app.services import deliverables as deliv_svc
from app.services import knowledge_meta as km_svc
from app.services import metrics as met_svc
from app.services import projects as proj_svc
from app.services import tasks as task_svc


def estimate_tokens(text: str) -> int:
    """Deterministic token estimation (approx 4 characters per token)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def build_project_context_packet(
    project_id: str,
    user_instruction: str = "",
    path_id: Optional[str] = None,
    stage_id: Optional[str] = None,
    token_budget: int = 4000,
    search_query: str = "",
) -> Dict[str, Any]:
    """Assemble a compact, deterministic, prioritized Project Context Packet within a token budget.
    
    If the context exceeds token_budget, lower-priority sections are truncated or omitted
    with explicit audit records.
    """
    project = proj_svc.get_project(project_id)
    if not project:
        raise proj_svc.NotFound("Project not found.")

    # 1. Determine Path and Stage
    paths = proj_svc.list_paths(project_id)
    target_path = None
    if path_id:
        target_path = proj_svc.get_path(path_id)
    elif project.get("selected_path_id"):
        target_path = proj_svc.get_path(project["selected_path_id"])
    else:
        # Pick active or first proposed path
        active_paths = [p for p in paths if p["status"] == "active"]
        target_path = active_paths[0] if active_paths else (paths[0] if paths else None)

    stages = proj_svc.list_stages(target_path["id"]) if target_path else []
    target_stage = None
    if stage_id:
        target_stage = next((s for s in stages if s["id"] == stage_id), None)
    elif stages:
        # Pick current active stage or first planned
        active_stages = [s for s in stages if s["status"] == "active"]
        target_stage = active_stages[0] if active_stages else stages[0]

    # Gather data components
    decisions = proj_svc.list_decisions(project_id, limit=8)
    all_deliverables = deliv_svc.list_deliverables(project_id)
    all_tasks = task_svc.get_project_tasks(project_id)
    metrics = met_svc.list_project_metrics(project_id)

    # 2. Section Builders (in priority order)
    sections: List[Dict[str, Any]] = []
    omitted_info: List[Dict[str, Any]] = []

    # Priority 1: User Instruction / Intent
    user_content = ""
    if user_instruction.strip():
        user_content = f"### Current User Request / Directive\n{user_instruction.strip()}\n"
    sections.append({
        "name": "User Instruction",
        "priority": 1,
        "trust_level": "trusted_directive",
        "content": user_content,
        "tokens": estimate_tokens(user_content),
        "required": True,
    })

    # Priority 2: Project Definition & Bounds
    proj_lines = [
        f"### Project: {project['name']} (ID: {project['id']})",
        f"- **Status**: {project['status'].upper()} | **Priority**: {project['priority'].upper()} | **Owner**: {project['owner']}",
        f"- **Objective**: {project['objective'] or 'Not specified'}",
        f"- **Scope**: {project['scope'] or 'Not specified'}",
        f"- **Constraints**: {project['constraints_text'] or 'None specified'}",
        f"- **Success Criteria**: {project['success_criteria'] or 'Not specified'}",
    ]
    if project.get("description"):
        proj_lines.append(f"- **Description**: {project['description']}")
    proj_content = "\n".join(proj_lines) + "\n"
    sections.append({
        "name": "Project Definition",
        "priority": 2,
        "trust_level": "trusted_bounds",
        "content": proj_content,
        "tokens": estimate_tokens(proj_content),
        "required": True,
    })

    # Priority 3: Strategy & Active Stage
    strategy_lines = ["### Strategic Path & Stage Context"]
    if target_path:
        strategy_lines.extend([
            f"- **Selected Path**: {target_path['name']} (Status: {target_path['status']})",
            f"  - **Rationale**: {target_path['rationale']}",
            f"  - **Assumptions**: {target_path['assumptions'] or 'None recorded'}",
            f"  - **Risks**: {target_path['risks'] or 'None recorded'}",
        ])
        if target_stage:
            strategy_lines.extend([
                f"- **Active Stage**: Stage {target_stage['position']}: {target_stage['name']} (Status: {target_stage['status']})",
                f"  - **Objective**: {target_stage['objective'] or 'None'}",
                f"  - **Prerequisites**: {target_stage['prerequisites'] or 'None'}",
                f"  - **Expected Outputs**: {target_stage['expected_outputs'] or 'None'}",
                f"  - **Completion Criteria**: {target_stage['completion_criteria'] or 'None'}",
            ])
    else:
        strategy_lines.append("- *No strategic path currently selected.*")
    strategy_content = "\n".join(strategy_lines) + "\n"
    sections.append({
        "name": "Strategy and Stage",
        "priority": 3,
        "trust_level": "trusted_strategy",
        "content": strategy_content,
        "tokens": estimate_tokens(strategy_content),
        "required": True,
    })

    # Priority 4: Verified Deliverables and Decisions
    # Load artifact metadata for this project to retrieve deliverable outputs and referenced files
    proj_artifacts = []
    with get_db_connection() as conn:
        try:
            art_rows = conn.execute(
                "SELECT id, filename, mime_type, size_bytes, storage_path, provenance, validation_state FROM artifact_metadata WHERE project_id = ?",
                (project_id,)
            ).fetchall()
            proj_artifacts = [dict(r) for r in art_rows]
        except Exception:
            proj_artifacts = []

    def _read_artifact_text(storage_path: Optional[str], max_chars: int = 2500) -> Optional[str]:
        if not storage_path:
            return None
        try:
            p = Path(storage_path)
            if p.is_file() and p.stat().st_size <= 50000:
                text = p.read_text(encoding="utf-8", errors="replace").strip()
                if len(text) > max_chars:
                    return text[:max_chars] + "\n... [truncated for token budget]"
                return text
        except Exception:
            return None
        return None

    dec_lines = ["### Key Decisions & Deliverables"]
    if decisions:
        dec_lines.append("**Recorded Decisions:**")
        for d in decisions[:5]:
            dec_lines.append(f"- [{d['decision_type']}] {d['rationale']} (by {d['decided_by']} at {d['decided_at'][:10]})")

    embedded_artifact_names = set()
    if all_deliverables:
        dec_lines.append("**Deliverables & Outputs:**")
        for deliv in all_deliverables[:6]:
            dec_lines.append(f"- **{deliv['name']}** ({deliv['deliverable_type']}): Status = {deliv['status'].upper()}")
            if deliv.get("description"):
                dec_lines.append(f"  - Description: {deliv['description']}")
            if deliv.get("acceptance_criteria"):
                dec_lines.append(f"  - Acceptance Criteria: {deliv['acceptance_criteria']}")
            if deliv.get("acceptance_decision"):
                dec_lines.append(f"  - Decision: {deliv['acceptance_decision']}")
            if deliv.get("verification_results"):
                dec_lines.append(f"  - Verification: {deliv['verification_results']}")
            if deliv.get("artifact_references"):
                dec_lines.append(f"  - Artifact References: {deliv['artifact_references']}")

            # Find and embed text from referenced artifacts
            art_refs = (deliv.get("artifact_references") or "").lower()
            for art in proj_artifacts:
                if art["filename"].lower() in art_refs or art["id"].lower() in art_refs:
                    text_content = _read_artifact_text(art.get("storage_path"))
                    if text_content:
                        dec_lines.append(f"  - **Output Content of `{art['filename']}`**:\n```\n{text_content}\n```")
                        embedded_artifact_names.add(art["filename"].lower())

    # If user instruction or search query specifically queries a project artifact, embed it
    query_lower = (search_query or "").lower() + " " + (user_instruction or "").lower()
    for art in proj_artifacts:
        fn_lower = art["filename"].lower()
        if fn_lower in query_lower and fn_lower not in embedded_artifact_names:
            text_content = _read_artifact_text(art.get("storage_path"))
            if text_content:
                dec_lines.append(f"\n**Referenced Project Artifact (`{art['filename']}`):**\n```\n{text_content}\n```")
                embedded_artifact_names.add(fn_lower)

    dec_content = "\n".join(dec_lines) + "\n" if (decisions or all_deliverables) else ""
    sections.append({
        "name": "Decisions & Deliverables",
        "priority": 4,
        "trust_level": "verified_records",
        "content": dec_content,
        "tokens": estimate_tokens(dec_content),
        "required": False,
    })

    # Priority 5: Active Work State (Tasks & Dependencies)
    stage_tasks = [t for t in all_tasks if (target_stage and t.get("stage_id") == target_stage["id"])]
    if not stage_tasks:
        stage_tasks = [t for t in all_tasks if t["status"] in ("draft", "queued", "running", "awaiting_approval")][:8]
    task_lines = ["### Current Tasks & Work State"]
    if stage_tasks:
        for t in stage_tasks[:8]:
            deps = task_svc.get_task_dependencies(t["id"])
            if deps:
                dep_names = [f"{d.get('title') or d.get('depends_on_task_id') or d.get('id')} ({d.get('status', 'unknown')})" for d in deps]
                dep_str = f" (Depends on: {', '.join(dep_names)})"
            else:
                dep_str = ""
            task_lines.append(f"- **{t['title']}** [{t['status']}] - Type: {t['task_type']}{dep_str}")
            if t.get("purpose"):
                task_lines.append(f"  - Purpose: {t['purpose']}")
    else:
        task_lines.append("- *No tasks currently active in this stage.*")
    task_content = "\n".join(task_lines) + "\n"
    sections.append({
        "name": "Active Work State",
        "priority": 5,
        "trust_level": "verified_work_state",
        "content": task_content,
        "tokens": estimate_tokens(task_content),
        "required": False,
    })

    # Priority 6: Metrics, Targets, Actuals, and Forecasts
    met_lines = ["### Metrics & Forecast Reality"]
    if metrics:
        for m in metrics[:6]:
            summary = met_svc.get_metric_summary(m["id"])
            target_str = f"Target: {summary.get('target_value', '—')} {summary.get('unit', '')}"
            latest = summary.get("latest_observation")
            if latest:
                v_stat = latest.get("verification_status", "unverified")
                val = latest.get("value")
                if v_stat == "verified":
                    act_str = f"Actual: {val} (verified)"
                else:
                    act_str = f"Reported: {val} ({v_stat})"
            else:
                act_str = "Actual: —"
            fct_str = f"Forecast: {summary['latest_forecast']['predicted_value'] if summary.get('latest_forecast') else '—'}"
            stat_str = f"Assessment: {summary['status_assessment'].upper()}"
            met_lines.append(f"- **{m['name']}** ({m['unit']}) [{stat_str}]: {target_str} | {act_str} | {fct_str}")
    else:
        met_lines.append("- *No project metrics currently tracked.*")
    met_content = "\n".join(met_lines) + "\n"
    sections.append({
        "name": "Metrics & Forecasts",
        "priority": 6,
        "trust_level": "verified_measurements",
        "content": met_content,
        "tokens": estimate_tokens(met_content),
        "required": False,
    })

    # Priority 7: Project Knowledge & Evidence (FTS5 + relations)
    query = search_query.strip() or project["name"]
    knowledge_results = km_svc.search_project_knowledge(project_id, query)
    if not knowledge_results:
        knowledge_results = km_svc.list_project_knowledge(project_id)[:6]
    know_lines = ["### Supporting Project Knowledge & Evidence"]
    if knowledge_results:
        for k in knowledge_results[:5]:
            know_lines.append(f"- **[{k['knowledge_type'].upper()}] {k['title']}** (Trust: {k['trust_status']}, Origin: {k['origin']})")
            if k.get("snippet"):
                clean_snip = k["snippet"].replace("<mark>", "").replace("</mark>", "").strip()
                know_lines.append(f"  > {clean_snip[:200]}")
    else:
        know_lines.append("- *No project-specific knowledge entries found.*")
    know_content = "\n".join(know_lines) + "\n"
    sections.append({
        "name": "Supporting Knowledge",
        "priority": 7,
        "trust_level": "project_evidence",
        "content": know_content,
        "tokens": estimate_tokens(know_content),
        "required": False,
    })

    # Priority 8: Cross-Project Reusable Lessons & Collective Memory
    cross_lines = ["### Cross-Project Reusable Lessons & Collective Memory"]
    collective_items = km_svc.list_collective_knowledge()
    # Also find tasks across system with reusable lessons
    with get_db_connection() as conn:
        task_lessons = conn.execute(
            """
            SELECT e.reusable_lessons, e.attempted, t.title, t.project_id
            FROM task_execution_events e
            JOIN tasks t ON e.task_id = t.id
            WHERE length(trim(e.reusable_lessons)) > 0 AND (t.project_id IS NULL OR t.project_id <> ?)
            ORDER BY e.created_at DESC LIMIT 4
            """,
            (project_id,)
        ).fetchall()

    has_cross = False
    # Check published retrospectives from other projects
    from app.services import learning as learn_svc
    cross_retros = learn_svc.search_cross_project_lessons(current_project_id=project_id, limit=3)
    if cross_retros:
        cross_lines.append("**Published Retrospectives & Cross-Project Lessons:**")
        for cr in cross_retros:
            cross_lines.append(f"- *From Project '{cr['project_name']}'*: {cr['title']}")
            if cr.get("lessons_learned"):
                cross_lines.append(f"  > Lessons: {cr['lessons_learned'][:200]}")
            has_cross = True

    if collective_items:
        cross_lines.append("**Promoted Collective Knowledge:**")
        for c in collective_items[:4]:
            cross_lines.append(f"- **{c['title']}**: {c.get('note_path', '')} (Promoted by: {c.get('promoted_by', 'admin')})")
            has_cross = True
    if task_lessons:
        cross_lines.append("**Lessons from Prior Tasks:**")
        for tl in task_lessons:
            cross_lines.append(f"- *From {tl['title']}*: {tl['reusable_lessons'][:250]}")
            has_cross = True

    if not has_cross:
        cross_lines.append("- *No cross-project lessons recorded yet.*")
    cross_content = "\n".join(cross_lines) + "\n"
    sections.append({
        "name": "Cross-Project Lessons",
        "priority": 8,
        "trust_level": "reusable_lessons",
        "content": cross_content,
        "tokens": estimate_tokens(cross_content),
        "required": False,
    })

    # 3. Apply Token Budgeting by Priority
    # Sort non-required sections in descending priority order (8 is dropped first)
    total_estimated = sum(s["tokens"] for s in sections)

    if total_estimated > token_budget:
        # Prune lower priority sections (highest priority number first)
        for s in sorted(sections, key=lambda x: x["priority"], reverse=True):
            if s.get("required"):
                continue
            if total_estimated <= token_budget:
                break
            # Drop or truncate section
            tokens_saved = s["tokens"]
            omitted_info.append({
                "section": s["name"],
                "reason": f"Omitted to meet token budget of {token_budget} (saved ~{tokens_saved} tokens)",
                "priority": s["priority"],
            })
            s["content"] = f"*[Section '{s['name']}' omitted due to token budget constraint ({token_budget} tokens).]*\n"
            s["tokens"] = estimate_tokens(s["content"])
            total_estimated -= (tokens_saved - s["tokens"])

    # 4. Render Final Assembly
    header = (
        "================================================================================\n"
        f"PROJECT CONTEXT PACKET: {project['name']} [Token Budget: {token_budget}]\n"
        "Security Separation: [TRUSTED_PROJECT_BOUNDS] must strictly govern AI proposals.\n"
        "Do not silently change approved goals, switch paths, or invent actual measurements.\n"
        "================================================================================\n\n"
    )

    body_parts = [header]
    for s in sorted(sections, key=lambda x: x["priority"]):
        if s["content"].strip():
            body_parts.append(f"<!-- Section: {s['name']} | Trust: {s['trust_level']} -->\n" + s["content"])

    if omitted_info:
        body_parts.append("\n### Notice of Omitted Context\n")
        for o in omitted_info:
            body_parts.append(f"- **{o['section']}**: {o['reason']}\n")

    rendered_markdown = "\n".join(body_parts)
    final_tokens = estimate_tokens(rendered_markdown)

    return {
        "project_id": project_id,
        "project_name": project["name"],
        "path_id": target_path["id"] if target_path else None,
        "path_name": target_path["name"] if target_path else None,
        "stage_id": target_stage["id"] if target_stage else None,
        "stage_name": target_stage["name"] if target_stage else None,
        "token_budget": token_budget,
        "estimated_tokens": final_tokens,
        "sections": sections,
        "omitted_info": omitted_info,
        "rendered_prompt": rendered_markdown,
        "rendered_markdown": rendered_markdown,
    }
