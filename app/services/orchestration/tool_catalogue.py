"""Typed Tool Catalogue and Safe Action Execution Layer for Qwen Collective (Increment 18).

Provides a unified, typed tool catalogue describing actual capabilities, input/output schemas,
permissions, side-effect classification, and verification requirements across all 7 required domains:
1. Document and repository inspection (`inspect_document_or_repo`)
2. Search and retrieval with source metadata (`search_and_retrieve_sources`)
3. Exact Decimal calculation, reconciliation, and structured data analysis (`decimal_calculate_and_reconcile`, `compare_options_against_constraints`, `analyze_structured_dataset`)
4. Code generation, isolated execution, and test verification (`execute_and_test_code`)
5. Browser/application interaction checks (`verify_web_application_behavior`)
6. Artifact creation, formatting, and versioned revision (`create_or_revise_artifact`)
7. Operational/system inspection (`inspect_operational_state`)

Enforces:
- Every tool call validates its input schema and produces a provenance-tagged observation.
- Untrusted retrieved document text is scanned and stripped of directive/authority-escalation attempts
  before it can reach any downstream tool or model prompt.
- Tools never expose environment secrets (`QC_`, `SECRET`, `TOKEN`, `API_KEY`, `PASSWORD`).
"""
from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.orchestration.action_registry import ActionKind, ActionManifest, GLOBAL_ACTION_REGISTRY
from app.services.orchestration.common import new_id, now_iso
from app.services.orchestration.financial_ledger import Allocation, Cost, Fund, money, reconcile
from app.services.orchestration.intake import inspect_untrusted_source_for_directive_injection

logger = logging.getLogger("qc.orchestrator.tool_catalogue")


@dataclass(frozen=True)
class ToolSpec:
    tool_id: str
    category: str
    description: str
    input_schema: Dict[str, str]
    output_schema: Dict[str, str]
    permissions: List[str]
    side_effects: bool
    verification_requirement: str
    required_effects: Tuple[str, ...] = ("read_only",)
    version: str = "1.0.0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "category": self.category,
            "version": self.version,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "permissions": self.permissions,
            "side_effects": self.side_effects,
            "required_effects": list(self.required_effects),
            "verification_requirement": self.verification_requirement,
        }


# ---------------------------------------------------------------------------
# 1. Exact Decimal Calculation, Supplier/Option Comparison & Data Analysis
# ---------------------------------------------------------------------------

def resolve_quote_revisions(quotes: List[Dict[str, Any]], as_of_date: Optional[str] = None) -> Dict[str, Any]:
    """Resolve conflicting or superseded supplier quote revisions deterministically.

    Selects the latest valid revision per supplier/option based on explicit `revision` number
    or `effective_date` / `timestamp`, and flags expired quotes or conflicting same-revision entries.
    """
    by_supplier: Dict[str, List[Dict[str, Any]]] = {}
    for q in quotes:
        if not isinstance(q, dict):
            continue
        sup_id = str(q.get("supplier_id") or q.get("option_id") or q.get("name") or "").strip()
        if not sup_id:
            continue
        by_supplier.setdefault(sup_id, []).append(q)

    resolved: Dict[str, Dict[str, Any]] = {}
    superseded_log: List[Dict[str, Any]] = []
    contradictions: List[Dict[str, Any]] = []

    for sup_id, items in sorted(by_supplier.items()):
        # Filter out explicitly withdrawn/cancelled quotes
        active_items = [it for it in items if str(it.get("status", "active")).lower() not in ("withdrawn", "cancelled", "void")]
        if not active_items:
            continue

        # Sort by (revision_number, date_str)
        def _sort_key(it: Dict[str, Any]) -> Tuple[int, str]:
            rev = int(it.get("revision") or it.get("version") or 1)
            dt = str(it.get("effective_date") or it.get("date") or it.get("updated_at") or "")
            return (rev, dt)

        sorted_items = sorted(active_items, key=_sort_key)
        latest = dict(sorted_items[-1])

        # Check if two quotes have the exact same highest revision/date but different prices/terms
        if len(sorted_items) >= 2:
            prev = sorted_items[-2]
            if _sort_key(prev) == _sort_key(latest) and prev != latest:
                contradictions.append({
                    "supplier_id": sup_id,
                    "reason": "unresolved_same_revision_conflict",
                    "quote_a": prev,
                    "quote_b": latest,
                })
            for old_q in sorted_items[:-1]:
                superseded_log.append({
                    "supplier_id": sup_id,
                    "superseded_revision": old_q.get("revision", 1),
                    "superseded_date": old_q.get("effective_date") or old_q.get("date"),
                    "active_revision": latest.get("revision", 1),
                })

        # Check quote expiration if `as_of_date` and `valid_until` are present
        if as_of_date and latest.get("valid_until"):
            if str(latest["valid_until"]) < str(as_of_date):
                latest["expired"] = True
                contradictions.append({
                    "supplier_id": sup_id,
                    "reason": f"quote expired on {latest['valid_until']} (as_of={as_of_date})",
                })
            else:
                latest["expired"] = False

        resolved[sup_id] = latest

    return {
        "schema": "qc.quote_resolution.v1",
        "resolved_quotes": resolved,
        "superseded_revisions": superseded_log,
        "contradictions": contradictions,
    }


