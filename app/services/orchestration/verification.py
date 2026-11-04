"""Deterministic Verification & Arithmetic Engine (Phase C/D Milestone 1).

Eliminates LLM arithmetic, ratio, and calendar hallucinations by providing
deterministic, sandboxed Python calculations and temporal verifications.
Uses Python's AST for safe mathematical expression evaluation (no eval()).
"""
from __future__ import annotations

import ast
import logging
import math
import operator
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("qc.orchestrator.verification")

# Supported safe operators for AST arithmetic evaluation
_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


class SafeMathEvaluator(ast.NodeVisitor):
    """Safely evaluates mathematical expressions without arbitrary code execution."""

    def __init__(self, variables: Optional[Dict[str, float]] = None):
        self.variables = variables or {}

    def visit_BinOp(self, node: ast.BinOp) -> float:
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"Unsupported operator in arithmetic assertion: {op_type.__name__}")
        left = self.visit(node.left)
        right = self.visit(node.right)
        return _SAFE_OPERATORS[op_type](left, right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> float:
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        operand = self.visit(node.operand)
        return _SAFE_OPERATORS[op_type](operand)

    def visit_Constant(self, node: ast.Constant) -> float:
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Unsupported constant type in math expression: {type(node.value)}")

    def visit_Num(self, node: ast.Num) -> float:  # Python <=3.7 compat
        return float(node.n)

    def visit_Name(self, node: ast.Name) -> float:
        if node.id in self.variables:
            return float(self.variables[node.id])
        raise ValueError(f"Undefined variable in arithmetic expression: '{node.id}'")

    def generic_visit(self, node: ast.AST):
        raise ValueError(f"Unsupported expression node in math evaluator: {type(node).__name__}")


def safe_eval_math(expression: str, variables: Optional[Dict[str, float]] = None) -> float:
    """Evaluate a mathematical expression string using safe AST traversal."""
    cleaned = expression.strip().replace("$", "").replace(",", "")
    tree = ast.parse(cleaned, mode="eval")
    evaluator = SafeMathEvaluator(variables)
    return evaluator.visit(tree.body)


class DeterministicVerificationEngine:
    """Core verification engine for factual, numerical, and date constraints."""

    # -------------------------------------------------------------------------
    # 1. Financial & Grant Arithmetic Verifications
    # -------------------------------------------------------------------------

    @staticmethod
    def verify_grant_match(
        grant_requested: float,
        match_rate: float,
        declared_match: float,
        tolerance_pct: float = 0.05,
    ) -> Dict[str, Any]:
        """Verify matching fund calculation.

        Example:
            Grant requested = $60,000, match_rate = 0.50 ($0.50 per grant dollar).
            Expected match = $30,000.
            Total matching grant project cost = $90,000.
            Match ratio of total project cost = 30,000 / 90,000 = 33.33%.
        """
        expected_match = grant_requested * match_rate
        total_matching_cost = grant_requested + expected_match
        match_ratio_of_total = (expected_match / total_matching_cost) if total_matching_cost > 0 else 0.0

        diff = abs(declared_match - expected_match)
        rel_diff = (diff / expected_match) if expected_match > 0 else 0.0
        passed = rel_diff <= (tolerance_pct / 100.0)

        return {
            "passed": passed,
            "grant_requested": grant_requested,
            "match_rate": match_rate,
            "declared_match": declared_match,
            "expected_match": round(expected_match, 2),
            "total_matching_project_cost": round(total_matching_cost, 2),
            "match_pct_of_total": round(match_ratio_of_total * 100.0, 2),
            "discrepancy": round(diff, 2),
            "detail": (
                f"Match verified: ${declared_match:,.2f} matches expected ${expected_match:,.2f} "
                f"at {match_rate*100:.1f}% rate (1:{1/match_rate:.1f} ratio, {match_ratio_of_total*100:.2f}% of project)"
                if passed
                else f"Match calculation error: declared ${declared_match:,.2f}, expected ${expected_match:,.2f} (diff: ${diff:,.2f})"
            ),
        }

    @staticmethod
    def verify_cost_cap(
        base_amount: float,
        cost_amount: float,
        cap_pct: float,
    ) -> Dict[str, Any]:
        """Verify whether an expense category respects a statutory/policy cap percentage.

        Example: Admin cannot exceed 10% of grant request.
        """
        max_allowable = base_amount * (cap_pct / 100.0)
        passed = cost_amount <= (max_allowable + 1e-4)
        actual_pct = (cost_amount / base_amount * 100.0) if base_amount > 0 else 0.0

        return {
            "passed": passed,
            "base_amount": base_amount,
            "cost_amount": cost_amount,
            "cap_pct": cap_pct,
            "max_allowable": round(max_allowable, 2),
            "actual_pct": round(actual_pct, 2),
            "detail": (
                f"Cost ${cost_amount:,.2f} ({actual_pct:.2f}%) within allowable cap of {cap_pct:.1f}% (${max_allowable:,.2f})"
                if passed
                else f"Cost ${cost_amount:,.2f} ({actual_pct:.2f}%) EXCEEDS allowable cap of {cap_pct:.1f}% (max ${max_allowable:,.2f})"
            ),
        }

    @staticmethod
    def compute_multi_grant_allocation(
        total_project_cost: float,
        line_items: List[Dict[str, Any]],
        grants: List[Dict[str, Any]],
        local_cash: float = 0.0,
        local_inkind: float = 0.0,
    ) -> Dict[str, Any]:
        """Compute exact non-duplicative funding allocation across multiple grant programs.

        Enforces allowable category rules, admin caps, and design caps deterministically.
        """
        allocated_grants: List[Dict[str, Any]] = []
        total_grant_funding = 0.0
        total_match_required = 0.0

        for g in grants:
            gid = g.get("id", "grant")
            name = g.get("name", gid)
            max_award = float(g.get("max_award", float("inf")))
            match_rate = float(g.get("match_rate", 0.0))
            max_admin_pct = float(g.get("max_admin_pct", 100.0))
            max_design_pct = float(g.get("max_design_pct", 100.0))

            eligible_pool = 0.0
            for item in line_items:
                cat = item.get("category", "").lower()
                cost = float(item.get("cost", 0.0))
                # Check eligibility flag
                if item.get(f"eligible_{gid}", item.get("eligible", True)):
                    if "admin" in cat or "outreach" in cat:
                        # Cap admin dynamically
                        allowable_admin = min(cost, max_award * (max_admin_pct / 100.0))
                        eligible_pool += allowable_admin
                    elif "design" in cat or "permitting" in cat:
                        allowable_design = min(cost, max_award * (max_design_pct / 100.0))
                        eligible_pool += allowable_design
                    else:
                        eligible_pool += cost

            awarded = min(eligible_pool, max_award)
            match_req = awarded * match_rate
            allocated_grants.append({
                "id": gid,
                "name": name,
                "awarded": round(awarded, 2),
                "match_required": round(match_req, 2),
                "eligible_pool": round(eligible_pool, 2),
            })
            total_grant_funding += awarded
            total_match_required += match_req

        total_local_available = local_cash + local_inkind
        net_gap = total_project_cost - (total_grant_funding + total_local_available)
        match_deficit = max(0.0, total_match_required - total_local_available)

        return {
            "total_project_cost": total_project_cost,
            "allocated_grants": allocated_grants,
            "total_grant_funding": round(total_grant_funding, 2),
            "total_match_required": round(total_match_required, 2),
            "local_cash": local_cash,
            "local_inkind": local_inkind,
            "total_local_available": total_local_available,
            "match_deficit": round(match_deficit, 2),
            "net_unfunded_gap": round(net_gap, 2),
            "fully_funded": net_gap <= 0.0 and match_deficit == 0.0,
        }

    # -------------------------------------------------------------------------
    # 2. Date & Temporal Consistency Verifications
    # -------------------------------------------------------------------------

    @staticmethod
    def parse_date_flexible(date_str: str) -> Optional[date]:
        """Parse date strings across common formats (YYYY-MM-DD, Month DD, YYYY)."""
        date_str = date_str.strip()
        # Clean ordinals like 1st, 2nd, 3rd, 4th
        cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", date_str)
        formats = [
            "%Y-%m-%d",
            "%B %d, %Y",
            "%b %d, %Y",
            "%m/%d/%Y",
            "%d %B %Y",
            "%B %Y",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(cleaned, fmt)
                return dt.date()
            except ValueError:
                continue
        return None

    @classmethod
    def verify_deadline(
        cls,
        deadline_str: str,
        reference_date: Optional[Union[str, date]] = None,
        must_be_future: bool = True,
        unannounced_flag: bool = False,
    ) -> Dict[str, Any]:
        """Verify whether a stated deadline is valid, in the future, or properly caveated."""
        if isinstance(reference_date, str):
            ref_date = cls.parse_date_flexible(reference_date) or date.today()
        elif isinstance(reference_date, date):
            ref_date = reference_date
        else:
            ref_date = date.today()

        d = cls.parse_date_flexible(deadline_str)
        if not d:
            return {
                "passed": False,
                "error": "UNPARSEABLE_DATE",
                "detail": f"Could not parse deadline string '{deadline_str}'",
            }

        days_remaining = (d - ref_date).days
        is_future = days_remaining > 0

        if must_be_future and not is_future:
            return {
                "passed": False,
                "error": "EXPIRED_DEADLINE",
                "days_remaining": days_remaining,
                "parsed_date": str(d),
                "reference_date": str(ref_date),
                "detail": f"Deadline '{d}' is expired ({abs(days_remaining)} days in the past relative to {ref_date})",
            }

        return {
            "passed": True,
            "parsed_date": str(d),
            "reference_date": str(ref_date),
            "days_remaining": days_remaining,
            "is_future": is_future,
            "detail": f"Deadline '{d}' verified ({days_remaining} days remaining from {ref_date})",
        }

    # -------------------------------------------------------------------------
    # 3. Document-Level Contract Constraint Verifiers
    # -------------------------------------------------------------------------

    @classmethod
    def verify_negative_constraints(
        cls,
        text: str,
        disallowed_claims: List[Union[str, Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Verify that document contains zero prohibited phrases, conflations, or assertions."""
        violations = []
        for claim in disallowed_claims:
            pattern = ""
            desc = ""
            if isinstance(claim, str):
                pattern = claim
                desc = f"prohibited phrase: '{claim}'"
            elif isinstance(claim, dict):
                pattern = claim.get("pattern", "")
                desc = claim.get("description", pattern)

            if pattern:
                matches = list(re.finditer(pattern, text, re.IGNORECASE))
                if matches:
                    violations.append({
                        "pattern": pattern,
                        "description": desc,
                        "match_count": len(matches),
                        "snippet": text[max(0, matches[0].start() - 40): min(len(text), matches[0].end() + 40)],
                    })
        return violations

    @classmethod
    def verify_required_numerical_assertions(
        cls,
        text: str,
        numerical_assertions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Verify that specific numerical figures appear in the text within required formulas."""
        failures = []
        for assertion in numerical_assertions:
            name = assertion.get("name", "unnamed_assertion")
            expected_val = float(assertion.get("expected_value", 0.0))
            tolerance_pct = float(assertion.get("tolerance_pct", 0.5))
            pattern = assertion.get("regex_pattern")

            found_val: Optional[float] = None
            if pattern:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    try:
                        raw = m.group(1).replace(",", "").replace("$", "").replace("%", "")
                        found_val = float(raw)
                    except (ValueError, IndexError):
                        pass

            if found_val is None:
                failures.append({
                    "name": name,
                    "error": "VALUE_NOT_FOUND",
                    "expected_value": expected_val,
                    "detail": f"Could not find matching numerical value in text for assertion '{name}'",
                })
            else:
                diff = abs(found_val - expected_val)
                rel_diff = (diff / expected_val) if expected_val != 0 else diff
                if rel_diff > (tolerance_pct / 100.0):
                    failures.append({
                        "name": name,
                        "error": "ARITHMETIC_MISMATCH",
                        "found_value": found_val,
                        "expected_value": expected_val,
                        "discrepancy": round(diff, 2),
                        "detail": f"Assertion '{name}' found {found_val}, expected {expected_val} (diff: {diff:.2f})",
                    })
        return failures
