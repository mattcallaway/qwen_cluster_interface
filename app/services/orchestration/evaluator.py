"""Deterministic acceptance evaluation. "Process exited 0" is NOT acceptance; the output must pass checks.

Three-layer correctness model (Finding F-12):
1. SchemaCheck: data shape, references, parsing, metadata
2. ExecutionCheck: trusted worker receipt, actual tool exit code, metrics, stderr, task outcome
3. GoalCheck: testable user acceptance criteria, output behavior, evidence, review
Decision: ACCEPTED | REJECTED | BLOCKED | NEEDS_HUMAN_REVIEW

AI review is never used here as an authority on facts. Model output is validated structurally and labelled.
"""
from __future__ import annotations

import ast
from contextlib import closing
import csv
import io
import json
import logging
from pathlib import Path
import re
from datetime import date
from typing import Any, Dict, List, Optional, Union

from app.config import settings
from app.database import get_db_connection
from app.services.orchestration import script_library
from app.services.orchestration.common import jload
from app.services.orchestration.task_graph import scan_text_for_injection
from app.services.orchestration.verification import DeterministicVerificationEngine

logger = logging.getLogger("qc.orchestrator.evaluator")

_ENVELOPE = {"execution_class", "isolation_mechanism", "stdout", "stderr", "returncode", "artifacts", "exit_code"}


def _check(name: str, passed: bool, detail: str = "", layer: str = "schema") -> Dict[str, Any]:
    """Uniform check representation preserving the 3-layer architecture."""
    return {"name": name, "passed": bool(passed), "detail": str(detail)[:300], "layer": layer}


def extract_payload(result_data: Dict[str, Any]) -> Dict[str, Any]:
    """The structured JSON a script printed (merged into the result by the worker)."""
    if not isinstance(result_data, dict):
        return {}
    if "schema" in result_data:
        return {k: v for k, v in result_data.items() if k not in _ENVELOPE}
    for line in str(result_data.get("stdout", "")).splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                d = json.loads(line)
                if isinstance(d, dict):
                    return d
            except ValueError:
                pass
    return {}


def executing_node(task_id: str) -> Optional[str]:
    """Which authenticated worker actually held the (completed) lease for this task."""
    c = get_db_connection()
    try:
        row = c.execute("""SELECT worker_id FROM worker_task_leases WHERE task_id = ?
                           ORDER BY (status='completed') DESC, leased_at DESC LIMIT 1""", (task_id,)).fetchone()
        return row["worker_id"] if row else None
    finally:
        c.close()


