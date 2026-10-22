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
import csv
import io
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from app.config import settings
from app.database import get_db_connection
from app.services.orchestration import script_library
from app.services.orchestration.common import jload
from app.services.orchestration.task_graph import scan_text_for_injection

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

    goal_checks: List[Dict[str, Any]] = [
        _check("not_prompt_echo", not_echo, "output is not an echo of prompt" if not_echo else "prompt echo detected", layer="goal"),
    ]
    if suspicious:
        goal_checks.append(_check("not_prompt_injection", False, "flagged suspicious instruction-like content", layer="goal"))
    else:
        goal_checks.append(_check("not_prompt_injection", True, "no suspicious instruction patterns", layer="goal"))

    all_checks = schema_checks + execution_checks + goal_checks
    task_passed = (
        all(c["passed"] for c in schema_checks)
        and all(c["passed"] for c in execution_checks)
        and not_echo
    )
    decision = "ACCEPTED" if task_passed and not suspicious else ("REJECTED" if not task_passed else "NEEDS_HUMAN_REVIEW")

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
) -> Dict[str, Any]:
    """3-layer evaluation for generated reports and documents."""
    schema_checks: List[Dict[str, Any]] = []
    execution_checks: List[Dict[str, Any]] = []
    goal_checks: List[Dict[str, Any]] = []

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

    all_checks = schema_checks + execution_checks + goal_checks
    all_passed = all(c["passed"] for c in all_checks)
    is_blocked = any(c["name"] == "measured_telemetry_available" and not c["passed"] for c in goal_checks)
    
    if all_passed:
        decision = "ACCEPTED"
    elif is_blocked:
        decision = "BLOCKED"
    elif not claims_grounded:
        decision = "REJECTED"
    elif requires_citations and citation_rejection_reason:
        decision = "REJECTED"
    elif requires_citations and unverified_citations:
        decision = "NEEDS_HUMAN_REVIEW"
    else:
        decision = "REJECTED"

    return {
        "accepted": all_passed,
        "decision": decision,
        "schema_checks": schema_checks,
        "execution_checks": execution_checks,
        "goal_checks": goal_checks,
        "checks": all_checks,
    }


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

    if dtype in ("software_artifact", "code"):
        return evaluate_code_deliverable(
            code=content_bytes,
            filename=primary_art.get("filename", "main.py"),
            test_code=crit_dict.get("test_code"),
            acceptance_criteria=crit_dict,
        )
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


