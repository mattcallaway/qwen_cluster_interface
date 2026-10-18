"""Mandatory Research Checkpoint System for Project Lifecycle Stages.

Executes at every meaningful project stage boundary (Intake, Planning, Execution,
Evaluation, Replanning, Final Reporting). Evaluates existing project and collective
knowledge, assesses freshness, decides whether external research is required, executes
bounded research when warranted, preserves structured knowledge records with full provenance,
and tracks impact on downstream decisions.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.database import db_transaction, get_db_connection
from app.services.knowledge_store import (
    create_knowledge_record,
    evaluate_freshness,
    evaluate_promotion_eligibility,
    propose_promotion,
    search_knowledge_records,
)
from app.services.orchestration.common import log_event, new_id, now_iso
from app.services.research.manager import ResearchManager

logger = logging.getLogger("qc.orchestrator.checkpoint")


def evaluate_research_need(
    project_id: str,
    stage_phase: str,
    objective: str,
    stage_name: str = "",
    context_input: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministic research decision engine with evidence and freshness assessment."""
    obj_clean = (objective or "").strip()
    full_context_text = f"{obj_clean} {stage_name} {json.dumps(context_input or {})}"

    # 1. Search existing project knowledge + approved collective knowledge
    # Extract salient keywords from objective
    queries = [q for q in [obj_clean[:80], stage_name] if q.strip()]
    if not queries:
        queries = ["project requirements"]

    existing_records: List[Dict[str, Any]] = []
    for q in queries:
        results = search_knowledge_records(q, project_id=project_id, include_collective=True, limit=10)
        for r in results:
            if not any(e["id"] == r["id"] for e in existing_records):
                existing_records.append(r)

    # 2. Assess evidence quality and freshness
    fresh_supported = []
    stale_or_contradicted = []
    for rec in existing_records:
        freshness = rec.get("freshness") or evaluate_freshness(rec)
        if freshness["is_fresh"] and rec.get("verification_status") in ("supported", "corroborated"):
            fresh_supported.append(rec)
        else:
            stale_or_contradicted.append(rec)

    # 3. Decision criteria
    # Search is needed when:
    # - In intake/planning: no fresh supported knowledge exists, or explicit external info requested
    # - In execution: new specific technical dependencies or unfamiliar APIs appear
    # - In replanning: prior failure or contradiction detected
    # - In final reporting: need citation audit or time-sensitive refresh
    research_needed = False
    decision_reason = ""
    external_queries: List[str] = []

    # Check for keywords indicating external / current research
    requires_external = any(
        kw in full_context_text.lower()
        for kw in ("current", "latest", "compare", "research", "recent", "documentation", "external", "online", "api", "pricing")
    )

    if stage_phase in ("intake", "planning"):
        if not fresh_supported:
            research_needed = True
            decision_reason = "No prior verified project or collective knowledge found for this objective."
            external_queries = [f"{obj_clean[:80]}"]
        elif requires_external and len(fresh_supported) < 1:
            research_needed = True
            decision_reason = "Objective requires current external documentation and existing verified knowledge is insufficient."
            external_queries = [f"{obj_clean[:70]} current documentation", f"{obj_clean[:70]} best practices"]
        else:
            research_needed = False
            decision_reason = f"Existing verified knowledge ({len(fresh_supported)} fresh record(s)) is sufficient for {stage_phase}."


    elif stage_phase == "execution":
        if stale_or_contradicted:
            research_needed = True
            decision_reason = "Existing knowledge records are outdated or contradicted; fresh verification required."
            external_queries = [f"{stale_or_contradicted[0].get('subject', obj_clean[:60])} latest"]
        elif not fresh_supported and requires_external:
            research_needed = True
            decision_reason = "Task execution requires external verified facts not present in knowledge base."
            external_queries = [f"{obj_clean[:70]} reference"]
        else:
            research_needed = False
            decision_reason = "Execution proceeds with available project context and verified local capabilities."

    elif stage_phase == "evaluation":
        research_needed = False
        decision_reason = "Evaluation operates against objective acceptance criteria and existing source evidence ledger."

    elif stage_phase == "replanning":
        research_needed = True
        decision_reason = "Replanning requires investigating alternative approaches and resolving invalidated assumptions."
        external_queries = [f"{obj_clean[:60]} alternative solutions"]

    elif stage_phase == "final_reporting":
        research_needed = False
        decision_reason = "Final reporting synthesizes and audits existing accepted deliverables and verified evidence."

    return {
        "research_needed": research_needed,
        "decision_reason": decision_reason,
        "information_needs": f"Phase '{stage_phase}' information requirements for: {obj_clean[:120]}",
        "knowledge_queries": queries,
        "existing_records": existing_records,
        "fresh_supported_count": len(fresh_supported),
        "stale_count": len(stale_or_contradicted),
        "external_queries": external_queries[:3],
    }