def evaluate_script_task(orch: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    """3-layer evaluation for worker-executed script tasks."""
    action = jload(orch["action_json"])
    script_id = action.get("script_id")
    rd = task.get("result_data") or {}

    schema_checks: List[Dict[str, Any]] = []
    execution_checks: List[Dict[str, Any]] = []
    goal_checks: List[Dict[str, Any]] = []

    # 1. Execution checks
    execution_checks.append(_check("task_completed", task["status"] == "completed", f"status={task['status']}", layer="execution"))
    execution_checks.append(_check("exit_code_zero", rd.get("returncode", 0) == 0, f"returncode={rd.get('returncode')}", layer="execution"))
    node = executing_node(task["id"])
    execution_checks.append(_check("executed_on_expected_node", node is not None and node == orch["dispatched_node"],
                                  f"leased_by={node} expected={orch['dispatched_node']}", layer="execution"))

    # 2. Schema checks
    payload = extract_payload(rd)
    schema_checks.append(_check("structured_output_present", bool(payload), "JSON result parsed" if payload else "no JSON found", layer="schema"))
    ok, problems = (False, ["no payload"])
    if payload and script_id in script_library.LIBRARY:
        ok, problems = script_library.validator_for(script_id)(payload)
    schema_checks.append(_check("schema_valid", ok, "; ".join(problems), layer="schema"))

    # Artifact corroboration: result.json stored by worker must exist and match
    art = Path(settings.evidence_dir) / f"task_{task['id']}" / "result.json"
    art_ok = False
    if art.is_file():
        try:
            art_ok = json.loads(art.read_text("utf-8")).get("schema") == payload.get("schema")
        except (ValueError, OSError):
            art_ok = False
    schema_checks.append(_check("artifact_result_json_matches", art_ok, str(art.name), layer="schema"))

    # 3. Goal checks: verify real measured data
    has_data = bool(payload and any(k not in ("schema", "status", "node_id", "timestamp") for k in payload))
    goal_checks.append(_check("has_measured_data", has_data,
                              "measured telemetry payload verified" if has_data else "no telemetry metrics found", layer="goal"))

    all_checks = schema_checks + execution_checks + goal_checks
    all_passed = all(c["passed"] for c in all_checks)
    decision = "ACCEPTED" if all_passed else ("BLOCKED" if not has_data and all(c["passed"] for c in schema_checks + execution_checks) else "REJECTED")

    return {
        "accepted": all_passed,
        "decision": decision,
        "schema_checks": schema_checks,
        "execution_checks": execution_checks,
        "goal_checks": goal_checks,
        "checks": all_checks,
        "node": node,
        "result": {"kind": "worker_telemetry", "node": node, "schema": payload.get("schema"), "data": payload} if all_passed else {},
    }


def evaluate_inference(orch: Dict[str, Any], req: Dict[str, Any], prompt: str) -> Dict[str, Any]:
    """3-layer evaluation for model inference outputs."""
    acc = jload(orch["acceptance_json"])
    text = (req.get("response") or "").strip()

    schema_checks: List[Dict[str, Any]] = [
        _check("non_empty", bool(text), "non-empty response text" if text else "empty response", layer="schema"),
        _check("min_length", len(text) >= int(acc.get("min_chars", 20)), f"{len(text)} chars (min {acc.get('min_chars', 20)})", layer="schema"),
    ]

    execution_checks: List[Dict[str, Any]] = [
        _check("inference_completed", req.get("status") == "completed", f"status={req.get('status')}", layer="execution"),
        _check("executed_on_expected_node", req.get("node_id") == orch["dispatched_node"],
               f"node={req.get('node_id')} expected={orch['dispatched_node']}", layer="execution"),
    ]

    suspicious = scan_text_for_injection(text)
    not_echo = text[:200] != prompt.strip()[:200]

    action = jload(orch.get("action_json") or "{}")
    instruction = action.get("instruction", "")
    relevance_words = [
        w.lower() for w in re.findall(r"\b[A-Za-z]{4,}\b", instruction)
        if w.lower() not in {"objective", "evaluate", "verified", "evidence", "analyze", "primary", "mandatory", "policy", "recommendations", "strictly", "grounded"}
    ]
    is_topical = True
    matches = 0
    if relevance_words and len(text) > 80:
        matches = sum(1 for w in relevance_words if w in text.lower())
        if matches == 0 and len(relevance_words) >= 4:
            is_topical = False

    unrelated_leak = (
        any(k in text.lower() for k in ("potter valley", "scott dam", "ferc surrender", "van arsdale"))
        and not any(k in instruction.lower() for k in ("potter", "water", "dam", "river"))
    )

    goal_checks: List[Dict[str, Any]] = [
        _check("not_prompt_echo", not_echo, "output is not an echo of prompt" if not_echo else "prompt echo detected", layer="goal"),
        _check("topical_relevance", is_topical, f"relevance check against task objective (matches={matches})", layer="goal"),
        _check("no_cross_domain_leak", not unrelated_leak, "no cross-domain contamination detected" if not unrelated_leak else "unrelated domain boilerplate detected", layer="goal"),
    ]
    if suspicious:
        goal_checks.append(_check("not_prompt_injection", False, "flagged suspicious instruction-like content", layer="goal"))
    else:
        goal_checks.append(_check("not_prompt_injection", True, "no suspicious instruction patterns", layer="goal"))

    # Factual consistency validation against supplied evidence/facts
    verified_facts = acc.get("verified_facts") or action.get("verified_facts")
    require_facts = acc.get("require_factual_consistency", False) or bool(verified_facts)
    factual_contradictions = []

    if require_facts and verified_facts and isinstance(verified_facts, dict):
        for claim_key, fact_spec in verified_facts.items():
            if isinstance(fact_spec, dict):
                for bad_phrase in fact_spec.get("prohibited_patterns", []):
                    if bad_phrase.lower() in text.lower():
                        factual_contradictions.append(f"Contradiction on '{claim_key}': found prohibited '{bad_phrase}'")
                for req_phrase in fact_spec.get("required_patterns", []):
                    if req_phrase.lower() not in text.lower():
                        factual_contradictions.append(f"Missing factual requirement for '{claim_key}': expected '{req_phrase}'")
            elif isinstance(fact_spec, str):
                if fact_spec.lower() not in text.lower():
                    factual_contradictions.append(f"Missing verified fact '{claim_key}': expected '{fact_spec}'")

    if require_facts:
        goal_checks.append(
            _check(
                "factual_consistency",
                len(factual_contradictions) == 0,
                "factual assertions consistent with verified context" if not factual_contradictions else "; ".join(factual_contradictions),
                layer="goal",
            )
        )

    # Truncation detection: token budget exhaustion or hanging output
    num_predict = req.get("num_predict") or acc.get("max_tokens") or action.get("num_predict")
    gen_tokens = req.get("gen_tokens")
    is_truncated = False
    truncation_reason = ""

    if num_predict and gen_tokens and int(gen_tokens) >= int(num_predict):
        stripped = text.strip()
        if not any(stripped.endswith(tc) for tc in (".", "!", "?", "```", "---")):
            is_truncated = True
            truncation_reason = f"Generation reached token cap ({gen_tokens}/{num_predict} tokens) and ended abruptly without sentence termination"
        elif re.search(r"[-*]\s+\*\*[^*]+:\s*$", stripped):
            is_truncated = True
            truncation_reason = f"Generation reached token cap ({gen_tokens}/{num_predict} tokens) and ended on an incomplete field marker"

    if not is_truncated and re.search(r"[-*]\s+\*\*[^*]+:\s*$", text.strip()):
        is_truncated = True
        truncation_reason = "Output ended abruptly on an incomplete bullet/field header"

    if acc.get("check_truncation", True) and (num_predict is not None or is_truncated):
        goal_checks.append(
            _check(
                "output_not_truncated",
                not is_truncated,
                "output is complete without premature truncation" if not is_truncated else truncation_reason,
                layer="goal",
            )
        )

    # Instruction completion verification
    required_instructions = acc.get("required_instructions") or action.get("required_instructions") or []
    missing_instructions = []

    if required_instructions and isinstance(required_instructions, list):
        for instr in required_instructions:
            if isinstance(instr, dict):
                instr_id = instr.get("id") or instr.get("name", "unnamed_instruction")
                desc = instr.get("description", instr_id)
                patterns = instr.get("semantic_patterns", [])
                prohibited = instr.get("prohibited_patterns", [])

                has_prohibited = any(re.search(p, text, re.IGNORECASE) for p in prohibited)
                if has_prohibited:
                    missing_instructions.append(f"Instruction '{instr_id}' violated negative constraint: {prohibited}")
                    continue

                mode = instr.get("pattern_mode", "all")
                if patterns:
                    if mode == "all":
                        matches = [bool(re.search(p, text, re.IGNORECASE)) for p in patterns]
                        if not all(matches):
                            missing_instructions.append(f"Instruction '{instr_id}' incomplete: required semantic elements missing for '{desc}'")
                    else:
                        has_match = any(re.search(p, text, re.IGNORECASE) for p in patterns)
                        if not has_match:
                            missing_instructions.append(f"Instruction '{instr_id}' missing required conclusion or answer for '{desc}'")
            elif isinstance(instr, str):
                if instr.lower() not in text.lower():
                    missing_instructions.append(f"Missing required instruction element: '{instr}'")

        goal_checks.append(
            _check(
                "instruction_completion",
                len(missing_instructions) == 0,
                "all required instructions and conclusions answered" if not missing_instructions else "; ".join(missing_instructions),
                layer="goal",
            )
        )

    all_checks = schema_checks + execution_checks + goal_checks
    task_passed = (
        all(c["passed"] for c in schema_checks)
        and all(c["passed"] for c in execution_checks)
        and all(c["passed"] for c in goal_checks if c["name"] != "not_prompt_injection")
    )
    decision = "ACCEPTED" if task_passed and not suspicious else ("NEEDS_HUMAN_REVIEW" if task_passed and suspicious else "REJECTED")

    return {
        "accepted": task_passed,
        "decision": decision,
        "schema_checks": schema_checks,
        "execution_checks": execution_checks,
        "goal_checks": goal_checks,
        "checks": all_checks,
        "node": req.get("node_id"),
        "suspicious_output": bool(suspicious),
        "result": {
            "kind": "model_output",
            "label": "AI-generated, unverified",
            "text": text[:6000],
            "model": req.get("model"),
            "node": req.get("node_id"),
            "gen_tokens": req.get("gen_tokens"),
            "prompt_tokens": req.get("prompt_tokens"),
            "latency_s": req.get("latency_s"),
            "flagged_instruction_like_content": bool(suspicious),
        } if task_passed else {},
    }


def evaluate_report(
    md: str,
    required_sections: Optional[List[str]] = None,
    has_telemetry: Optional[bool] = None,
    has_ai: Optional[bool] = None,
    require_telemetry_data: bool = False,
    has_telemetry_data: bool = True,
    requires_citations: bool = False,
    claims_grounded: bool = True,
    require_provenance: Optional[bool] = None,
    verified_sources: Optional[List[Dict[str, Any]]] = None,
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
    citation_audit: Optional[Dict[str, Any]] = None,
    disallowed_claims: Optional[List[Union[str, Dict[str, Any]]]] = None,
    numerical_constraints: Optional[List[Dict[str, Any]]] = None,
    temporal_constraints: Optional[List[Dict[str, Any]]] = None,
    required_categories: Optional[List[str]] = None,
    reference_date: Optional[Union[str, date]] = None,
) -> Dict[str, Any]:
    """3-layer evaluation for generated reports and documents."""
    schema_checks: List[Dict[str, Any]] = []
    execution_checks: List[Dict[str, Any]] = []
    goal_checks: List[Dict[str, Any]] = []

    # Certification containment: a heading, source dictionary, or caller-supplied
    # audit is not proof of native execution or claim support.
    from app.services.orchestration.certification import native_output_provenance
    native = False
    if project_id and run_id:
        try:
            connection = get_db_connection()
            try:
                native = native_output_provenance(connection, project_id, run_id, md)
            finally:
                connection.close()
        except Exception:
            native = False
    execution_checks.append(_check("native_output_provenance", native,
        "controller/run/task/lease/execution/artifact binding required", layer="execution"))
    if requires_citations:
        # Intentionally unavailable until the independently stored claim inventory,
        # source snapshots and verifier attestations are implemented. No boolean
        # or optional audit dictionary may waive this hold.
        goal_checks.append(_check("trusted_claim_evidence_complete", False,
            "claim-level certification unavailable; research acceptance held", layer="goal"))

    # Schema checks: non-empty markdown document
    schema_checks.append(_check("document_non_empty", bool(md.strip()), "document has content", layer="schema"))

    prov_needed = require_provenance if require_provenance is not None else bool(
        has_telemetry or has_ai or (required_sections and any("provenance" in s.lower() for s in required_sections))
    )
    if prov_needed:
        schema_checks.append(_check("provenance_present", "## Provenance" in md or "Provenance" in md, "provenance table must be present", layer="schema"))

    # Execution checks: verify presence of required sections
    if has_telemetry:
        execution_checks.append(_check("telemetry_section_present", "## Measured telemetry" in md, "measured telemetry section missing", layer="execution"))
    if has_ai:
        execution_checks.append(_check("ai_section_present", "## AI-generated summary" in md, "AI summary section missing", layer="execution"))

    if required_sections:
        effective_sections = []
        for s in required_sections:
            if s == "## Measured telemetry" and not has_telemetry and "## Measured telemetry" not in md:
                continue
            effective_sections.append(s)
        missing = [s for s in effective_sections if s not in md]
        execution_checks.append(_check("sections_present", not missing, f"missing={missing}", layer="execution"))

    # Goal checks: proper labelling and verification of contents
    if has_telemetry:
        goal_checks.append(_check("telemetry_label_present", "Measured" in md, "telemetry must be labelled as measured", layer="goal"))
    if has_ai:
        goal_checks.append(_check("ai_label_present", "AI-generated" in md, "AI content must be labelled unverified", layer="goal"))

    if required_sections:
        goal_checks.append(_check("labels_present", any(l in md for l in ("Measured", "AI-generated", "Objective", "Provenance")),
                                  "report content must be appropriately labelled", layer="goal"))

    # Finding F-12 & Audit requirement 6:
    # Health report requiring measured telemetry but none available cannot complete!
    if require_telemetry_data or (has_telemetry and not has_telemetry_data):
        has_actual_telemetry = bool(has_telemetry and has_telemetry_data)
        goal_checks.append(_check("measured_telemetry_available", has_actual_telemetry,
                                  "real measured telemetry data is present" if has_actual_telemetry else "missing measured telemetry data", layer="goal"))

    # Finding F-12 & Audit requirement 8 & Stage 3 Execution Safety:
    # A research_report.md with invented claims or unverified sources CANNOT pass!
    unverified_citations = False
    citation_rejection_reason = ""
    if requires_citations:
        citation_matches = list(re.finditer(r"\[(?:\d+|src-[a-zA-Z0-9_\-]+)\]", md))
        has_citations = len(citation_matches) > 0
        if not has_citations:
            citation_rejection_reason = "MISSING_REQUIRED_CITATIONS: Report contains 0 inline citations. All research deliverables must cite authoritative sources."
            goal_checks.append(_check("citation_coverage", False, citation_rejection_reason, layer="goal"))
        else:
            goal_checks.append(_check("citation_coverage", True, f"{len(citation_matches)} inline citations found in report", layer="goal"))

        # Authoritative source-to-claim validation (Fail-Closed Bridge)
        sources = verified_sources
        if sources is None and project_id:
            try:
                conn = get_db_connection()
                try:
                    s_rows = conn.execute("SELECT * FROM research_sources WHERE project_id=?", (project_id,)).fetchall()
                    sources = [dict(r) for r in s_rows]
                finally:
                    conn.close()
            except Exception:
                sources = []

        if sources is None:
            # Check if sources are declared inline in the report when claims are verified grounded
            inline_sources = []
            for line in md.splitlines():
                line_str = line.strip()
                if line_str.startswith("- ["):
                    m_inline = re.match(r"^-\s*\[([^\]]+)\]", line_str)
                    if m_inline:
                        inline_sources.append({"id": m_inline.group(1), "title": line_str})
            if inline_sources and claims_grounded:
                sources = inline_sources

        if sources is None:
            # No source library or verification context provided -> fail closed
            unverified_citations = True
            goal_checks.append(_check("citations_verified", False, "no verified source library provided to validate citations", layer="goal"))
        elif len(sources) == 0:
            unverified_citations = True
            goal_checks.append(_check("citations_verified", False, "report contains citations but source catalog has 0 verified sources", layer="goal"))
        else:
            valid_src_ids = {s.get("id") for s in sources} | {s.get("source_id") for s in sources}
            source_by_idx = {i + 1: s for i, s in enumerate(sources)}
            unresolved = []
            cross_run = []
            valid_run_ids = {run_id, f"rrun-{run_id}"}
            if run_id:
                try:
                    c_run = get_db_connection()
                    try:
                        r_runs = c_run.execute("SELECT id FROM research_runs WHERE orchestration_run_id = ? OR id = ?", (run_id, run_id)).fetchall()
                        for r_r in r_runs:
                            valid_run_ids.add(r_r["id"])
                    finally:
                        c_run.close()
                except Exception:
                    pass

            for m in re.finditer(r"\[(src-[a-zA-Z0-9_\-]+)\]", md):
                sid = m.group(1)
                if sid not in valid_src_ids:
                    unresolved.append(f"[{sid}]")
                else:
                    ms = next((s for s in sources if s.get("id") == sid or s.get("source_id") == sid), None)
                    if ms and run_id and ms.get("research_run_id") and ms.get("research_run_id") not in valid_run_ids:
                        cross_run.append(f"[{sid}] from run {ms.get('research_run_id')}")

            for m in re.finditer(r"\[(\d+)\]", md):
                idx = int(m.group(1))
                # Disambiguate 4-digit years (e.g. [2026], [2024]) from footnote citations
                if 1900 <= idx <= 2099 and idx not in source_by_idx and str(idx) not in valid_src_ids:
                    continue
                if idx not in source_by_idx and str(idx) not in valid_src_ids:
                    unresolved.append(f"[{idx}]")
                else:
                    ms = source_by_idx.get(idx) or next((s for s in sources if str(s.get("id")) == str(idx)), None)
                    if ms and run_id and ms.get("research_run_id") and ms.get("research_run_id") not in valid_run_ids:
                        cross_run.append(f"[{idx}] from run {ms.get('research_run_id')}")

            if unresolved:
                citation_rejection_reason = f"invented or missing source references: {', '.join(unresolved)}"
                goal_checks.append(_check("citations_resolved", False, citation_rejection_reason, layer="goal"))
            else:
                goal_checks.append(_check("citations_resolved", True, "all citations resolve to registered sources", layer="goal"))

            if cross_run:
                citation_rejection_reason = f"citations violate run isolation: {', '.join(cross_run)}"
                goal_checks.append(_check("cross_run_isolation", False, citation_rejection_reason, layer="goal"))
            else:
                goal_checks.append(_check("cross_run_isolation", True, "citations belong to current run", layer="goal"))

        if citation_audit:
            audit_ok = bool(citation_audit.get("valid"))
            if not audit_ok:
                contradicted_cits = citation_audit.get("contradicted") or []
                insufficient_cits = citation_audit.get("insufficient_evidence") or [
                    c for c in (citation_audit.get("errors") or []) if "INSUFFICIENT_EVIDENCE" in c
                ]
                unverified_cits = citation_audit.get("unverified") or []
                if contradicted_cits:
                    claims_grounded = False
                    citation_rejection_reason = f"CONTRADICTED_CLAIMS: {len(contradicted_cits)} cited assertions directly contradict referenced sources"
                    goal_checks.append(_check("citation_contradiction", False, citation_rejection_reason, layer="goal"))
                elif insufficient_cits:
                    claims_grounded = False
                    citation_rejection_reason = f"INSUFFICIENT_EVIDENCE: {len(insufficient_cits)} cited assertions lack verifiable evidence in referenced sources"
                    goal_checks.append(_check("citation_insufficient_evidence", False, citation_rejection_reason, layer="goal"))
                elif unverified_cits:
                    unverified_citations = True
                    goal_checks.append(_check("citation_unverified", False, f"unverified citations: {len(unverified_cits)} assertions lack deterministic proof", layer="goal"))
                else:
                    claims_grounded = False
                goal_checks.append(_check("citation_audit", False, f"audit reported errors: {citation_audit.get('errors')}", layer="goal"))
            else:
                goal_checks.append(_check("citation_audit", True, "deterministic citation audit passed", layer="goal"))

    if not claims_grounded:
        goal_checks.append(_check("claims_grounded", False, citation_rejection_reason or "invented, contradicted, or uncorroborated claims detected in report", layer="goal"))
    elif requires_citations:
        goal_checks.append(_check("claims_grounded", True, "claims corroborated by retrieved sources", layer="goal"))

    # Quality-First negative constraint checking (Milestone 2)
    negative_violations = []
    if disallowed_claims:
        negative_violations = DeterministicVerificationEngine.verify_negative_constraints(md, disallowed_claims)
        goal_checks.append(
            _check(
                "negative_constraints_passed",
                len(negative_violations) == 0,
                "no prohibited claims or policy conflations detected" if not negative_violations else f"Prohibited claims found: {'; '.join(v['description'] for v in negative_violations)}",
                layer="goal",
            )
        )

    # Quality-First deterministic numerical & formula verification (Milestone 1/2)
    numerical_failures = []
    if numerical_constraints:
        numerical_failures = DeterministicVerificationEngine.verify_required_numerical_assertions(md, numerical_constraints)
        goal_checks.append(
            _check(
                "numerical_constraints_passed",
                len(numerical_failures) == 0,
                "all numerical and formula constraints verified deterministically" if not numerical_failures else f"Arithmetic errors: {'; '.join(f['detail'] for f in numerical_failures)}",
                layer="goal",
            )
        )

    # Quality-First category completeness checking
    missing_categories = []
    if required_categories:
        for cat in required_categories:
            cat_words = [w.lower() for w in re.findall(r"\b[A-Za-z]{3,}\b", cat) if w.lower() not in ("and", "the", "for", "with")]
            if cat_words:
                found_word = any(w in md.lower() for w in cat_words)
                if not found_word:
                    missing_categories.append(cat)
        goal_checks.append(
            _check(
                "category_completeness",
                len(missing_categories) == 0,
                f"all {len(required_categories)} required information categories documented" if not missing_categories else f"Missing required categories: {', '.join(missing_categories)}",
                layer="goal",
            )
        )

    # Quality-First temporal / deadline checking
    temporal_failures = []
    if temporal_constraints:
        for tc in temporal_constraints:
            if tc.get("must_be_future"):
                date_matches = list(re.finditer(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b", md))
                for m in date_matches:
                    dm = m.group(0)
                    start_ctx = max(0, m.start() - 80)
                    end_ctx = min(len(md), m.end() + 80)
                    ctx = md[start_ctx:end_ctx].lower()

                    # Ignore citation, publication, adoption, or historical reference dates
                    if any(ref_kw in ctx for ref_kw in ("adopted", "published", "enacted", "approved", "effective", "passed", "accessed", "retrieved", "source:", "reference", "provenance")):
                        continue

                    res_d = DeterministicVerificationEngine.verify_deadline(dm, reference_date=reference_date, must_be_future=True)
                    if not res_d["passed"]:
                        if not any(k in ctx for k in ("unannounced", "closed", "uncertain", "pending", "provisional", "past", "historical", "tbd", "tba")):
                            temporal_failures.append(res_d["detail"])
        goal_checks.append(
            _check(
                "temporal_constraints_passed",
                len(temporal_failures) == 0,
                "deadlines verified valid or properly caveated" if not temporal_failures else f"Expired unflagged deadlines: {'; '.join(temporal_failures)}",
                layer="goal",
            )
        )

    all_checks = schema_checks + execution_checks + goal_checks
    all_passed = all(c["passed"] for c in all_checks)
    is_blocked = any(
        c["name"] in {"measured_telemetry_available", "native_output_provenance", "trusted_claim_evidence_complete"}
        and not c["passed"] for c in all_checks
    )
    has_negative_violation = bool(negative_violations)
    has_numerical_failure = bool(numerical_failures)
    has_temporal_failure = bool(temporal_failures)

    if all_passed:
        decision = "ACCEPTED"
        fulfillment_status = "FULFILLED"
        fulfillment_score = 1.0
    elif is_blocked:
        decision = "BLOCKED"
        fulfillment_status = "BLOCKED"
        fulfillment_score = 0.0
    elif has_negative_violation or has_numerical_failure or has_temporal_failure:
        decision = "REJECTED"
        fulfillment_status = "PARTIAL"
        fulfillment_score = 0.50
    elif not claims_grounded:
        decision = "REJECTED"
        fulfillment_status = "PARTIAL"
        fulfillment_score = 0.50
    elif requires_citations and citation_rejection_reason:
        decision = "REJECTED"
        fulfillment_status = "PARTIAL"
        fulfillment_score = 0.60
    elif requires_citations and unverified_citations:
        decision = "NEEDS_HUMAN_REVIEW"
        fulfillment_status = "NEEDS_REVIEW"
        fulfillment_score = 0.75
    elif missing_categories and len(missing_categories) < len(required_categories):
        decision = "NEEDS_HUMAN_REVIEW"
        fulfillment_status = "QUALIFIED_FULFILLMENT"
        fulfillment_score = 0.85
    else:
        decision = "REJECTED"
        fulfillment_status = "UNFULFILLED"
        fulfillment_score = 0.25

    return {
        "accepted": all_passed,
        "decision": decision,
        "fulfillment_status": fulfillment_status,
        "fulfillment_score": fulfillment_score,
        "schema_checks": schema_checks,
        "execution_checks": execution_checks,
        "goal_checks": goal_checks,
        "checks": all_checks,
        "negative_violations": negative_violations,
        "numerical_failures": numerical_failures,
        "missing_categories": missing_categories,
        "temporal_failures": temporal_failures,
    }


def evaluate_deliverable_contract(
    md: str,
    contract: Any,
    verified_sources: Optional[List[Dict[str, Any]]] = None,
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
    reference_date: Optional[Union[str, date]] = None,
) -> Dict[str, Any]:
    """Evaluate a markdown deliverable document directly against a DeliverableRequirementContract."""
    if hasattr(contract, "to_dict"):
        cd = contract.to_dict()
    elif isinstance(contract, dict):
        cd = contract
    else:
        cd = {}

    crit = cd.get("measurable_acceptance_criteria", {})
    req_sections = cd.get("required_output_structure") or crit.get("required_sections")
    disallowed = cd.get("disallowed_claims") or crit.get("disallowed_claims") or []
    numerical = cd.get("numerical_constraints") or crit.get("numerical_constraints") or []
    temporal = cd.get("temporal_constraints") or crit.get("temporal_constraints") or []
    req_cats = cd.get("required_info_categories") or []
    req_cits = crit.get("require_citations", True)

    return evaluate_report(
        md=md,
        required_sections=req_sections,
        requires_citations=req_cits,
        verified_sources=verified_sources,
        project_id=project_id,
        run_id=run_id,
        disallowed_claims=disallowed,
        numerical_constraints=numerical,
        temporal_constraints=temporal,
        required_categories=req_cats,
        reference_date=reference_date,
    )


def evaluate_code_deliverable(
    code: str | bytes,
    filename: str = "main.py",
    language: str = "python",
    test_code: Optional[str] = None,
    acceptance_criteria: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Typed validator for code/software deliverables (Finding F-12 & Stage 3 Execution Safety).
    Requires syntax validity (ast.parse for Python), compilation/parse checks,
    and process-isolated verification that code and tests execute inside a restricted
    sandbox runner with budget and resource controls.
    """
    schema_checks: List[Dict[str, Any]] = []
    execution_checks: List[Dict[str, Any]] = []
    goal_checks: List[Dict[str, Any]] = []

    if isinstance(code, bytes):
        try:
            code_str = code.decode("utf-8")
        except UnicodeDecodeError:
            schema_checks.append(_check("utf8_decodable", False, "Code bytes failed UTF-8 decoding", layer="schema"))
            return {
                "accepted": False,
                "decision": "REJECTED",
                "schema_checks": schema_checks,
                "execution_checks": [],
                "goal_checks": [],
                "checks": schema_checks,
            }
    else:
        code_str = str(code)

    schema_checks.append(_check("code_non_empty", bool(code_str.strip()), f"code length {len(code_str)} chars", layer="schema"))
    ext = Path(filename).suffix.lower()
    schema_checks.append(_check("valid_code_extension", ext in (".py", ".sh", ".js", ".ts", ".json", ".sql", ".rs", ".go"),
                                f"extension '{ext}'", layer="schema"))

    # Execution checks: Syntax parsing
    syntax_ok = False
    syntax_err = ""
    parsed_ast = None
    if ext == ".py" or language.lower() == "python":
        try:
            parsed_ast = ast.parse(code_str, filename=filename)
            syntax_ok = True
        except SyntaxError as se:
            syntax_ok = False
            syntax_err = f"SyntaxError at line {se.lineno}: {se.msg}"
        execution_checks.append(_check("python_syntax_valid", syntax_ok, syntax_err or "ast.parse succeeded", layer="execution"))
    elif ext == ".json":
        try:
            json.loads(code_str)
            syntax_ok = True
        except ValueError as ve:
            syntax_ok = False
            syntax_err = f"JSONDecodeError: {ve}"
        execution_checks.append(_check("json_syntax_valid", syntax_ok, syntax_err or "json.loads succeeded", layer="execution"))
    else:
        syntax_ok = bool(code_str.strip())
        execution_checks.append(_check("script_structure_valid", syntax_ok, "script non-empty", layer="execution"))

    # Goal checks: Acceptance criteria and testable functionality
    crit = acceptance_criteria or {}
    runner_blocked = False
    blocked_err = ""
    if not syntax_ok:
        goal_checks.append(_check("code_syntax_valid", False, f"Cannot parse code with syntax error: {syntax_err}", layer="goal"))
    else:
        goal_checks.append(_check("code_syntax_valid", True, "Code parses cleanly without syntax errors (static validation; dynamic execution unverified)", layer="goal"))

        # Check acceptance criteria (required functions / classes / exports)
        req_funcs = crit.get("required_functions") or crit.get("required_symbols") or []
        if isinstance(req_funcs, list) and req_funcs and parsed_ast:
            defined_names = {node.name for node in ast.walk(parsed_ast) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            missing = [f for f in req_funcs if f not in defined_names]
            goal_checks.append(_check("required_functions_present", not missing, f"missing={missing}" if missing else "all required functions present", layer="goal"))

        # Check if acceptance criteria requires tests
        crit_requires_tests = bool(crit.get("require_tests") or crit.get("required_tests"))
        if crit_requires_tests and not test_code:
            goal_checks.append(_check("required_tests_executed", False, "Acceptance criteria specifies required tests, but no test fixture was provided", layer="goal"))

        # If test suite code is provided or execution check requested, execute in restricted runner
        if (test_code or crit.get("require_execution")) and ext == ".py":
            from app.services.orchestration.code_sandbox import run_isolated_code_evaluation
            box_timeout = float(crit.get("timeout_seconds", 5.0))
            box_res = run_isolated_code_evaluation(
                code=code_str,
                test_code=test_code,
                filename=filename,
                timeout_seconds=box_timeout,
            )
            if box_res.status == "BLOCKED":
                runner_blocked = True
                blocked_err = box_res.error_message or "SECURE_EXECUTION_UNAVAILABLE: OS-enforced isolation required."
                goal_checks.append(_check("secure_execution_environment", False, blocked_err, layer="goal"))
            else:
                chk_name = "test_suite_passed" if test_code else "isolated_execution_passed"
                chk_detail = box_res.error_message or ("Test fixture passed in isolated runner" if test_code else "Clean execution in isolated runner")
                goal_checks.append(_check(chk_name, box_res.success, chk_detail, layer="goal"))

    all_checks = schema_checks + execution_checks + goal_checks
    all_passed = all(c["passed"] for c in all_checks)
    blocked_reason = None
    if runner_blocked:
        decision = "BLOCKED"
        blocked_reason = "SECURE_EXECUTION_UNAVAILABLE"
        summary = blocked_err or "SECURE_EXECUTION_UNAVAILABLE: OS-enforced execution containment is unavailable."
    elif all_passed:
        decision = "ACCEPTED"
        summary = "Code deliverable accepted based on static syntax and structural validation."
    else:
        decision = "REJECTED"
        summary = "Code deliverable validation failed."

    return {
        "accepted": all_passed and not runner_blocked,
        "decision": decision,
        "blocked_reason": blocked_reason,
        "summary": summary,
        "schema_checks": schema_checks,
        "execution_checks": execution_checks,
        "goal_checks": goal_checks,
        "checks": all_checks,
    }


def evaluate_data_deliverable(
    data: str | bytes,
    filename: str = "data.json",
    format_type: str = "json",
    acceptance_criteria: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Typed validator for dataset / structured data deliverables (Finding F-12)."""
    schema_checks: List[Dict[str, Any]] = []
    execution_checks: List[Dict[str, Any]] = []
    goal_checks: List[Dict[str, Any]] = []

    if isinstance(data, bytes):
        try:
            data_str = data.decode("utf-8")
        except UnicodeDecodeError:
            schema_checks.append(_check("utf8_decodable", False, "Data bytes failed UTF-8 decoding", layer="schema"))
            return {"accepted": False, "decision": "REJECTED", "schema_checks": schema_checks, "execution_checks": [], "goal_checks": [], "checks": schema_checks}
    else:
        data_str = str(data)

    parsed_json = None
    if format_type.lower() == "json" or filename.endswith(".json"):
        try:
            parsed_json = json.loads(data_str)
            schema_checks.append(_check("json_parsable", True, "Valid JSON structure", layer="schema"))
        except ValueError as ve:
            schema_checks.append(_check("json_parsable", False, f"JSON parse error: {ve}", layer="schema"))
            return {"accepted": False, "decision": "REJECTED", "schema_checks": schema_checks, "execution_checks": [], "goal_checks": [], "checks": schema_checks}
    elif format_type.lower() == "csv" or filename.endswith(".csv"):
        try:
            reader = list(csv.reader(io.StringIO(data_str)))
            schema_checks.append(_check("csv_parsable", len(reader) > 0, f"CSV rows count: {len(reader)}", layer="schema"))
        except Exception as ce:
            schema_checks.append(_check("csv_parsable", False, f"CSV parse error: {ce}", layer="schema"))
            return {"accepted": False, "decision": "REJECTED", "schema_checks": schema_checks, "execution_checks": [], "goal_checks": [], "checks": schema_checks}

    execution_checks.append(_check("data_non_empty", bool(data_str.strip()), f"data length {len(data_str)} bytes", layer="execution"))

    # Goal checks: schema constraints and required keys
    crit = acceptance_criteria or {}
    if parsed_json is not None:
        req_keys = crit.get("required_keys") or []
        if isinstance(req_keys, list) and req_keys:
            if isinstance(parsed_json, dict):
                missing_keys = [k for k in req_keys if k not in parsed_json]
                goal_checks.append(_check("required_keys_present", not missing_keys, f"missing={missing_keys}" if missing_keys else "all required keys present", layer="goal"))
            elif isinstance(parsed_json, list):
                missing_keys = [k for k in req_keys if not any(k in item for item in parsed_json if isinstance(item, dict))]
                goal_checks.append(_check("required_keys_present", not missing_keys, f"missing={missing_keys}" if missing_keys else "all required keys present", layer="goal"))
        min_items = crit.get("min_items")
        if min_items and isinstance(parsed_json, list):
            goal_checks.append(_check("min_items_threshold", len(parsed_json) >= int(min_items), f"items {len(parsed_json)} (min {min_items})", layer="goal"))

    all_checks = schema_checks + execution_checks + goal_checks
    all_passed = all(c["passed"] for c in all_checks)
    decision = "ACCEPTED" if all_passed else "REJECTED"

    return {
        "accepted": all_passed,
        "decision": decision,
        "schema_checks": schema_checks,
        "execution_checks": execution_checks,
        "goal_checks": goal_checks,
        "checks": all_checks,
    }


def evaluate_deliverable_acceptance(
    deliverable: Dict[str, Any],
    artifacts: List[Dict[str, Any]],
    acceptance_criteria: Optional[Dict[str, Any]] = None,
    context_inputs: Optional[Dict[str, Any]] = None,
    autonomy_level: int = 1,
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Authoritative 3-layer deliverable acceptance evaluation (Findings F-12, F-04).
    Validates deliverable output across SchemaCheck, ExecutionCheck, and GoalCheck layers.
    Determines whether a deliverable is:
    - ACCEPTED: All 3 layers pass deterministically
    - REJECTED: Validation or acceptance check failed
    - BLOCKED: Required evidence (e.g. measured telemetry) is missing
    - NEEDS_HUMAN_REVIEW: Unverifiable inference or subjective task
    """
    crit = acceptance_criteria or {}
    inputs = context_inputs or {}
    dtype = deliverable.get("deliverable_type") or deliverable.get("type") or "document"
    name = deliverable.get("name") or deliverable.get("key") or "Deliverable"

    if not artifacts:
        return {
            "accepted": False,
            "decision": "BLOCKED",
            "summary": f"Deliverable '{name}' has no backing artifacts.",
            "schema_checks": [_check("artifact_present", False, "no artifacts produced", layer="schema")],
            "execution_checks": [],
            "goal_checks": [],
            "checks": [_check("artifact_present", False, "no artifacts produced", layer="schema")],
        }

    primary_art = artifacts[0]
    for a in artifacts:
        fname = a.get("filename", "").lower()
        if dtype in ("report", "document") and fname.endswith((".md", ".markdown", ".txt")):
            primary_art = a
            break
        elif dtype in ("software_artifact", "code") and any(fname.endswith(ext) for ext in (".py", ".sh", ".js", ".ts", ".go", ".rs")):
            primary_art = a
            break
        elif dtype in ("dataset",) and any(fname.endswith(ext) for ext in (".json", ".csv")):
            primary_art = a
            break

    art_path = Path(primary_art.get("storage_path", "")) if primary_art.get("storage_path") else None
    content_bytes = b""
    if art_path and art_path.is_file():
        try:
            content_bytes = art_path.read_bytes()
        except OSError:
            content_bytes = b""
    if not content_bytes and primary_art.get("cas_hash"):
        try:
            from app.services.artifacts.storage import storage_backend
            cas_p = storage_backend.get_cas_path(primary_art["cas_hash"])
            if cas_p.is_file():
                content_bytes = cas_p.read_bytes()
        except Exception:
            pass

    crit_text = json.dumps(crit).lower() if isinstance(crit, dict) else str(crit).lower()
    name_lower = name.lower()
    requires_telemetry = (
        "telemetry" in crit_text or "measured" in crit_text or "system health" in name_lower or "health_report" in name_lower
    )
    has_telemetry_input = any(v.get("kind") == "worker_telemetry" for v in inputs.values()) if inputs else False

    if requires_telemetry and not has_telemetry_input:
        has_telemetry_art = any(a.get("provenance") == "worker_telemetry" for a in artifacts)
        if not has_telemetry_art:
            return {
                "accepted": False,
                "decision": "BLOCKED",
                "summary": f"Deliverable '{name}' requires measured telemetry, but no telemetry evidence is available.",
                "schema_checks": [_check("telemetry_evidence_required", False, "requires measured telemetry but none available", layer="schema")],
                "execution_checks": [],
                "goal_checks": [_check("telemetry_measured", False, "no worker telemetry recorded for this run", layer="goal")],
                "checks": [_check("telemetry_measured", False, "no worker telemetry recorded for this run", layer="goal")],
            }

    crit_dict = crit if isinstance(crit, dict) else {}
    if not crit_dict and isinstance(crit, str) and crit.strip().startswith("{"):
        try:
            crit_dict = json.loads(crit)
        except Exception:
            crit_dict = {}

    if crit_dict.get('schema')=='qc.claim_contract.v1':
        from app.database import db_transaction
        from app.services.research.feedback import ProjectEvidenceFeedback
        from app.services.research.claim_contracts import ClaimDocumentReview
        from app.services.orchestration.engine import _NATIVE_RETRIEVAL_VERIFIER
        review=None
        try:
            with db_transaction() as conn:
                scopes=conn.execute('SELECT id FROM research_runs WHERE project_id=? AND orchestration_run_id=?',
                    (project_id,run_id)).fetchall()
                if len(scopes)!=1: raise ValueError('Unique research scope required')
                ledger=ProjectEvidenceFeedback(conn,project_id,scopes[0]['id'],receipt_verifier=_NATIVE_RETRIEVAL_VERIFIER)
                candidates=[r for r in ledger.items('deliverable_revision')
                    if r['deliverable_key']==deliverable.get('key') and r['artifact_id']==primary_art.get('id')]
                if not candidates: raise ValueError('Missing canonical deliverable binding')
                reviewer=ClaimDocumentReview(ledger)
                reviews=[reviewer.evaluate(r['id']) for r in candidates]
                review=next((r for r in reviews if r['coverage_complete']),reviews[-1])
        except Exception:
            pass
        checks=[_check('version_bound_claim_coverage',bool(review and review['coverage_complete']),
            'Fresh canonical source-assertion review required',layer='goal'),
            _check('trusted_claim_certification',False,'Research certification hold remains active',layer='goal')]
        return {'accepted':False,'decision':'BLOCKED','fulfillment_status':'BLOCKED','fulfillment_score':0.0,
            'summary':'Version-bound claim review evaluated; research certification remains held.',
            'checks':checks,'goal_checks':checks,'schema_checks':[],'execution_checks':[],'claim_review':review}

    if dtype in ("software_artifact", "code"):
        if crit_dict.get('schema')=='qc.software_contract.v1':
            proposal_review=None
            try:
                from app.services.orchestration.code_proposals import CodeProposalReview
                with closing(get_db_connection()) as conn:
                    proposal_review=CodeProposalReview(conn,project_id,run_id).review(
                        deliverable.get('key'),primary_art.get('id'))
                    conn.commit()
            except Exception:
                proposal_review=None
            checks=[_check('version_bound_code_proposal',bool(proposal_review and proposal_review['stageable']),
                          'Fresh canonical native proposal and approved tests required',layer='goal'),
                    _check('independent_software_acceptance',False,
                           'Staging is not independently verified software behavior',layer='goal')]
            return {'accepted':False,'decision':'BLOCKED','fulfillment_status':'BLOCKED','fulfillment_score':0.0,
                    'blocked_reason':'SOFTWARE_ACCEPTANCE_UNVERIFIED','checks':checks,
                    'schema_checks':[],'execution_checks':[],'goal_checks':checks,'proposal_review':proposal_review,
                    'summary':'Version-bound proposal diagnostics recorded; software acceptance remains held.'}
        review = evaluate_code_deliverable(
            code=content_bytes,
            filename=primary_art.get("filename", "main.py"),
            test_code=crit_dict.get("test_code"),
            acceptance_criteria=crit_dict,
        )
        # Static parsing and caller-provided tests are diagnostics, not independent
        # acceptance. No trusted native software acceptance receipt exists yet.
        check = _check('independent_software_acceptance', False,
            'Native version-bound independent behavior verification is not provisioned',layer='goal')
        review['checks'].append(check)
        review['goal_checks'].append(check)
        review.update(accepted=False,decision='BLOCKED',fulfillment_status='BLOCKED',fulfillment_score=0.0,
            blocked_reason='SOFTWARE_ACCEPTANCE_UNVERIFIED',
            summary='Software diagnostics recorded; independent software acceptance remains held.')
        return review
    elif dtype in ("dataset",):
        return evaluate_data_deliverable(
            data=content_bytes,
            filename=primary_art.get("filename", "data.json"),
            acceptance_criteria=crit_dict,
        )
    elif dtype in ("report", "document"):
        text_content = content_bytes.decode("utf-8", errors="replace")
        has_ai_input = any(v.get("kind") == "model_output" for v in inputs.values()) if inputs else False
        req_sects = crit_dict.get("required_sections")
        is_research = "research" in name_lower or "research" in dtype.lower() or "citation" in crit_text

        # Extract citation audit artifact if present among artifacts
        audit_data = None
        for a in artifacts:
            afname = a.get("filename", "")
            if (afname == "citation_audit.json" or (afname.startswith("citation_audit") and afname.endswith(".json"))):
                try:
                    if a.get("storage_path") and Path(a["storage_path"]).is_file():
                        audit_data = json.loads(Path(a["storage_path"]).read_text(encoding="utf-8"))
                        break
                    elif a.get("cas_hash"):
                        from app.services.artifacts.storage import storage_backend
                        cas_p = storage_backend.get_cas_path(a["cas_hash"])
                        if cas_p.is_file():
                            audit_data = json.loads(cas_p.read_text(encoding="utf-8"))
                            break
                except Exception:
                    pass

        pid = project_id or primary_art.get("project_id")
        rid = run_id or primary_art.get("run_id")

        contract_data = deliverable.get("contract") or crit_dict.get("contract") or {}
        if hasattr(contract_data, "to_dict"):
            contract_data = contract_data.to_dict()

        disallowed = contract_data.get("disallowed_claims") or crit_dict.get("disallowed_claims")
        numerical = contract_data.get("numerical_constraints") or crit_dict.get("numerical_constraints")
        temporal = contract_data.get("temporal_constraints") or crit_dict.get("temporal_constraints")
        req_cats = contract_data.get("required_info_categories") or crit_dict.get("required_info_categories")

        return evaluate_report(
            md=text_content,
            required_sections=req_sects,
            has_telemetry=has_telemetry_input,
            has_ai=has_ai_input,
            require_telemetry_data=requires_telemetry,
            has_telemetry_data=has_telemetry_input,
            requires_citations=is_research or bool(crit_dict.get("require_citations")),
            verified_sources=None,
            project_id=pid,
            run_id=rid,
            citation_audit=audit_data,
            disallowed_claims=disallowed,
            numerical_constraints=numerical,
            temporal_constraints=temporal,
            required_categories=req_cats,
        )
    else:
        if primary_art.get("validation_state") == "valid" and content_bytes:
            if primary_art.get("provenance") == "model_output" and not crit:
                return {
                    "accepted": False,
                    "decision": "NEEDS_HUMAN_REVIEW",
                    "summary": f"Deliverable '{name}' contains AI-generated content without deterministic acceptance checks; human review required.",
                    "schema_checks": [_check("artifact_valid", True, "artifact registered", layer="schema")],
                    "execution_checks": [_check("inference_produced", True, "model output received", layer="execution")],
                    "goal_checks": [_check("objective_goal_verified", False, "unverifiable inference requires human review", layer="goal")],
                    "checks": [_check("objective_goal_verified", False, "unverifiable inference requires human review", layer="goal")],
                }
            return {
                "accepted": True,
                "decision": "ACCEPTED",
                "summary": f"Deliverable '{name}' verified against general artifact checks.",
                "schema_checks": [_check("artifact_valid", True, "artifact valid", layer="schema")],
                "execution_checks": [_check("execution_success", True, "artifact produced", layer="execution")],
                "goal_checks": [_check("general_acceptance", True, "acceptance verified", layer="goal")],
                "checks": [_check("general_acceptance", True, "acceptance verified", layer="goal")],
            }
        return {
            "accepted": False,
            "decision": "REJECTED",
            "summary": f"Deliverable '{name}' backing artifact is invalid or empty.",
            "schema_checks": [_check("artifact_valid", False, "artifact validation failed", layer="schema")],
            "execution_checks": [],
            "goal_checks": [],
            "checks": [_check("artifact_valid", False, "artifact validation failed", layer="schema")],
        }


def evaluate_research_step(step_name: str, result_data: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic acceptance evaluation for controller research steps."""
    schema_checks: List[Dict[str, Any]] = []
    execution_checks: List[Dict[str, Any]] = []
    goal_checks: List[Dict[str, Any]] = []

    status = result_data.get("status")
    execution_checks.append(_check("execution_success", status in ("success", "completed"), f"status={status}", layer="execution"))

    if step_name == "research_search":
        count = result_data.get("results_count", 0)
        goal_checks.append(_check("search_results_found", count > 0, f"results_count={count}", layer="goal"))
    elif step_name in ("research_fetch", "research_browser_read", "research_extract"):
        count = result_data.get("sources_count", 0)
        goal_checks.append(_check("sources_retrieved", count > 0, f"sources_count={count}", layer="goal"))
    elif step_name == "research_verify":
        count = result_data.get("claims_count", 0)
        goal_checks.append(_check("claims_verified", count > 0, f"claims_count={count}", layer="goal"))
    elif step_name in ("research_coverage", "research_evidence_coverage"):
        assessment = result_data.get("assessment", {})
        overall_status = assessment.get("overall_status") or result_data.get("overall_status")
        schema_checks.append(_check("assessment_present", bool(assessment), "assessment structured data", layer="schema"))
        valid_status = overall_status in ("SUFFICIENT", "PARTIAL", "INSUFFICIENT", "BLOCKED")
        goal_checks.append(_check("coverage_assessed", valid_status, f"overall_status={overall_status}", layer="goal"))
    elif step_name in ("research_synthesize", "research_citation_audit", "research_full_cycle"):
        audit = result_data.get("citation_audit") or result_data.get("audit") or {}
        has_report = bool(result_data.get("report_markdown"))
        schema_checks.append(_check("report_generated", has_report, "report markdown generated" if has_report else "no report markdown", layer="schema"))
        goal_checks.append(_check("citations_valid", audit.get("valid", False), f"audit_valid={audit.get('valid')}", layer="goal"))

    all_checks = schema_checks + execution_checks + goal_checks
    all_passed = all(c["passed"] for c in all_checks)
    decision = "ACCEPTED" if all_passed else "REJECTED"

    return {
        "accepted": all_passed,
        "decision": decision,
        "schema_checks": schema_checks,
        "execution_checks": execution_checks,
        "goal_checks": goal_checks,
        "checks": all_checks,
    }


def evaluate_project_objective_fulfillment(
    project_id: str,
    run: Dict[str, Any],
    plan: Dict[str, Any],
    intake: Dict[str, Any],
    coverage_assessment: Optional[Dict[str, Any]] = None,
    deliverables_tested: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Three-level project evaluation (Phase 6):
    Level 1: Task Execution - Did all tasks run and produce expected outputs?
    Level 2: Deliverable Acceptance - Did each deliverable satisfy structural & citation criteria?
    Level 3: Project Objective Fulfillment - Do the accepted deliverables collectively satisfy
             the user's stated objective, deliverable contracts, and success criteria?
    """
    obj = (intake.get("objective") or "").strip()
    sc_text = str(intake.get("success_criteria") or "").strip()
    delivs = plan.get("deliverables", [])
    tested = deliverables_tested or []

    # A planned deliverable and a caller's passed=True are not certification.
    expected = [d.get("key") for d in delivs]
    actual = [t.get("key") for t in tested]
    deliv_success = bool(expected) and all(expected) and len(set(expected)) == len(expected)
    deliv_success = deliv_success and len(actual) == len(expected) and set(actual) == set(expected)
    deliv_success = deliv_success and all(
        t.get("status") == "accepted" and t.get("passed") is not False
        and bool(t.get("evidence_satisfied")) for t in tested
    )
    tech_success = False
    model_inference_required = bool(re.search(r"\b(inference|model|reasoning|scheduler-mediated)\b", sc_text, re.I))
    model_inference_executed = False
    from app.services.orchestration.certification import native_artifact_digest, completed_native_inference
    try:
        c = get_db_connection()
        try:
            model_inference_executed = completed_native_inference(c, project_id, run["id"])
            tech_success = bool(tested) and all(
                isinstance(a, dict) and native_artifact_digest(c, project_id, run["id"], a.get("sha256"))
                for t in tested for a in (t.get("evidence_satisfied") or [None])
            )
        finally:
            c.close()
    except Exception:
        tech_success = False
    deliv_success = deliv_success and tech_success

    fulfilled_criteria: List[str] = []
    unfulfilled_criteria: List[str] = []

    if model_inference_required:
        if model_inference_executed:
            fulfilled_criteria.append("Scheduler-mediated model inference executed on eligible node")
        else:
            unfulfilled_criteria.append("Scheduler-mediated model inference was required by success criteria but omitted or ran locally without model worker")

    # 3. Deliverable count criteria
    m_count = re.search(r"\b(?:all\s+)?(\d+|five|four|three|two|six)\s+deliverables\b", sc_text, re.I)
    if m_count:
        num_map = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
        raw_cnt = m_count.group(1).lower()
        exp_cnt = num_map.get(raw_cnt, int(raw_cnt) if raw_cnt.isdigit() else len(delivs))
        if len(delivs) >= exp_cnt:
            fulfilled_criteria.append(f"Preserved all requested deliverables ({len(delivs)} deliverables configured)")
        else:
            unfulfilled_criteria.append(f"Requested {exp_cnt} deliverables, but only {len(delivs)} present")

    # 4. Evidence Coverage & Information Sufficiency (Level 3 Core)
    cov_status = "UNKNOWN"
    deliv_findings: Dict[str, Any] = {}
    critical_gaps: List[str] = []

    if coverage_assessment:
        cov_status = coverage_assessment.get("overall_status", "UNKNOWN")
        raw_delivs = coverage_assessment.get("deliverable_assessments", {})
        if isinstance(raw_delivs, list):
            deliv_map = {da.get("key", da.get("name", "")): da for da in raw_delivs if isinstance(da, dict)}
        elif isinstance(raw_delivs, dict):
            deliv_map = raw_delivs
        else:
            deliv_map = {}
        for d in delivs:
            dk = d.get("key", "")
            da = deliv_map.get(dk) or deliv_map.get(d.get("name", "")) or {}
            readiness = da.get("readiness_for_synthesis", "UNKNOWN")
            rel_score = da.get("evidence_relevance_score", 0.0)
            missing = da.get("missing_evidence", [])
            deliv_findings[dk] = {
                "name": d["name"],
                "readiness": readiness,
                "relevance_score": rel_score,
                "missing_categories": missing,
            }
            if readiness in ("INSUFFICIENT", "BLOCKED") or rel_score < 0.20:
                critical_gaps.append(
                    f"Deliverable '{d['name']}' has insufficient evidence (status={readiness}, relevance={rel_score:.2f}). Missing: {', '.join(missing) or 'relevant evidence'}"
                )

    # 5. Objective Subject Alignment Check
    # Ensure evidence does not completely mismatch the user objective (e.g. county budget vs environmental grants)
    obj_subject_terms = [
        w.lower() for w in re.findall(r"\b[A-Za-z][A-Za-z0-9\-]{3,}\b", obj)
        if w.lower() not in {"produce", "research", "deliver", "project", "work", "opportunities", "county"}
    ]
    # Check if critical subject terms were represented in coverage or evidence
    if critical_gaps and len(critical_gaps) >= max(1, len(delivs) // 2):
        unfulfilled_criteria.append("Retrieved evidence fails to ground core deliverable requirements; critical evidence gaps remain.")

    # 6. Synthesize substantive completion status
    substantive_status: str
    substantively_fulfilled: bool
    score: float

    has_research_tasks = any(
        t.get("action", {}).get("step", "").startswith("research_")
        or t.get("action", {}).get("type") == "research_fetch"
        for t in plan.get("tasks", [])
    )

    is_research_project = has_research_tasks or bool(coverage_assessment)
    if is_research_project:
        unfulfilled_criteria.append("Trusted claim-evidence certification is unavailable; research acceptance held.")

    if is_research_project:
        substantive_status = "BLOCKED"
        substantively_fulfilled = False
        score = 0.0
    else:
        if tech_success and deliv_success and not unfulfilled_criteria:
            substantive_status = "FULFILLED"
            substantively_fulfilled = True
            score = 1.0
            fulfilled_criteria.append("All non-research task deliverables verified against criteria.")
        else:
            substantive_status = "UNFULFILLED"
            substantively_fulfilled = False
            score = 0.0
    gap_summary = "; ".join(critical_gaps + unfulfilled_criteria) or "No critical gaps identified."
    rec_action = (
        "Project deliverables meet all substantive criteria."
        if substantively_fulfilled
        else "Operator review required; targeted research or capability provision needed before project completion."
    )

    return {
        "technical_execution_success": tech_success,
        "deliverable_acceptance_success": deliv_success,
        "substantively_fulfilled": substantively_fulfilled,
        "substantive_status": substantive_status,
        "fulfillment_score": score,
        "fulfilled_criteria": fulfilled_criteria,
        "unfulfilled_criteria": unfulfilled_criteria,
        "coverage_overall_status": cov_status,
        "deliverable_findings": deliv_findings,
        "critical_gaps": critical_gaps,
        "gap_summary": gap_summary,
        "recommended_action": rec_action,
    }



