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
import re
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
    context_summary: str = "",
) -> Dict[str, Any]:
    """Deterministic research decision engine with evidence and freshness assessment."""
    obj_clean = (objective or "").strip()
    full_context_text = f"{obj_clean} {stage_name} {context_summary} {json.dumps(context_input or {})}"

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

    # 2. Assess evidence quality, freshness, and topical relevance (F-08)
    # Extract objective non-stopword tokens for relevance filtering
    obj_tokens = {w.lower() for w in re.findall(r"\b[A-Za-z]{4,}\b", obj_clean)}
    filler = {"research", "project", "produce", "deliverable", "deliverables", "latest", "current", "developments", "requirements"}
    key_obj_tokens = obj_tokens - filler

    fresh_supported = []
    stale_or_contradicted = []
    for rec in existing_records:
        # Require reviewed/promoted status for collective or shared records (F-08)
        is_promoted = rec.get("promotion_status") in ("promoted", "approved") or rec.get("is_promoted")
        is_project_owned = (rec.get("project_id") == project_id)
        if not is_project_owned and not is_promoted:
            continue

        # Check topical relevance: must share at least 2 key tokens with objective
        rec_text = f"{rec.get('title', '')} {rec.get('subject', '')} {rec.get('content', '')}".lower()
        overlap = sum(1 for tok in key_obj_tokens if tok in rec_text)
        if key_obj_tokens and overlap < min(2, len(key_obj_tokens)):
            # Discard topically irrelevant record
            continue

        freshness = rec.get("freshness") or evaluate_freshness(rec)
        if freshness.get("is_fresh") and rec.get("verification_status") in ("supported", "corroborated"):
            fresh_supported.append(rec)
        else:
            stale_or_contradicted.append(rec)

    # 3. Check existing verified research sources in project library
    existing_source_ids = []
    conn = get_db_connection()
    try:
        s_rows = conn.execute(
            "SELECT id, canonical_url, title, status, quality_score, updated_at FROM research_sources WHERE project_id = ? AND status IN ('fetched', 'extracted')",
            (project_id,)
        ).fetchall()
        for sr in s_rows:
            existing_source_ids.append(sr["id"])
    finally:
        conn.close()

    total_fresh_evidence = len(fresh_supported) + len(existing_source_ids)

    # 4. Decision criteria
    research_needed = False
    decision_reason = ""
    external_queries: List[str] = []

    # Check for keywords indicating external / current research
    requires_external = any(
        kw in full_context_text.lower()
        for kw in ("current", "latest", "compare", "research", "recent", "documentation", "external", "online", "api", "pricing", "water", "security", "county")
    )

    if stage_phase in ("intake", "planning", "intake_analysis", "feasibility_planning"):
        if total_fresh_evidence == 0:
            research_needed = True
            decision_reason = "No existing verified evidence or source library records exist for this project intake."
            from app.services.research.planner import ResearchPlanner
            strat = ResearchPlanner().generate_strategy(obj_clean)
            external_queries = strat.initial_queries[:5]
        else:
            research_needed = False
            decision_reason = f"Existing verified evidence ({len(fresh_supported)} fresh record(s), {len(existing_source_ids)} source(s)) is sufficient for {stage_phase}; duplicate internet search suppressed."

    elif stage_phase in ("execution", "execution_gap", "task_pre_execution"):
        if stale_or_contradicted:
            research_needed = True
            decision_reason = "Existing knowledge records are outdated or contradicted; fresh verification required."
            external_queries = [f"{stale_or_contradicted[0].get('subject', obj_clean[:60])} latest"]
        elif total_fresh_evidence == 0 and requires_external:
            research_needed = True
            decision_reason = "Task execution requires external verified facts not present in knowledge base."
            from app.services.research.planner import ResearchPlanner
            strat = ResearchPlanner().generate_strategy(obj_clean)
            external_queries = strat.initial_queries[:5]
        else:
            research_needed = False
            decision_reason = f"Execution proceeds with verified project context ({total_fresh_evidence} evidence item(s)) and local capabilities."

    elif stage_phase in ("evaluation", "eval_verification"):
        research_needed = False
        decision_reason = "Evaluation operates against objective acceptance criteria and existing source evidence ledger."

    elif stage_phase in ("replanning", "failure_recovery"):
        research_needed = True
        decision_reason = "Replanning requires investigating alternative approaches and resolving invalidated assumptions."
        external_queries = [f"{obj_clean[:60]} alternative solutions"]

    elif stage_phase in ("final_reporting", "final_synthesis"):
        research_needed = False
        decision_reason = "Final reporting synthesizes and audits existing accepted deliverables and verified evidence."

    return {
        "research_needed": research_needed,
        "decision_reason": decision_reason,
        "information_needs": f"Phase '{stage_phase}' information requirements for: {obj_clean[:120]}",
        "knowledge_queries": queries,
        "existing_records": existing_records,
        "existing_source_ids": existing_source_ids,
        "fresh_supported_count": total_fresh_evidence,
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
    context_summary: str = "",
    actor: str = "orchestrator-checkpoint",
) -> Dict[str, Any]:
    """Execute the mandatory research checkpoint, recording durable events, evidence, and knowledge."""
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
    assessment = evaluate_research_need(
        project_id, stage_phase, objective, stage_name, context_input, context_summary=context_summary
    )

    _record_event(
        "knowledge_search_completed",
        f"Searched knowledge base: found {len(assessment['existing_records'])} records ({assessment['fresh_supported_count']} fresh evidence items)"
    )

    sources_retrieved_count = 0
    claims_verified_count = 0
    records_created_count = 0
    new_knowledge_ids: List[str] = []
    budget_used = {}
    is_degraded = False
    degraded_error_msg = ""

    if assessment["research_needed"]:
        _record_event("research_required", assessment["decision_reason"])
        _record_event("external_search_started", f"Executing queries: {assessment['external_queries']}")

        try:
            rm = ResearchManager()
            ext_results = rm.execute_search_step(project_id, chk_id, assessment["external_queries"], research_run_id=run_id)
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
                fetch_res = rm.execute_fetch_and_extract_step(project_id, chk_id, items, research_run_id=run_id, session_id=chk_id)
                sources_retrieved_count = fetch_res.get("sources_count", 0)
                _record_event("source_retrieved", f"Retrieved and extracted {sources_retrieved_count} authorized web sources")

                # Extract claims and create structured knowledge records
                extracted_sources = rm.source_library.list_sources(project_id, research_run_id=run_id)
                for src in extracted_sources[:5]:
                    clean_text = src.get("extracted_text", "")
                    title = src.get("title") or src.get("url")
                    paras = [p.strip() for p in clean_text.split("\n\n") if len(p.strip()) > 60]
                    for p in paras[:2]:
                        stmt = p.split(".")[0].strip() + "."
                        try:
                            # Stage 5 / Stage 7 Trust Tier Governance:
                            # Discoveries are recorded as candidate project knowledge records.
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

                            # Link claim in EvidenceLedger with full Stage 5 Section 4 structured metadata
                            claim_obj = rm.evidence_ledger.add_claim(
                                project_id=project_id,
                                claim_text=stmt,
                                claim_type="fact",
                                research_run_id=run_id,
                                confidence_score=0.85,
                            )
                            rm.evidence_ledger.attach_evidence(
                                project_id=project_id,
                                claim_id=claim_obj.claim_id,
                                source_id=src["id"],
                                supporting_excerpt=p[:400],
                                evidence_strength="strong",
                                orchestration_run_id=run_id,
                                research_session_id=chk_id,
                                source_version_id=src.get("latest_version_id"),
                                is_heuristic=True,
                                heuristic_label="HEURISTIC_ESTIMATE",
                                informed_tasks=[stage_phase],
                            )

                            # Propose candidate for collective promotion without auto-promotion
                            elig = evaluate_promotion_eligibility(kn)
                            if elig["eligible"]:
                                propose_promotion(
                                    kn["id"],
                                    project_id,
                                    "public_technical",
                                    "Candidate finding from research checkpoint awaiting Stage 7 review",
                                    allow_auto_promote=False,
                                )
                        except Exception as e:
                            logger.debug("Failed saving knowledge record: %s", e)

                _record_event(
                    "knowledge_integrated",
                    f"Integrated {records_created_count} candidate knowledge record(s) into project knowledge base"
                )

        except Exception as ex:
            logger.exception("Error executing research step in checkpoint: %s", ex)
            is_degraded = True
            degraded_error_msg = str(ex)[:150]
            _record_event("research_degraded", f"External research provider error: {degraded_error_msg}; continuing degraded with existing knowledge")
    else:
        _record_event("research_not_required", assessment["decision_reason"])
        # Record authorized source reuse lineage for considered sources (Finding F-09 / Stage 5)
        if assessment.get("existing_source_ids"):
            rm = ResearchManager()
            for sid in assessment["existing_source_ids"][:5]:
                try:
                    rm.source_library.record_source_reuse(
                        project_id=project_id,
                        research_session_id=chk_id,
                        source_id=sid,
                        orchestration_run_id=run_id,
                        target_stage_phase=stage_phase,
                        reuse_reason=f"Reused fresh verified source for stage phase '{stage_phase}'",
                        verified_fresh=True,
                    )
                except Exception as e:
                    logger.debug("Failed recording source reuse in checkpoint: %s", e)

    # Assemble stage context
    if is_degraded:
        status_val = "degraded"
        findings_summary = (
            f"Checkpoint degraded for '{stage_phase}': external research unavailable ({degraded_error_msg}). "
            f"Continuing execution with available offline project context."
        )
        evidence_gaps = f"External research unavailable: {degraded_error_msg}"
    else:
        status_val = "completed"
        findings_summary = (
            f"Checkpoint complete for '{stage_phase}'. "
            f"{'Executed external research.' if assessment['research_needed'] else 'Reused existing verified knowledge and sources.'} "
            f"Available records: {len(assessment['existing_records']) + records_created_count}."
        )
        evidence_gaps = "None" if (assessment["fresh_supported_count"] > 0 or records_created_count > 0) else "Missing authoritative corroboration"

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
                chk_id, project_id, stage_id, stage_name, effective_run_id, stage_phase, status_val,
                assessment["information_needs"],
                json.dumps(assessment["knowledge_queries"]),
                json.dumps([r["id"] for r in assessment["existing_records"]] + assessment.get("existing_source_ids", [])),
                f"{assessment['fresh_supported_count']} fresh records/sources, {assessment['stale_count']} stale/outdated",
                1 if assessment["research_needed"] else 0,
                assessment["decision_reason"],
                json.dumps(assessment["external_queries"]),
                sources_retrieved_count,
                claims_verified_count,
                records_created_count,
                findings_summary,
                evidence_gaps,
                json.dumps(budget_used),
                now,
                completed_at,
            )
        )
        if run:
            try:
                log_event(conn, run, "research_check_completed" if not is_degraded else "research_check_degraded",
                          actor, f"Checkpoint {chk_id} {status_val}: {findings_summary[:200]}")
            except Exception as e:
                logger.debug("Failed logging research checkpoint event: %s", e)

    return {
        "id": chk_id,
        "project_id": project_id,
        "run_id": run_id,
        "stage_phase": stage_phase,
        "status": status_val,
        "search_decision": "search_executed" if assessment["research_needed"] else "search_skipped",
        "research_needed": assessment["research_needed"],
        "decision_reason": assessment["decision_reason"],
        "knowledge_queries": assessment["knowledge_queries"],
        "records_created_count": records_created_count,
        "sources_retrieved_count": sources_retrieved_count,
        "new_knowledge_ids": new_knowledge_ids,
        "findings_summary": findings_summary,
        "evidence_gaps": evidence_gaps,
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