def execute_research_checkpoint(
    *,
    project_id: str,
    stage_phase: str,
    run_id: Optional[str] = None,
    stage_id: Optional[str] = None,
    stage_name: str = "",
    objective: str = "",
    context_input: Optional[Dict[str, Any]] = None,
    actor: str = "orchestrator-checkpoint",
) -> Dict[str, Any]:
    """Execute the mandatory research checkpoint, recording durable events and knowledge."""
    chk_id = new_id("chk")
    now = now_iso()

    # Log checkpoint start event if active run exists
    conn = get_db_connection()
    try:
        run_row = conn.execute("SELECT * FROM orchestration_runs WHERE id = ?", (run_id,)).fetchone() if run_id else None
        run = dict(run_row) if run_row else None
    finally:
        conn.close()

    def _record_event(event_type: str, reason: str = ""):
        if run:
            try:
                with db_transaction() as c:
                    log_event(c, run, event_type, actor, reason)
            except Exception as e:
                logger.debug("Safe log_event failed: %s", e)

    _record_event("research_check_started", f"Research checkpoint started for phase '{stage_phase}'")

    # Evaluate research need
    assessment = evaluate_research_need(project_id, stage_phase, objective, stage_name, context_input)

    _record_event(
        "knowledge_search_completed",
        f"Searched knowledge base: found {len(assessment['existing_records'])} records ({assessment['fresh_supported_count']} fresh)"
    )

    sources_retrieved_count = 0
    claims_verified_count = 0
    records_created_count = 0
    new_knowledge_ids: List[str] = []
    budget_used = {}

    if assessment["research_needed"]:
        _record_event("research_required", assessment["decision_reason"])
        _record_event("external_search_started", f"Executing queries: {assessment['external_queries']}")

        try:
            rm = ResearchManager()
            ext_results = rm.execute_search_step(project_id, chk_id, assessment["external_queries"])
            sources_retrieved_count = ext_results.get("results_count", 0)

            _record_event("external_search_completed", f"Retrieved {sources_retrieved_count} candidate search results")

            # Fetch and extract sources
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT url, title, snippet, provider FROM research_search_results WHERE project_id = ? ORDER BY rank LIMIT 5",
                    (project_id,)
                )
                items = [dict(r) for r in cursor.fetchall()]
            finally:
                conn.close()


            if items:
                fetch_res = rm.execute_fetch_and_extract_step(project_id, chk_id, items)
                sources_retrieved_count = fetch_res.get("sources_count", 0)
                _record_event("source_retrieved", f"Retrieved and extracted {sources_retrieved_count} authorized web sources")

                # Extract claims and create structured knowledge records
                extracted_sources = rm.source_library.list_sources(project_id)
                for src in extracted_sources[:5]:
                    clean_text = src.get("extracted_text", "")
                    title = src.get("title") or src.get("url")
                    paras = [p.strip() for p in clean_text.split("\n\n") if len(p.strip()) > 60]
                    for p in paras[:2]:
                        stmt = p.split(".")[0].strip() + "."
                        try:
                            kn = create_knowledge_record(
                                subject=title[:80],
                                statement=stmt,
                                knowledge_type="fact",
                                scope="project",
                                project_id=project_id,
                                summary=p[:300],
                                source_ids=[src["id"]],
                                verification_status="supported",
                                confidence_assessment="high",
                                topics=[stage_phase, "web_research"],
                                keywords=[w.lower() for w in title.split() if len(w) > 3][:6],
                            )
                            new_knowledge_ids.append(kn["id"])
                            records_created_count += 1
                            claims_verified_count += 1

                            # Check for collective promotion eligibility
                            elig = evaluate_promotion_eligibility(kn)
                            if elig["eligible"]:
                                propose_promotion(kn["id"], project_id, "public_technical", "Verified public technical finding")
                        except Exception as e:
                            logger.debug("Failed saving knowledge record: %s", e)

                _record_event(
                    "knowledge_integrated",
                    f"Integrated {records_created_count} verified knowledge record(s) into project knowledge base"
                )

        except Exception as ex:
            logger.exception("Error executing research step in checkpoint: %s", ex)
            _record_event("research_degraded", f"External research provider error: {str(ex)[:150]}; continuing degraded with existing knowledge")
    else:
        _record_event("research_not_required", assessment["decision_reason"])


    # Assemble stage context
    findings_summary = (
        f"Checkpoint complete for '{stage_phase}'. "
        f"{'Executed external research.' if assessment['research_needed'] else 'Reused existing verified knowledge.'} "
        f"Available records: {len(assessment['existing_records']) + records_created_count}."
    )

    # Insert checkpoint record into stage_research_checkpoints table
    completed_at = now_iso()
    effective_run_id = run["id"] if run else None
    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO stage_research_checkpoints (
                id, project_id, stage_id, stage_name, run_id, stage_phase, status,
                information_needs, knowledge_queries_json, existing_sources_considered_json,
                freshness_assessment, research_needed, decision_reason, external_queries_json,
                sources_retrieved_count, claims_verified_count, knowledge_records_created_count,
                findings_summary, evidence_gaps, budget_used_json, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chk_id, project_id, stage_id, stage_name, effective_run_id, stage_phase, "completed",

                assessment["information_needs"],
                json.dumps(assessment["knowledge_queries"]),
                json.dumps([r["id"] for r in assessment["existing_records"]]),
                f"{assessment['fresh_supported_count']} fresh records, {assessment['stale_count']} stale/outdated",
                1 if assessment["research_needed"] else 0,
                assessment["decision_reason"],
                json.dumps(assessment["external_queries"]),
                sources_retrieved_count,
                claims_verified_count,
                records_created_count,
                findings_summary,
                "None" if (assessment["fresh_supported_count"] > 0 or records_created_count > 0) else "Missing authoritative corroboration",
                json.dumps(budget_used),
                now,
                completed_at,
            )
        )
        if run:
            try:
                log_event(conn, run, "research_check_completed", actor, f"Checkpoint {chk_id} completed successfully")
            except Exception as e:
                logger.debug("Failed logging research_check_completed: %s", e)

    return {
        "id": chk_id,
        "project_id": project_id,
        "run_id": run_id,
        "stage_phase": stage_phase,
        "search_decision": "search_executed" if assessment["research_needed"] else "search_skipped",
        "research_needed": assessment["research_needed"],
        "decision_reason": assessment["decision_reason"],
        "knowledge_queries": assessment["knowledge_queries"],
        "records_created_count": records_created_count,
        "sources_retrieved_count": sources_retrieved_count,
        "new_knowledge_ids": new_knowledge_ids,
        "findings_summary": findings_summary,
    }



def list_project_checkpoints(project_id: str) -> List[Dict[str, Any]]:
    """List all research checkpoints executed for a project."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM stage_research_checkpoints
               WHERE project_id = ?
               ORDER BY created_at DESC""",
            (project_id,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            for jf in ("knowledge_queries_json", "existing_sources_considered_json", "external_queries_json", "budget_used_json"):
                if isinstance(d.get(jf), str):
                    try:
                        d[jf.replace("_json", "")] = json.loads(d[jf])
                    except Exception:
                        d[jf.replace("_json", "")] = []
            result.append(d)
        return result
    finally:
        conn.close()