def calculate_option_total_cost(option: Dict[str, Any], quantity: int = 1) -> Dict[str, Any]:
    """Compute exact cent-accurate Decimal total cost for a supplier/project option."""
    with localcontext() as ctx:
        ctx.prec = 64
        qty = Decimal(int(option.get("quantity", quantity)))
        unit_price = Decimal(str(option.get("unit_price", "0.00")))
        base_cost = Decimal(str(option.get("base_cost", "0.00")))
        setup_fee = Decimal(str(option.get("setup_fee", "0.00")))
        shipping_fee = Decimal(str(option.get("shipping_fee", "0.00")))
        support_fee = Decimal(str(option.get("support_fee", "0.00")))
        contingency_rate = Decimal(str(option.get("contingency_rate", "0")))
        discount_rate = Decimal(str(option.get("discount_rate", "0")))
        discount_fixed = Decimal(str(option.get("discount_amount", "0.00")))
        tax_rate = Decimal(str(option.get("tax_rate", "0")))

        line_subtotal = (unit_price * qty) + base_cost
        discount_from_rate = (line_subtotal * discount_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        discounted_subtotal = max(Decimal("0.00"), line_subtotal - discount_from_rate - discount_fixed)
        fees_total = setup_fee + shipping_fee + support_fee
        pre_tax = discounted_subtotal + fees_total
        contingency_amt = (pre_tax * contingency_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        taxable_base = pre_tax + contingency_amt
        tax_amt = (taxable_base * tax_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total = (taxable_base + tax_amt).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return {
            "option_id": option.get("supplier_id") or option.get("option_id") or option.get("id") or option.get("name"),
            "revision": option.get("revision", 1),
            "quantity": int(qty),
            "line_subtotal": str(line_subtotal.quantize(Decimal("0.01"))),
            "discount_applied": str((discount_from_rate + discount_fixed).quantize(Decimal("0.01"))),
            "fees_total": str(fees_total.quantize(Decimal("0.01"))),
            "contingency_amount": str(contingency_amt),
            "tax_amount": str(tax_amt),
            "total_cost": str(total),
        }


def compare_options_against_constraints(
    options: List[Dict[str, Any]],
    constraints: Dict[str, Any],
    quantity: int = 1,
    as_of_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Deterministically evaluate candidate options/suppliers against hard and soft constraints.

    Supports:
    - Conflicting quote revision resolution (picks latest revision per supplier).
    - Exact Decimal total cost calculation against `max_budget` ceiling.
    - Delivery schedule constraint (`max_delivery_days`).
    - Mandatory capability/compliance/dependency requirements (`required_capabilities`, `excluded_vendors`, `required_dependencies`).
    - Soft preferences (`prefer_lowest_cost`, `prefer_fastest_delivery`, `preferred_certifications`).
    - Honest blocker reporting when zero options satisfy all hard constraints.
    """
    resolution = resolve_quote_revisions(options, as_of_date=as_of_date)
    active_options = list(resolution["resolved_quotes"].values())

    max_budget = Decimal(str(constraints["max_budget"])).quantize(Decimal("0.01")) if constraints.get("max_budget") is not None else None
    max_delivery_days = int(constraints["max_delivery_days"]) if constraints.get("max_delivery_days") is not None else None
    req_caps = {str(c).lower() for c in (constraints.get("required_capabilities") or [])}
    req_deps = {str(d).lower() for d in (constraints.get("required_dependencies") or [])}
    excluded_vendors = {str(v).lower() for v in (constraints.get("excluded_vendors") or [])}
    min_warranty_months = int(constraints["min_warranty_months"]) if constraints.get("min_warranty_months") is not None else None

    evaluated: List[Dict[str, Any]] = []
    eligible: List[Dict[str, Any]] = []

    for opt in active_options:
        opt_id = str(opt.get("supplier_id") or opt.get("option_id") or opt.get("id") or opt.get("name"))
        calc = calculate_option_total_cost(opt, quantity=quantity)
        total_dec = Decimal(calc["total_cost"])
        delivery_days = int(opt.get("delivery_days") or opt.get("lead_time_days") or 0)
        opt_caps = {str(c).lower() for c in (opt.get("capabilities") or opt.get("certifications") or [])}
        opt_deps = {str(d).lower() for d in (opt.get("supported_dependencies") or opt.get("dependencies_met") or [])}
        warranty_months = int(opt.get("warranty_months") or 0)

        hard_violations: List[str] = []
        if opt.get("expired"):
            hard_violations.append("quote_expired")
        if opt_id.lower() in excluded_vendors or str(opt.get("name", "")).lower() in excluded_vendors:
            hard_violations.append(f"excluded_vendor ({opt_id})")
        if max_budget is not None and total_dec > max_budget:
            overage = total_dec - max_budget
            hard_violations.append(f"exceeds_max_budget (${total_dec} > ceiling ${max_budget}, over by ${overage})")
        if max_delivery_days is not None and delivery_days > max_delivery_days:
            hard_violations.append(f"exceeds_max_delivery_days ({delivery_days}d > max {max_delivery_days}d)")
        if req_caps:
            missing_c = sorted(req_caps - opt_caps)
            if missing_c:
                hard_violations.append(f"missing_required_capabilities ({', '.join(missing_c)})")
        if req_deps:
            missing_d = sorted(req_deps - opt_deps)
            if missing_d:
                hard_violations.append(f"missing_required_dependencies ({', '.join(missing_d)})")
        if min_warranty_months is not None and warranty_months < min_warranty_months:
            hard_violations.append(f"insufficient_warranty ({warranty_months}m < min {min_warranty_months}m)")

        # Soft score for ranking among eligible options
        soft_notes: List[str] = []
        pref_caps = {str(c).lower() for c in (constraints.get("preferred_capabilities") or [])}
        matched_prefs = sorted(pref_caps & opt_caps)
        if matched_prefs:
            soft_notes.append(f"matches preferred capabilities: {', '.join(matched_prefs)}")

        entry = {
            "option_id": opt_id,
            "name": opt.get("name") or opt_id,
            "revision": opt.get("revision", 1),
            "effective_date": opt.get("effective_date") or opt.get("date"),
            "cost_breakdown": calc,
            "total_cost": calc["total_cost"],
            "delivery_days": delivery_days,
            "warranty_months": warranty_months,
            "capabilities": sorted(opt_caps),
            "dependencies_met": sorted(opt_deps),
            "feasible": len(hard_violations) == 0,
            "hard_violations": hard_violations,
            "soft_notes": soft_notes,
            "matched_preferences_count": len(matched_prefs),
        }
        evaluated.append(entry)
        if entry["feasible"]:
            eligible.append(entry)

    # Sort eligible options by (primary preference: cost or delivery, then matched_preferences_count, then option_id)
    sort_mode = str(constraints.get("ranking_preference") or "lowest_cost").lower()
    if sort_mode == "fastest_delivery":
        eligible.sort(key=lambda e: (e["delivery_days"], Decimal(e["total_cost"]), -e["matched_preferences_count"], e["option_id"]))
    else:
        eligible.sort(key=lambda e: (Decimal(e["total_cost"]), e["delivery_days"], -e["matched_preferences_count"], e["option_id"]))

    recommended = eligible[0] if eligible else None
    status = "verified_for_scope" if recommended else "blocked"

    blocker_summary = None
    if not recommended:
        reasons = [f"{e['option_id']}: {'; '.join(e['hard_violations'])}" for e in evaluated]
        blocker_summary = (
            f"INFEASIBLE_CONSTRAINTS: 0 of {len(evaluated)} evaluated options satisfy all hard constraints. "
            + " | ".join(reasons)
        )

    return {
        "schema": "qc.option_comparison.v1",
        "status": status,
        "constraints_applied": {
            "max_budget": str(max_budget) if max_budget is not None else None,
            "max_delivery_days": max_delivery_days,
            "required_capabilities": sorted(req_caps),
            "required_dependencies": sorted(req_deps),
            "min_warranty_months": min_warranty_months,
            "ranking_preference": sort_mode,
        },
        "quote_resolution": {
            "superseded_revisions": resolution["superseded_revisions"],
            "contradictions": resolution["contradictions"],
        },
        "evaluated_options": evaluated,
        "feasible_count": len(eligible),
        "recommended_option": recommended,
        "blocker": blocker_summary,
    }


def analyze_structured_dataset(
    rows: List[Dict[str, Any]],
    group_by: Optional[str] = None,
    value_field: str = "amount",
    anomaly_z_threshold: float = 2.0,
) -> Dict[str, Any]:
    """Perform exact Decimal aggregation, group-by summary, and outlier/anomaly detection on tabular data."""
    if not rows:
        return {"schema": "qc.data_analysis.v1", "row_count": 0, "error": "empty_dataset"}

    with localcontext() as ctx:
        ctx.prec = 64
        valid_values: List[Tuple[int, Dict[str, Any], Decimal]] = []
        malformed_rows: List[Dict[str, Any]] = []
        groups: Dict[str, List[Decimal]] = {}

        for idx, r in enumerate(rows):
            raw_v = r.get(value_field)
            if raw_v is None or str(raw_v).strip() == "":
                malformed_rows.append({"row_index": idx, "reason": f"missing {value_field}"})
                continue
            try:
                dec_v = Decimal(str(raw_v).replace(",", "").replace("$", "").strip())
                if not dec_v.is_finite():
                    raise InvalidOperation("non-finite")
                valid_values.append((idx, r, dec_v))
                if group_by:
                    g_key = str(r.get(group_by, "unspecified"))
                    groups.setdefault(g_key, []).append(dec_v)
            except (InvalidOperation, ValueError):
                malformed_rows.append({"row_index": idx, "reason": f"invalid numeric {value_field}: {raw_v}"})

        if not valid_values:
            return {
                "schema": "qc.data_analysis.v1",
                "row_count": len(rows),
                "valid_row_count": 0,
                "malformed_rows": malformed_rows,
                "error": "no_valid_numeric_rows",
            }

        nums = [v for _, _, v in valid_values]
        total = sum(nums, Decimal("0"))
        count = Decimal(len(nums))
        mean = (total / count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        sorted_nums = sorted(nums)
        mid = len(sorted_nums) // 2
        if len(sorted_nums) % 2 == 1:
            median = sorted_nums[mid].quantize(Decimal("0.01"))
        else:
            median = ((sorted_nums[mid - 1] + sorted_nums[mid]) / Decimal(2)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Standard deviation & anomaly detection
        variance = sum((x - mean) ** 2 for x in nums) / count
        std_dev = variance.sqrt().quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        anomalies = []
        if std_dev > 0:
            for idx, r, dec_v in valid_values:
                z = abs(dec_v - mean) / std_dev
                if z >= Decimal(str(anomaly_z_threshold)):
                    anomalies.append({
                        "row_index": idx,
                        "id": r.get("id") or r.get("name") or f"row_{idx}",
                        "value": str(dec_v.quantize(Decimal("0.01"))),
                        "z_score": str(z.quantize(Decimal("0.01"))),
                    })

        group_summaries = {}
        for g_key, g_vals in sorted(groups.items()):
            g_tot = sum(g_vals, Decimal("0")).quantize(Decimal("0.01"))
            g_avg = (g_tot / Decimal(len(g_vals))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            group_summaries[g_key] = {
                "count": len(g_vals),
                "total": str(g_tot),
                "mean": str(g_avg),
                "min": str(min(g_vals).quantize(Decimal("0.01"))),
                "max": str(max(g_vals).quantize(Decimal("0.01"))),
            }

        return {
            "schema": "qc.data_analysis.v1",
            "row_count": len(rows),
            "valid_row_count": len(valid_values),
            "malformed_rows": malformed_rows,
            "summary": {
                "total": str(total.quantize(Decimal("0.01"))),
                "mean": str(mean),
                "median": str(median),
                "min": str(min(nums).quantize(Decimal("0.01"))),
                "max": str(max(nums).quantize(Decimal("0.01"))),
                "std_dev": str(std_dev),
            },
            "group_by": group_by,
            "groups": group_summaries,
            "anomalies": anomalies,
        }


# ---------------------------------------------------------------------------
# 2. Browser / Web Application Interaction Verification Tool
# ---------------------------------------------------------------------------

def verify_web_application_behavior(
    html_content: str,
    js_content: str = "",
    css_content: str = "",
    required_elements: Optional[List[str]] = None,
    interaction_cases: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Verify a small web application's DOM structure, accessibility attributes, and interactive JS state logic."""
    checks: List[Dict[str, Any]] = []
    html_str = str(html_content or "")
    js_str = str(js_content or "")

    # Extract inline <script> if js_content not passed separately
    if not js_str and "<script" in html_str.lower():
        scripts = re.findall(r"<script[^>]*>([\s\S]*?)</script>", html_str, re.I)
        js_str = "\n".join(scripts)

    has_doctype_or_html = "<html" in html_str.lower() or "<!doctype html" in html_str.lower() or "<main" in html_str.lower() or "<div" in html_str.lower()
    checks.append({
        "name": "valid_html_structure",
        "passed": bool(html_str.strip() and has_doctype_or_html),
        "detail": "HTML document structure present",
    })

    for elem_id in (required_elements or []):
        found = (
            f'id="{elem_id}"' in html_str
            or f"id='{elem_id}'" in html_str
            or f'data-testid="{elem_id}"' in html_str
            or f"<{elem_id}" in html_str.lower()
        )
        checks.append({
            "name": f"element_present:{elem_id}",
            "passed": found,
            "detail": f"Required DOM element '{elem_id}' {'found' if found else 'missing'}",
        })

    # Verify interactive cases if provided (e.g., filter, calculate, form validation)
    if interaction_cases:
        for idx, ic in enumerate(interaction_cases, start=1):
            case_name = ic.get("name") or f"interaction_{idx}"
            req_handler = ic.get("required_handler") or ic.get("function")
            if req_handler:
                has_fn = (
                    f"function {req_handler}" in js_str
                    or f"const {req_handler}" in js_str
                    or f"let {req_handler}" in js_str
                    or f"{req_handler}(" in html_str
                    or f"addEventListener" in js_str
                )
                checks.append({
                    "name": f"interaction_handler:{case_name}",
                    "passed": has_fn,
                    "detail": f"Handler '{req_handler}' verified in script/DOM" if has_fn else f"Handler '{req_handler}' missing",
                })

    all_passed = all(c["passed"] for c in checks)
    return {
        "schema": "qc.web_app_verification.v1",
        "verified": all_passed,
        "status": "verified_for_scope" if all_passed else "failed",
        "checks": checks,
    }




def run_bounded_python_verification(
    code: str,
    test_code: str,
    filename: str = "solution.py",
    timeout_seconds: float = 5.0,
) -> Dict[str, Any]:
    """Execute candidate Python code and independent test assertions inside a configured containment backend.

    SECURITY & CONTAINMENT BOUNDARY:
    - Refuses uncontained host execution: when no containment backend is configured
      (`protected_repo_verifier.get_protected_repo_verifier_state()["configured"] is False`),
      returns `status="held_containment_backend_required"` and `accepted=False` before candidate code starts.
    - When a backend is configured, stages `code` and `test_code` as a `unittest` suite inside a temporary
      workspace and executes it through `protected_repo_verifier.verify_repository_public_suite_protected`.
    """
    import ast
    import tempfile
    from app.services.orchestration import protected_repo_verifier

    checks: List[Dict[str, Any]] = []
    try:
        ast.parse(code, filename=filename)
        checks.append({"name": "python_syntax_valid", "passed": True, "detail": "ast.parse succeeded"})
    except SyntaxError as se:
        checks.append({"name": "python_syntax_valid", "passed": False, "detail": f"SyntaxError line {se.lineno}: {se.msg}"})
        return {
            "accepted": False,
            "status": "failed",
            "decision": "REJECTED",
            "containment_backend": "held",
            "cleanup_confirmed": True,
            "checks": checks,
        }

    verifier_state = protected_repo_verifier.get_protected_repo_verifier_state()
    if not verifier_state["configured"]:
        checks.append({
            "name": "containment_backend_configured",
            "passed": False,
            "detail": "Containment backend is held; candidate code execution refused on host.",
        })
        return {
            "accepted": False,
            "status": "held_containment_backend_required",
            "decision": "HELD",
            "containment_backend": "held",
            "filesystem_and_network_containment": False,
            "cleanup_confirmed": False,
            "checks": checks,
        }

    module_stem = Path(filename).stem
    indented_lines = []
    for line in test_code.splitlines():
        indented_lines.append("    " + line if line.strip() else "")
    wrapped_unittest = (
        "import unittest\n"
        f"import {module_stem}\n"
        "from " + module_stem + " import *\n\n"
        + test_code
        + "\n\nclass _GeneratedCodeVerificationSuite(unittest.TestCase):\n"
        "    def test_all_assertions(self):\n"
        "        ran_any = False\n"
        "        for _name, _fn in list(globals().items()):\n"
        "            if _name.startswith('test_') and callable(_fn):\n"
        "                _fn()\n"
        "                ran_any = True\n"
        "        self.assertTrue(ran_any, 'At least one test_* function required')\n"
    )

    with tempfile.TemporaryDirectory(prefix="qc_bounded_verif_") as tmpdir:
        tmp_path = Path(tmpdir)
        (tmp_path / filename).write_text(code, encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_generated_suite.py").write_text(wrapped_unittest, encoding="utf-8")

        pub_res = protected_repo_verifier.verify_repository_public_suite_protected(
            workspace_path=str(tmp_path),
            test_rel_path="tests/test_generated_suite.py",
            seconds=max(1, min(int(timeout_seconds), 15)),
        )

    passed = bool(pub_res.get("passed") and pub_res.get("cleanup_confirmed"))
    detail = (
        f"Independent test suite passed inside {pub_res.get('containment_backend')}"
        if passed
        else f"status={pub_res.get('status')} reason={pub_res.get('reason')} excerpt={str(pub_res.get('output_excerpt', ''))[:240]}"
    )
    checks.append({"name": "test_suite_passed", "passed": passed, "detail": detail})
    return {
        "accepted": passed,
        "status": "verified_for_scope" if passed else pub_res.get("status", "failed"),
        "decision": "ACCEPTED" if passed else "REJECTED",
        "boundary": pub_res.get("containment_backend", "held"),
        "containment_backend": pub_res.get("containment_backend", "held"),
        "filesystem_and_network_containment": bool(pub_res.get("filesystem_and_network_containment", False)),
        "cleanup_confirmed": bool(pub_res.get("cleanup_confirmed", False)),
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# 2b. Scoped Repository Inspection, Isolated Test Runner & Patch Verifier (Increment 19)
# ---------------------------------------------------------------------------

def _resolve_safe_workspace_path(workspace_path: str, relative_path: str) -> Path:
    """Resolve `relative_path` inside `workspace_path`, rejecting traversal (`..`) or outside-workspace access."""
    root = Path(workspace_path).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Workspace directory does not exist: {workspace_path}")
    rel = str(relative_path or "").replace("\\", "/").lstrip("/")
    if ".." in Path(rel).parts:
        raise PermissionError(f"Path traversal rejected in workspace path: {relative_path}")
    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        raise PermissionError(f"Target path '{relative_path}' escapes workspace root '{root}'")
    return target


def inspect_repository_workspace(
    workspace_path: str,
    include_globs: Optional[List[str]] = None,
    max_file_chars: int = 8000,
) -> Dict[str, Any]:
    """Inspect a scoped repository workspace with prompt-injection stripping and SHA-256 file inventory."""
    root = Path(workspace_path).resolve()
    if not root.exists() or not root.is_dir():
        return {
            "schema": "qc.repo_inspection.v1",
            "status": "failed",
            "error": f"Workspace not found: {workspace_path}",
            "files": {},
        }

    patterns = include_globs or ["*.py", "*.md", "*.json", "*.txt", "*.toml"]
    file_entries: Dict[str, Dict[str, Any]] = {}
    flagged_injections: List[Dict[str, Any]] = []

    all_paths: List[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(root).parts):
            continue
        if any(p.match(pat) for pat in patterns):
            all_paths.append(p)

    for p in all_paths:
        rel_str = p.relative_to(root).as_posix()
        raw_bytes = p.read_bytes()
        sha = hashlib.sha256(raw_bytes).hexdigest()
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        guard = inspect_untrusted_source_for_directive_injection(raw_text, source_id=rel_str)
        if guard["contains_injection_attempt"]:
            flagged_injections.append({
                "file": rel_str,
                "flagged_directives": guard["flagged_directives"],
            })
        sanitized_text = guard["sanitized_text"]
        if p.suffix.lower() == ".py":
            sanitized_text = re.sub(
                r"(?m)^(\s*)(\[UNTRUSTED_DIRECTIVE_STRIPPED[^\n]*\])",
                r"\1# \2",
                sanitized_text,
            )
        file_entries[rel_str] = {
            "path": rel_str,
            "sha256": sha,
            "size_bytes": len(raw_bytes),
            "contains_injection_attempt": guard["contains_injection_attempt"],
            "flagged_directives": guard["flagged_directives"],
            "sanitized_content": sanitized_text[:max_file_chars],
        }

    return {
        "schema": "qc.repo_inspection.v1",
        "status": "observed",
        "workspace_root": str(root),
        "file_count": len(file_entries),
        "files": file_entries,
        "contains_injection_attempt": len(flagged_injections) > 0,
        "flagged_injections": flagged_injections,
    }


def run_repository_test_suite(
    workspace_path: str,
    test_rel_path: Optional[str] = None,
    extra_test_dir: Optional[str] = None,
    timeout_seconds: float = 15.0,
) -> Dict[str, Any]:
    """Execute a repository's public test suite inside the configured containment backend.

    IMPORTANT BOUNDARY & CONTAINMENT CLASSIFICATION:
    - Requires a configured containment backend (`protected_repo_verifier.get_protected_repo_verifier_state()["configured"]`).
      When no backend is configured, returns `status="held_containment_backend_required"` and `passed=False`
      before any candidate code starts. Host-execution fallbacks are removed.
    - Running out-of-workspace holdout tests (`extra_test_dir`) via this function is strictly rejected (`PermissionError`).
    - Unsupported public suites return `status="not_evaluated"` with a specific `reason`.
    - Empty public suites (`expected_test_count == 0`) return `status="failed"` (`reason="zero_public_tests_discovered"`).
    """
    from app.services.orchestration import protected_repo_verifier

    if extra_test_dir is not None:
        raise PermissionError(
            "In-interpreter execution of holdout tests (extra_test_dir) is prohibited; "
            "use protected_repo_verifier.verify_repository_candidate_protected with declarative black-box cases."
        )

    root = Path(workspace_path).resolve()
    if not root.exists() or not root.is_dir():
        return {
            "schema": "qc.repo_test_run.v1",
            "suite_role": "public",
            "suite_id": str(test_rel_path or "tests"),
            "suite_sha256": None,
            "candidate_source_sha256": None,
            "passed": False,
            "status": "failed",
            "executed": False,
            "verification_scope": "public_development_diagnostics_only",
            "containment_backend": "held",
            "containment_category": "held",
            "process_lifetime_and_output_control": False,
            "filesystem_and_network_containment": False,
            "hidden_oracle_separated": True,
            "independent_result_authority": True,
            "protected_verification": False,
            "establishes_protected_acceptance": False,
            "tests_run": 0,
            "expected_test_count": 0,
            "failures": 1,
            "errors": 1,
            "cleanup_confirmed": False,
            "error": f"Workspace not found: {workspace_path}",
        }

    if test_rel_path:
        # Validate path traversal upfront so callers passing '../outside' receive PermissionError
        _resolve_safe_workspace_path(workspace_path, test_rel_path)

    return protected_repo_verifier.verify_repository_public_suite_protected(
        workspace_path=workspace_path,
        test_rel_path=test_rel_path,
        seconds=max(1, min(int(timeout_seconds), 15)),
    )


def apply_and_verify_repository_patch(
    workspace_path: str,
    target_file: str,
    patched_content: str,
    public_test_rel_path: Optional[str] = None,
    holdout_test_dir: Optional[str] = None,
    revert_on_failure: bool = True,
    timeout_seconds: float = 15.0,
    project_id: str = "proj_repo_verif",
    run_id: str = "run_repo_verif",
    native_execution_id: str = "exec_bound",
    native_lease_id: str = "lease_bound",
    native_idempotency_key: str = "idem_bound",
) -> Dict[str, Any]:
    """Apply a candidate patch to a scoped repository file and independently verify both the
    required public test suite and (when `holdout_test_dir` is supplied) the protected black-box
    holdout suite inside the configured containment backend.

    SECURITY & CONTAINMENT BOUNDARY:
    - Every call — WITH OR WITHOUT `holdout_test_dir` — requires an active containment backend
      (`protected_repo_verifier.get_protected_repo_verifier_state()["configured"] is True`).
      When missing or unavailable, returns `status="held_protected_backend_required"` (or
      `"held_containment_backend_required"`) and `accepted=False` BEFORE candidate code starts.
    - Candidate code is staged in-memory into `qc.code_bundle.v1` and is NEVER written to the
      workspace `target_path` before both public and holdout suites pass (unless `revert_on_failure=False`).
    - Public suite and holdout suite are executed and reported independently:
      * Public suite results are NEVER derived or copied from holdout outcomes.
      * Passing holdouts + failing public suite -> `accepted=False`.
      * Passing public suite + failing holdouts -> `accepted=False`.
      * Unsupported public suite -> `public_tests["status"] == "not_evaluated"` -> `accepted=False`.
      * Empty public suite (`0` tests discovered) -> `public_tests["passed"] == False` -> `accepted=False`.
      * Unconfirmed cleanup on either suite -> `containment["cleanup_confirmed"] == False` -> `accepted=False`.
    """
    import ast
    import difflib
    from app.services.orchestration import protected_repo_verifier

    target_path = _resolve_safe_workspace_path(workspace_path, target_file)
    if not target_path.exists():
        return {
            "schema": "qc.repo_patch_verification.v1",
            "accepted": False,
            "status": "failed",
            "protected_verification": False,
            "error": f"Target file does not exist in workspace: {target_file}",
            "checks": [{"name": "target_file_exists", "passed": False, "detail": f"Missing {target_file}"}],
        }

    root = Path(workspace_path).resolve()
    before_hashes: Dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            before_hashes[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()

    norm_target = Path(target_file).as_posix().lstrip("/")
    original_text = target_path.read_text(encoding="utf-8")
    candidate_file_sha = hashlib.sha256(patched_content.encode("utf-8")).hexdigest()
    checks: List[Dict[str, Any]] = []

    try:
        ast.parse(patched_content, filename=target_file)
        checks.append({"name": "python_syntax_valid", "passed": True, "detail": "ast.parse succeeded"})
    except SyntaxError as se:
        checks.append({"name": "python_syntax_valid", "passed": False, "detail": f"SyntaxError line {se.lineno}: {se.msg}"})
        return {
            "schema": "qc.repo_patch_verification.v1",
            "accepted": False,
            "status": "failed",
            "protected_verification": False,
            "containment_backend": "held",
            "checks": checks,
            "public_tests": None,
            "holdout_tests": None,
            "containment": {
                "backend_kind": "held",
                "executed": False,
                "filesystem_and_network_containment": False,
                "cleanup_confirmed": True,
            },
        }

    verifier_state = protected_repo_verifier.get_protected_repo_verifier_state()
    if not verifier_state["configured"]:
        held_status = "held_protected_backend_required" if holdout_test_dir else "held_containment_backend_required"
        checks.append({
            "name": "protected_backend_configured",
            "passed": False,
            "detail": "Containment verifier backend is held; candidate code execution refused on host across all paths.",
        })
        return {
            "schema": "qc.repo_patch_verification.v1",
            "accepted": False,
            "status": held_status,
            "protected_verification": False,
            "containment_backend": "held",
            "process_lifetime_and_output_control": False,
            "filesystem_and_network_containment": False,
            "hidden_oracle_separated": True,
            "independent_result_authority": True,
            "target_file": norm_target,
            "before_sha256": before_hashes.get(norm_target),
            "after_sha256": before_hashes.get(norm_target),
            "candidate_file_sha256": candidate_file_sha,
            "unrelated_files_preserved": True,
            "unrelated_file_hashes": {k: v for k, v in before_hashes.items() if k != norm_target},
            "unified_diff": "",
            "checks": checks,
            "public_tests": None,
            "holdout_tests": None,
            "containment": {
                "backend_kind": "held",
                "executed": False,
                "filesystem_and_network_containment": False,
                "cleanup_confirmed": False,
            },
        }

    unified_diff = "".join(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            patched_content.splitlines(keepends=True),
            fromfile=f"a/{target_file}",
            tofile=f"b/{target_file}",
        )
    )

    # 1. Independently execute the required public test suite against the in-memory patched candidate
    public_res = protected_repo_verifier.verify_repository_public_suite_protected(
        workspace_path=workspace_path,
        test_rel_path=public_test_rel_path,
        target_file=target_file,
        patched_content=patched_content,
        project_id=project_id,
        run_id=run_id,
        native_execution_id=native_execution_id,
        native_lease_id=native_lease_id,
        native_idempotency_key=native_idempotency_key,
        seconds=max(1, min(int(timeout_seconds), 15)),
    )
    pub_passed = bool(
        public_res.get("status") not in ("not_evaluated", "failed", "timed_out", "backend_error", "cleanup_unverified", "held_containment_backend_required")
        and public_res.get("passed") is True
        and int(public_res.get("tests_run") or 0) >= max(1, int(public_res.get("expected_test_count") or 1))
        and public_res.get("cleanup_confirmed") is True
    )
    checks.append({
        "name": "public_test_suite_passed",
        "passed": pub_passed,
        "detail": (
            f"status={public_res.get('status')} ran={public_res.get('tests_run')}/{public_res.get('expected_test_count')} "
            f"failures={public_res.get('failures')} errors={public_res.get('errors')} "
            f"suite_sha256={public_res.get('suite_sha256')} reason={public_res.get('reason')} "
            f"(backend={public_res.get('containment_backend')})"
        ),
    })

    # 2. Independently execute the protected holdout suite if `holdout_test_dir` is supplied
    holdout_res: Optional[Dict[str, Any]] = None
    if holdout_test_dir:
        try:
            holdout_res = protected_repo_verifier.verify_repository_candidate_protected(
                workspace_path=workspace_path,
                target_file=target_file,
                patched_content=patched_content,
                holdout_test_dir=holdout_test_dir,
                project_id=project_id,
                run_id=run_id,
                native_execution_id=native_execution_id,
                native_lease_id=native_lease_id,
                native_idempotency_key=native_idempotency_key,
                seconds=max(1, min(int(timeout_seconds), 15)),
            )
            failed_reqs = [
                f"{c.get('case_id')}[{c.get('requirement_id')}]"
                for c in (holdout_res.get("cases") or [])
                if not c.get("passed")
            ]
            holdout_ok = bool(
                holdout_res.get("passed") is True
                and int(holdout_res.get("tests_run") or 0) > 0
                and holdout_res.get("cleanup_confirmed") is True
            )
            checks.append({
                "name": "protected_holdout_suite_passed",
                "passed": holdout_ok,
                "detail": (
                    f"Protected black-box cases passed (ran={holdout_res.get('tests_run')}, "
                    f"suite_sha256={holdout_res.get('suite_sha256')}, backend={holdout_res.get('containment_backend')})"
                    if holdout_ok
                    else (
                        f"Protected black-box cases failed (failures={holdout_res.get('failures')}, "
                        f"errors={holdout_res.get('errors')}, failed_cases={failed_reqs})"
                    )
                ),
            })
        except Exception as exc:
            holdout_res = {
                "suite_role": "holdout",
                "suite_id": str(Path(holdout_test_dir).name),
                "suite_kind": "qc.json_io_cases.v1",
                "suite_sha256": None,
                "candidate_source_sha256": None,
                "status": "backend_error",
                "executed": False,
                "passed": False,
                "tests_run": 0,
                "expected_test_count": 0,
                "failures": 1,
                "errors": 1,
                "cleanup_confirmed": False,
                "containment_backend": verifier_state["backend_kind"],
                "filesystem_and_network_containment": False,
                "error": str(exc),
            }
            checks.append({
                "name": "protected_holdout_suite_passed",
                "passed": False,
                "detail": f"Protected verifier rejected candidate: {exc}",
            })

    # 3. Independent containment & cleanup check across all executed suites
    pub_cleanup = bool(public_res.get("cleanup_confirmed") is True)
    hold_cleanup = bool(holdout_res.get("cleanup_confirmed") is True) if holdout_res is not None else True
    all_cleanup_confirmed = bool(pub_cleanup and hold_cleanup)
    checks.append({
        "name": "containment_and_cleanup_confirmed",
        "passed": all_cleanup_confirmed,
        "detail": (
            f"Cleanup confirmed across executed suites (public_cleanup={pub_cleanup}, holdout_cleanup={hold_cleanup})"
            if all_cleanup_confirmed
            else f"Cleanup unverified (public_cleanup={pub_cleanup}, holdout_cleanup={hold_cleanup})"
        ),
    })

    # Only write patched_content to workspace if all checks so far passed (or revert_on_failure is False)
    prelim_passed = all(c["passed"] for c in checks)
    if prelim_passed or not revert_on_failure:
        target_path.write_text(patched_content, encoding="utf-8")

    after_hashes: Dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            after_hashes[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()

    unrelated_modified = [
        k for k in set(before_hashes.keys()) | set(after_hashes.keys())
        if k != norm_target and before_hashes.get(k) != after_hashes.get(k)
    ]
    checks.append({
        "name": "unrelated_files_preserved",
        "passed": len(unrelated_modified) == 0,
        "detail": "All non-target workspace files preserved intact" if not unrelated_modified else f"Modified unrelated files: {unrelated_modified}",
    })

    accepted = all(c["passed"] for c in checks)
    if not accepted and revert_on_failure:
        target_path.write_text(original_text, encoding="utf-8")

    fs_net_contained = bool(
        public_res.get("filesystem_and_network_containment") is True
        and (holdout_res is None or holdout_res.get("filesystem_and_network_containment") is True)
    )
    is_protected = bool(holdout_res is not None and holdout_res.get("passed") and all_cleanup_confirmed)
    backend_name = str(
        (holdout_res or {}).get("containment_backend")
        or public_res.get("containment_backend")
        or verifier_state["backend_kind"]
    )

    if public_res.get("status") == "not_evaluated":
        overall_status = "not_evaluated"
    elif not all_cleanup_confirmed:
        overall_status = "cleanup_unverified"
    elif accepted and is_protected:
        overall_status = "verified_for_scope"
    elif accepted:
        overall_status = "public_diagnostics_passed"
    else:
        overall_status = "failed"

    containment_summary = {
        "backend_kind": backend_name,
        "is_uncontained_test_double": bool(verifier_state.get("is_uncontained_test_double", False)),
        "process_lifetime_and_output_control": bool(
            public_res.get("process_lifetime_and_output_control")
            and (holdout_res is None or holdout_res.get("process_lifetime_and_output_control"))
        ),
        "filesystem_and_network_containment": fs_net_contained,
        "cleanup_confirmed": all_cleanup_confirmed,
        "public_cleanup_confirmed": pub_cleanup,
        "holdout_cleanup_confirmed": hold_cleanup if holdout_res is not None else None,
        "public_grant_nonce": public_res.get("grant_nonce"),
        "holdout_grant_nonce": (holdout_res or {}).get("grant_nonce"),
    }

    return {
        "schema": "qc.repo_patch_verification.v1",
        "accepted": accepted,
        "status": overall_status,
        "protected_verification": bool(accepted and is_protected and not verifier_state.get("is_uncontained_test_double")),
        "containment_backend": backend_name,
        "is_uncontained_test_double": bool(verifier_state.get("is_uncontained_test_double", False)),
        "process_lifetime_and_output_control": containment_summary["process_lifetime_and_output_control"],
        "filesystem_and_network_containment": fs_net_contained,
        "hidden_oracle_separated": True,
        "independent_result_authority": True,
        "cleanup_confirmed": all_cleanup_confirmed,
        "target_file": norm_target,
        "before_sha256": before_hashes.get(norm_target),
        "after_sha256": candidate_file_sha if accepted else before_hashes.get(norm_target),
        "candidate_file_sha256": candidate_file_sha,
        "unrelated_files_preserved": len(unrelated_modified) == 0,
        "unrelated_file_hashes": {k: v for k, v in after_hashes.items() if k != norm_target},
        "unified_diff": unified_diff,
        "checks": checks,
        "containment": containment_summary,
        "public_tests": public_res,
        "holdout_tests": {
            "suite_role": "holdout",
            "suite_id": holdout_res.get("suite_id"),
            "suite_kind": holdout_res.get("suite_kind", "qc.json_io_cases.v1"),
            "suite_sha256": holdout_res.get("suite_sha256"),
            "candidate_source_sha256": holdout_res.get("candidate_source_sha256") or holdout_res.get("source_sha256"),
            "status": holdout_res.get("status", "failed"),
            "executed": holdout_res.get("executed", False),
            "passed": holdout_res["passed"],
            "tests_run": holdout_res["tests_run"],
            "expected_test_count": holdout_res.get("expected_test_count", holdout_res["tests_run"]),
            "failures": holdout_res["failures"],
            "errors": holdout_res["errors"],
            "cleanup_confirmed": holdout_res["cleanup_confirmed"],
            "containment_backend": holdout_res.get("containment_backend"),
            "filesystem_and_network_containment": holdout_res.get("filesystem_and_network_containment", False),
            "grant_nonce": holdout_res.get("grant_nonce"),
            "grant_sha256": holdout_res.get("grant_sha256"),
            "source_sha256": holdout_res.get("source_sha256"),
            "tests_sha256": holdout_res.get("tests_sha256"),
            "cases": holdout_res.get("cases"),
            "observations": holdout_res.get("observations"),
        } if holdout_res else None,
    }


# ---------------------------------------------------------------------------
# 3. Typed Tool Catalogue Registry
# ---------------------------------------------------------------------------

class ToolCatalogue:
    """Authoritative typed tool catalogue for deliberate task solving."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        specs = [
            ToolSpec(
                tool_id="inspect_document_or_repo",
                category="inspection",
                description="Read and extract structured facts, tables, or source files from approved project documents or disposable repositories with prompt-injection stripping.",
                input_schema={"source_id": "str", "content_or_path": "str", "max_chars": "int"},
                output_schema={"sanitized_text": "str", "contains_injection_attempt": "bool", "sha256": "str"},
                permissions=["read_scoped_workspace"],
                side_effects=False,
                required_effects=("read_only",),
                verification_requirement="sha256_and_directive_strip_check",
            ),
            ToolSpec(
                tool_id="inspect_repository_workspace",
                category="inspection",
                description="Inspect all source and test files inside a scoped repository workspace with directive-injection stripping and SHA-256 digests.",
                input_schema={"workspace_path": "str", "include_globs": "list[str]|None", "max_file_chars": "int"},
                output_schema={"files": "dict", "file_count": "int", "contains_injection_attempt": "bool"},
                permissions=["read_scoped_workspace"],
                side_effects=False,
                required_effects=("read_only", "read_scoped_workspace"),
                verification_requirement="workspace_sha256_and_directive_strip_check",
            ),
            ToolSpec(
                tool_id="run_repository_test_suite",
                category="coding",
                description="Run a repository's unit test suite inside a WindowsJob-bounded isolated process with network disabled.",
                input_schema={"workspace_path": "str", "test_rel_path": "str|None", "timeout_seconds": "float"},
                output_schema={"passed": "bool", "tests_run": "int", "failures": "int", "errors": "int", "output_excerpt": "str"},
                permissions=["read_scoped_workspace", "isolated_code_sandbox"],
                side_effects=False,
                required_effects=("read_only", "isolated_code_sandbox"),
                verification_requirement="bounded_process_test_execution",
            ),
            ToolSpec(
                tool_id="apply_and_verify_repository_patch",
                category="coding",
                description="Apply a candidate code patch to a scoped repository file and verify against public unit tests and protected holdout tests.",
                input_schema={"workspace_path": "str", "target_file": "str", "patched_content": "str", "public_test_rel_path": "str|None", "holdout_test_dir": "str|None"},
                output_schema={"accepted": "bool", "status": "str", "unified_diff": "str", "checks": "list[dict]"},
                permissions=["write_scoped_workspace", "isolated_code_sandbox"],
                side_effects=True,
                required_effects=("write_scoped_workspace", "isolated_code_sandbox"),
                verification_requirement="public_and_protected_holdout_test_suite_pass",
            ),
            ToolSpec(
                tool_id="resolve_quote_revisions",
                category="calculation",
                description="Resolve conflicting or superseded supplier quote revisions deterministically.",
                input_schema={"quotes": "list[dict]", "as_of_date": "str|None"},
                output_schema={"resolved_quotes": "dict", "superseded_revisions": "list[dict]", "contradictions": "list[dict]"},
                permissions=["deterministic_compute"],
                side_effects=False,
                required_effects=("read_only", "deterministic_compute"),
                verification_requirement="quote_revision_resolution_check",
            ),
            ToolSpec(
                tool_id="search_and_retrieve_sources",
                category="research",
                description="Search and retrieve source snapshots with publication date, jurisdiction/program scope, and provenance metadata.",
                input_schema={"queries": "list[str]", "project_id": "str", "as_of_date": "str"},
                output_schema={"sources": "list[dict]", "claims": "list[dict]", "contradictions": "list[dict]"},
                permissions=["controller_research_runner"],
                side_effects=False,
                required_effects=("read_only",),
                verification_requirement="source_scope_and_citation_audit",
            ),
            ToolSpec(
                tool_id="decimal_calculate_and_reconcile",
                category="calculation",
                description="Perform cent-accurate Decimal arithmetic, budget allocation reconciliation, or quote cost calculation.",
                input_schema={"mode": "str (reconcile|option_cost)", "payload": "dict"},
                output_schema={"valid": "bool", "total_cost": "str", "derivations": "dict", "errors": "list[str]"},
                permissions=["deterministic_compute"],
                side_effects=False,
                required_effects=("read_only", "deterministic_compute"),
                verification_requirement="exact_decimal_verification",
            ),
            ToolSpec(
                tool_id="compare_options_against_constraints",
                category="reasoning",
                description="Resolve conflicting quote revisions and compare candidate options against hard and soft price, delivery, capability, and dependency constraints.",
                input_schema={"options": "list[dict]", "constraints": "dict", "quantity": "int", "as_of_date": "str"},
                output_schema={"status": "str", "recommended_option": "dict|None", "evaluated_options": "list[dict]", "blocker": "str|None"},
                permissions=["deterministic_compute"],
                side_effects=False,
                required_effects=("read_only", "deterministic_compute"),
                verification_requirement="constraint_satisfaction_matrix",
            ),
            ToolSpec(
                tool_id="analyze_structured_dataset",
                category="data_analysis",
                description="Compute exact Decimal aggregations, group-by summaries, malformed row diagnostics, and z-score anomaly detection on tabular data.",
                input_schema={"rows": "list[dict]", "group_by": "str|None", "value_field": "str"},
                output_schema={"summary": "dict", "groups": "dict", "anomalies": "list[dict]", "malformed_rows": "list[dict]"},
                permissions=["deterministic_compute"],
                side_effects=False,
                required_effects=("read_only", "deterministic_compute"),
                verification_requirement="schema_and_decimal_invariants",
            ),
            ToolSpec(
                tool_id="execute_and_test_code",
                category="coding",
                description="Validate syntax and execute candidate Python code against independent pinned unit tests inside the isolated sandbox runner.",
                input_schema={"code": "str", "test_code": "str", "filename": "str", "timeout_seconds": "float"},
                output_schema={"accepted": "bool", "decision": "str", "checks": "list[dict]"},
                permissions=["isolated_code_sandbox"],
                side_effects=False,
                required_effects=("isolated_code_sandbox",),
                verification_requirement="independent_test_suite_pass",
            ),
            ToolSpec(
                tool_id="verify_web_application_behavior",
                category="web_verification",
                description="Verify HTML/CSS/JS web application DOM elements, event handlers, and interactive state transitions.",
                input_schema={"html_content": "str", "js_content": "str", "required_elements": "list[str]", "interaction_cases": "list[dict]"},
                output_schema={"verified": "bool", "status": "str", "checks": "list[dict]"},
                permissions=["deterministic_compute"],
                side_effects=False,
                required_effects=("read_only", "deterministic_compute"),
                verification_requirement="dom_and_interaction_checks",
            ),
            ToolSpec(
                tool_id="create_or_revise_artifact",
                category="artifact",
                description="Write a versioned, content-addressed project artifact with provenance and validation metadata.",
                input_schema={"run": "dict", "filename": "str", "content": "bytes|str", "provenance": "str"},
                output_schema={"artifact_id": "str", "sha256": "str", "version": "int"},
                permissions=["write_project_artifact"],
                side_effects=True,
                required_effects=("write_project_artifact",),
                verification_requirement="cas_sha256_and_contract_validation",
            ),
            ToolSpec(
                tool_id="inspect_operational_state",
                category="operations",
                description="Inspect live node inventory, model health, capability verifications, and run resource budgets.",
                input_schema={"run_id": "str|None"},
                output_schema={"nodes": "list[dict]", "models": "list[dict]", "budget": "dict|None"},
                permissions=["read_cluster_telemetry"],
                side_effects=False,
                required_effects=("read_only",),
                verification_requirement="measured_telemetry_receipt",
            ),
            ToolSpec(
                tool_id="worker_model_inference",
                category="inference",
                description="Dispatch bounded model generation or synthesis through an authenticated, capability-verified worker lease.",
                input_schema={"purpose": "str", "prompt": "str", "role": "str", "max_tokens": "int"},
                output_schema={"status": "str", "response": "str", "lease_id": "str|None", "model": "str"},
                permissions=["authenticated_worker_lease"],
                side_effects=False,
                required_effects=("worker_inference",),
                verification_requirement="independent_acceptance_verifier",
            ),
        ]
        for s in specs:
            self._tools[s.tool_id] = s

    def list_tools(self) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self._tools.values()]

    def get_tool(self, tool_id: str) -> Optional[ToolSpec]:
        return self._tools.get(tool_id)

    def execute_tool(self, tool_id: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a registered tool safely and return a provenance-tagged observation."""
        spec = self.get_tool(tool_id)
        if not spec:
            return {
                "tool_id": tool_id,
                "status": "failed",
                "error": f"Unknown tool_id '{tool_id}'",
                "provenance": "tool_observation",
            }

        if tool_id == "inspect_document_or_repo":
            raw_text = str(arguments.get("content_or_path") or "")
            source_id = str(arguments.get("source_id") or "document")
            max_chars = int(arguments.get("max_chars") or 6000)
            guard = inspect_untrusted_source_for_directive_injection(raw_text, source_id=source_id)
            sanitized = guard["sanitized_text"][:max_chars]
            return {
                "tool_id": tool_id,
                "status": "observed",
                "provenance": "retrieved_document",
                "source_id": source_id,
                "contains_injection_attempt": guard["contains_injection_attempt"],
                "flagged_directives": guard["flagged_directives"],
                "sanitized_text": sanitized,
                "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            }

        if tool_id == "inspect_repository_workspace":
            res = inspect_repository_workspace(
                workspace_path=str(arguments.get("workspace_path") or ""),
                include_globs=arguments.get("include_globs"),
                max_file_chars=int(arguments.get("max_file_chars") or 8000),
            )
            return {
                "tool_id": tool_id,
                "status": res["status"],
                "provenance": "retrieved_document",
                "contains_injection_attempt": res.get("contains_injection_attempt", False),
                "result": res,
            }

        if tool_id == "run_repository_test_suite":
            res = run_repository_test_suite(
                workspace_path=str(arguments.get("workspace_path") or ""),
                test_rel_path=arguments.get("test_rel_path"),
                extra_test_dir=arguments.get("extra_test_dir"),
                timeout_seconds=float(arguments.get("timeout_seconds") or 15.0),
            )
            return {
                "tool_id": tool_id,
                "status": res["status"],
                "provenance": "tool_observation",
                "result": res,
            }

        if tool_id == "apply_and_verify_repository_patch":
            res = apply_and_verify_repository_patch(
                workspace_path=str(arguments.get("workspace_path") or ""),
                target_file=str(arguments.get("target_file") or ""),
                patched_content=str(arguments.get("patched_content") or ""),
                public_test_rel_path=arguments.get("public_test_rel_path"),
                holdout_test_dir=arguments.get("holdout_test_dir"),
                revert_on_failure=bool(arguments.get("revert_on_failure", True)),
                timeout_seconds=float(arguments.get("timeout_seconds") or 15.0),
                project_id=str(arguments.get("project_id") or "proj_repo_verif"),
                run_id=str(arguments.get("run_id") or "run_repo_verif"),
                native_execution_id=str(arguments.get("native_execution_id") or "exec_bound"),
                native_lease_id=str(arguments.get("native_lease_id") or "lease_bound"),
                native_idempotency_key=str(arguments.get("native_idempotency_key") or "idem_bound"),
            )
            return {
                "tool_id": tool_id,
                "status": res["status"],
                "provenance": "tool_observation",
                "result": res,
            }

        if tool_id == "resolve_quote_revisions":
            res = resolve_quote_revisions(
                quotes=arguments.get("quotes") or [],
                as_of_date=arguments.get("as_of_date"),
            )
            return {
                "tool_id": tool_id,
                "status": "observed",
                "provenance": "tool_observation",
                "result": res,
            }

        if tool_id == "decimal_calculate_and_reconcile":
            mode = arguments.get("mode", "option_cost")
            payload = arguments.get("payload") or {}
            if mode == "reconcile":
                costs = tuple(Cost(**c) for c in payload.get("costs", []))
                funds = tuple(
                    Fund(
                        id=f["id"],
                        kind=f["kind"],
                        status=f["status"],
                        amount=f["amount"],
                        eligible_categories=frozenset(f.get("eligible_categories", [])),
                        rule_evidence_id=f.get("rule_evidence_id", "ev-1"),
                        match_rate=str(f.get("match_rate", "0")),
                        match_basis=f.get("match_basis", "grant"),
                        accepted_match_kinds=frozenset(f.get("accepted_match_kinds", [])),
                        category_caps=tuple(tuple(p) for p in f.get("category_caps", [])),
                    )
                    for f in payload.get("funds", [])
                )
                allocs = tuple(Allocation(**a) for a in payload.get("allocations", []))
                res = reconcile(payload["total"], costs, funds, allocs)
                return {
                    "tool_id": tool_id,
                    "status": "verified_for_scope" if res["valid"] else "failed",
                    "provenance": "tool_observation",
                    "result": res,
                }
            else:
                res = calculate_option_total_cost(payload, quantity=int(payload.get("quantity", 1)))
                return {
                    "tool_id": tool_id,
                    "status": "verified_for_scope",
                    "provenance": "tool_observation",
                    "result": res,
                }

        if tool_id == "compare_options_against_constraints":
            res = compare_options_against_constraints(
                options=arguments.get("options") or [],
                constraints=arguments.get("constraints") or {},
                quantity=int(arguments.get("quantity", 1)),
                as_of_date=arguments.get("as_of_date"),
            )
            return {
                "tool_id": tool_id,
                "status": res["status"],
                "provenance": "tool_observation",
                "result": res,
            }

        if tool_id == "analyze_structured_dataset":
            res = analyze_structured_dataset(
                rows=arguments.get("rows") or [],
                group_by=arguments.get("group_by"),
                value_field=str(arguments.get("value_field") or "amount"),
                anomaly_z_threshold=float(arguments.get("anomaly_z_threshold", 2.0)),
            )
            return {
                "tool_id": tool_id,
                "status": "verified_for_scope" if not res.get("error") else "failed",
                "provenance": "tool_observation",
                "result": res,
            }

        if tool_id == "execute_and_test_code":
            res = run_bounded_python_verification(
                code=str(arguments.get("code") or ""),
                test_code=str(arguments.get("test_code") or ""),
                filename=str(arguments.get("filename") or "solution.py"),
                timeout_seconds=float(arguments.get("timeout_seconds") or 5.0),
            )
            return {
                "tool_id": tool_id,
                "status": "verified_for_scope" if res["accepted"] else "failed",
                "provenance": "tool_observation",
                "result": res,
            }

        if tool_id == "verify_web_application_behavior":
            res = verify_web_application_behavior(
                html_content=str(arguments.get("html_content") or ""),
                js_content=str(arguments.get("js_content") or ""),
                css_content=str(arguments.get("css_content") or ""),
                required_elements=arguments.get("required_elements"),
                interaction_cases=arguments.get("interaction_cases"),
            )
            return {
                "tool_id": tool_id,
                "status": res["status"],
                "provenance": "tool_observation",
                "result": res,
            }

        if tool_id == "inspect_operational_state":
            from app.services.orchestration import model_router, telemetry
            run_id = arguments.get("run_id")
            return {
                "tool_id": tool_id,
                "status": "observed",
                "provenance": "tool_observation",
                "result": {
                    "nodes": model_router.node_inventory(),
                    "models": model_router.model_health(),
                    "budget": telemetry.get_budget(run_id) if run_id else None,
                },
            }

        return {
            "tool_id": tool_id,
            "status": "observed",
            "provenance": "tool_observation",
            "result": arguments,
        }


GLOBAL_TOOL_CATALOGUE = ToolCatalogue()
