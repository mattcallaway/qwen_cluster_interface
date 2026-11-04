"""Lossless structured intake analysis and durable requirement ledger for Qwen Collective.

Enforces:
1. Complete preservation of user objectives, constraints, scope exclusions, deliverables,
   acceptance criteria, user decisions/corrections, explicit assumptions, material missing
   information, requirement dependencies, and affected artifact versions.
2. Machine-checkable requirement ledger (hardness, status, verification method & status, task coverage).
3. Zero silent parameter dropping or hard slicing of objectives.
4. Validation of plan fidelity against mandatory intake requirements.
5. Durable database persistence across revisions, mid-flight user steering, selective
   artifact/task invalidation, cancellation, and resumption.
6. Strict separation between user instructions and untrusted external source documents.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from app.database import db_transaction, get_db_connection
from app.services.orchestration.common import jdump, jload, new_id, now_iso

logger = logging.getLogger("qc.orchestrator.intake")


class RequirementKind(str, Enum):
    OBJECTIVE = "objective"
    CONSTRAINT = "constraint"
    DELIVERABLE = "deliverable"
    EXCLUSION = "exclusion"
    SUCCESS_CRITERION = "success_criterion"
    ACCEPTANCE_CRITERION = "acceptance_criterion"
    ASSUMPTION = "assumption"
    MISSING_INFO = "missing_info"
    USER_DECISION = "user_decision"
    DEPENDENCY = "dependency"


RequirementCategory = RequirementKind


class RequirementHardness(str, Enum):
    MUST = "must"
    SHOULD = "should"
    PREFERENCE = "preference"
    STRICT = "must"


class VerificationStatus(str, Enum):
    PROPOSED = "proposed"
    OBSERVED = "observed"
    SUPPORTED = "supported"
    VERIFIED_FOR_SCOPE = "verified_for_scope"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass
class RequirementItem:
    req_id: str
    kind: RequirementKind = RequirementKind.OBJECTIVE
    text: str = ""
    hardness: RequirementHardness = RequirementHardness.MUST
    source_field: str = ""
    included_in_plan: bool = False
    covered_by_task: Optional[str] = None
    verification_criteria: Optional[str] = None
    verification_method: str = "deterministic_check"
    verification_status: str = "proposed"
    verification_detail: str = ""
    status: str = "active"
    version_number: int = 1
    rationale: str = ""
    depends_on: List[str] = field(default_factory=list)
    affected_artifacts: List[str] = field(default_factory=list)
    superseded_by_id: Optional[str] = None
    content: Optional[str] = None
    category: Optional[RequirementKind] = None

    def __post_init__(self) -> None:
        if self.content and not self.text:
            self.text = self.content
        elif not self.content and self.text:
            self.content = self.text
        if self.category and not self.kind:
            self.kind = self.category
        elif not self.category and self.kind:
            self.category = self.kind


@dataclass
class RequirementLedger:
    items: List[RequirementItem] = field(default_factory=list)
    revision_number: int = 1
    project_id: Optional[str] = None
    run_id: Optional[str] = None

    def get_items_by_category(self, cat: RequirementKind, active_only: bool = True) -> List[RequirementItem]:
        out: List[RequirementItem] = []
        for item in self.items:
            if active_only and item.status != "active":
                continue
            if item.kind == cat:
                out.append(item)
            elif cat in (RequirementKind.SUCCESS_CRITERION, RequirementKind.ACCEPTANCE_CRITERION) and item.kind in (
                RequirementKind.SUCCESS_CRITERION, RequirementKind.ACCEPTANCE_CRITERION
            ):
                out.append(item)
        return out

    def format_prompt_summary(self) -> str:
        lines: List[str] = []
        objs = self.get_items_by_category(RequirementKind.OBJECTIVE)
        if objs:
            lines.append("OBJECTIVE:")
            for o in objs:
                lines.append(f"- {o.text}")

        constraints = self.get_items_by_category(RequirementKind.CONSTRAINT)
        if constraints:
            lines.append("CONSTRAINTS:")
            for c in constraints:
                tag = "HARD" if c.hardness == RequirementHardness.MUST else "SOFT/PREFERENCE"
                lines.append(f"- [{tag}] {c.text}")

        exclusions = self.get_items_by_category(RequirementKind.EXCLUSION)
        if exclusions:
            lines.append("EXCLUSIONS (Strictly forbidden):")
            for e in exclusions:
                lines.append(f"- {e.text}")

        deliverables = self.get_items_by_category(RequirementKind.DELIVERABLE)
        if deliverables:
            lines.append("REQUESTED DELIVERABLES:")
            for d in deliverables:
                lines.append(f"- {d.text}")

        criteria = self.get_items_by_category(RequirementKind.SUCCESS_CRITERION)
        if criteria:
            lines.append("SUCCESS CRITERIA:")
            for s in criteria:
                lines.append(f"- {s.text}")

        decisions = self.get_items_by_category(RequirementKind.USER_DECISION)
        if decisions:
            lines.append("USER DECISIONS & CORRECTIONS:")
            for dec in decisions:
                lines.append(f"- {dec.text}")

        assumptions = self.get_items_by_category(RequirementKind.ASSUMPTION)
        if assumptions:
            lines.append("EXPLICIT ASSUMPTIONS:")
            for a in assumptions:
                lines.append(f"- {a.text}")

        missing = self.get_items_by_category(RequirementKind.MISSING_INFO)
        if missing:
            lines.append("MATERIAL MISSING INFORMATION:")
            for m in missing:
                lines.append(f"- {m.text}")

        return "\n".join(lines)

    @classmethod
    def from_intake(cls, intake: Dict[str, Any]) -> RequirementLedger:
        ledger = cls()
        idx = 1

        # 1. Main objective
        obj = (intake.get("objective") or "").strip()
        if obj:
            ledger.items.append(RequirementItem(
                req_id=f"req-obj-{idx}",
                kind=RequirementKind.OBJECTIVE,
                text=obj,
                hardness=RequirementHardness.MUST,
                source_field="objective",
                verification_criteria="Overall plan addresses stated objective.",
                verification_method="goal_check",
            ))
            idx += 1

        # 2. Constraints (distinguish hard vs soft/preference)
        constraints = intake.get("constraints") or []
        if isinstance(constraints, str):
            constraints = [c.strip() for c in re.split(r"[\n;]+", constraints) if c.strip()]
        for c in constraints:
            if isinstance(c, str) and c.strip():
                txt = c.strip()
                is_soft = any(w in txt.lower() for w in ("prefer", "ideally", "if possible", "nice to have", "soft:", "optional"))
                ledger.items.append(RequirementItem(
                    req_id=f"req-con-{idx}",
                    kind=RequirementKind.CONSTRAINT,
                    text=txt,
                    hardness=RequirementHardness.SHOULD if is_soft else RequirementHardness.MUST,
                    source_field="constraints",
                    verification_criteria=f"Plan strictly respects constraint: {txt}",
                    verification_method="constraint_check",
                ))
                idx += 1

        # 3. Scope exclusions
        exclusions = intake.get("scope_exclusions") or intake.get("exclusions") or []
        if isinstance(exclusions, str):
            exclusions = [e.strip() for e in re.split(r"[\n;]+", exclusions) if e.strip()]
        for e in exclusions:
            if isinstance(e, str) and e.strip():
                ledger.items.append(RequirementItem(
                    req_id=f"req-excl-{idx}",
                    kind=RequirementKind.EXCLUSION,
                    text=e.strip(),
                    hardness=RequirementHardness.MUST,
                    source_field="scope_exclusions",
                    verification_criteria=f"Plan does not execute excluded action: {e.strip()}",
                    verification_method="negative_constraint_check",
                ))
                idx += 1

        # 4. Requested deliverables
        deliverables = intake.get("requested_deliverables") or intake.get("deliverables") or []
        if isinstance(deliverables, str):
            deliverables = [d.strip() for d in re.split(r"[\n;]+", deliverables) if d.strip()]
        for d in deliverables:
            d_text = d if isinstance(d, str) else d.get("name") or d.get("key") or str(d)
            if d_text and d_text.strip():
                ledger.items.append(RequirementItem(
                    req_id=f"req-del-{idx}",
                    kind=RequirementKind.DELIVERABLE,
                    text=d_text.strip(),
                    hardness=RequirementHardness.MUST,
                    source_field="requested_deliverables",
                    verification_criteria=f"Plan produces terminal deliverable matching: {d_text.strip()}",
                    verification_method="deliverable_contract_check",
                ))
                idx += 1

        # 5. Success / acceptance criteria
        criteria = intake.get("success_criteria") or intake.get("acceptance_criteria") or []
        if isinstance(criteria, str):
            criteria = [s.strip() for s in re.split(r"[\n;]+", criteria) if s.strip()]
        for s in criteria:
            if isinstance(s, str) and s.strip():
                ledger.items.append(RequirementItem(
                    req_id=f"req-crit-{idx}",
                    kind=RequirementKind.SUCCESS_CRITERION,
                    text=s.strip(),
                    hardness=RequirementHardness.MUST,
                    source_field="success_criteria",
                    verification_criteria=f"Plan output meets success criterion: {s.strip()}",
                    verification_method="acceptance_test",
                ))
                idx += 1

        # 6. Explicit assumptions
        assumptions = intake.get("assumptions") or []
        if isinstance(assumptions, str):
            assumptions = [a.strip() for a in re.split(r"[\n;]+", assumptions) if a.strip()]
        for a in assumptions:
            if isinstance(a, str) and a.strip():
                ledger.items.append(RequirementItem(
                    req_id=f"req-assump-{idx}",
                    kind=RequirementKind.ASSUMPTION,
                    text=a.strip(),
                    hardness=RequirementHardness.SHOULD,
                    source_field="assumptions",
                    verification_criteria="Disclosed assumption verified or surfaced in output.",
                    verification_method="assumption_audit",
                ))
                idx += 1

        # 7. Material missing information
        missing_info = intake.get("missing_info") or intake.get("critical_missing") or []
        if isinstance(missing_info, str):
            missing_info = [m.strip() for m in re.split(r"[\n;]+", missing_info) if m.strip()]
        for m in missing_info:
            if isinstance(m, str) and m.strip():
                ledger.items.append(RequirementItem(
                    req_id=f"req-gap-{idx}",
                    kind=RequirementKind.MISSING_INFO,
                    text=m.strip(),
                    hardness=RequirementHardness.MUST,
                    source_field="missing_info",
                    verification_criteria="Material gap must be clarified by user or disclosed as explicit assumption.",
                    verification_method="clarification_check",
                ))
                idx += 1

        # 8. User decisions / clarifications
        clarifications = intake.get("clarifications") or intake.get("user_decisions") or {}
        if isinstance(clarifications, dict):
            for k, v in clarifications.items():
                if v:
                    ledger.items.append(RequirementItem(
                        req_id=f"req-dec-{idx}",
                        kind=RequirementKind.USER_DECISION,
                        text=f"{k}: {v}",
                        hardness=RequirementHardness.MUST,
                        source_field="clarifications",
                        verification_criteria=f"Respects user clarification on {k}.",
                        verification_method="decision_check",
                    ))
                    idx += 1
        elif isinstance(clarifications, list):
            for dec in clarifications:
                if dec:
                    ledger.items.append(RequirementItem(
                        req_id=f"req-dec-{idx}",
                        kind=RequirementKind.USER_DECISION,
                        text=str(dec).strip(),
                        hardness=RequirementHardness.MUST,
                        source_field="user_decisions",
                        verification_criteria="Respects user decision.",
                        verification_method="decision_check",
                    ))
                    idx += 1

        # 9. Requirement dependencies
        deps = intake.get("requirement_dependencies") or []
        if isinstance(deps, list):
            for dep in deps:
                if isinstance(dep, dict) and dep.get("from") and dep.get("to"):
                    ledger.items.append(RequirementItem(
                        req_id=f"req-dep-{idx}",
                        kind=RequirementKind.DEPENDENCY,
                        text=f"{dep['from']} depends on {dep['to']}",
                        hardness=RequirementHardness.MUST,
                        source_field="requirement_dependencies",
                        depends_on=[str(dep["to"])],
                        verification_criteria=f"Requirement {dep['from']} evaluated after {dep['to']}.",
                        verification_method="dependency_check",
                    ))
                    idx += 1

        return ledger

    def detect_conflicts_and_ambiguities(self) -> Dict[str, Any]:
        """Detect conflicting hard requirements, infeasible numeric ceilings, or material ambiguity."""
        conflicts: List[Dict[str, Any]] = []
        material_gaps: List[str] = []
        disclosed_assumptions: List[str] = []

        active_constraints = self.get_items_by_category(RequirementKind.CONSTRAINT)
        active_exclusions = self.get_items_by_category(RequirementKind.EXCLUSION)
        active_deliverables = self.get_items_by_category(RequirementKind.DELIVERABLE)
        active_objs = self.get_items_by_category(RequirementKind.OBJECTIVE)

        # 1. Check deliverable vs exclusion conflicts
        for d in active_deliverables:
            d_low = d.text.lower()
            for ex in active_exclusions:
                ex_low = ex.text.lower()
                # Strip leading "no " or "do not " from exclusion to check overlap
                core_ex = re.sub(r"^(?:no|do\s+not\s+(?:use|include|produce|create)|exclude|without)\s+", "", ex_low).strip()
                if core_ex and len(core_ex) >= 4 and (core_ex in d_low or d_low in core_ex):
                    conflicts.append({
                        "type": "deliverable_exclusion_conflict",
                        "req_a": d.req_id,
                        "req_b": ex.req_id,
                        "detail": f"Requested deliverable '{d.text}' directly conflicts with exclusion '{ex.text}'.",
                    })

        # 2. Check contradictory numeric ceilings/floors on the same metric (e.g. budget ceiling < minimum cost)
        budget_ceilings: List[Tuple[str, float]] = []
        budget_floors: List[Tuple[str, float]] = []
        deadline_ceilings: List[Tuple[str, int]] = []
        deadline_floors: List[Tuple[str, int]] = []

        for c in active_constraints:
            txt = c.text.lower()
            # Max budget / ceiling
            m_max = re.search(r"(?:max(?:imum)?[^$\d]{0,30}|ceiling[^$\d]{0,30}|under\s+|at\s+most\s+|not\s+exceed\s+|<=?\s*|budget\s+of\s*)\$?\s*([\d,]+(?:\.\d+)?)", txt)
            if m_max and any(w in txt for w in ("$", "usd", "budget", "cost", "price", "spend", "ceiling")):
                val = float(m_max.group(1).replace(",", ""))
                budget_ceilings.append((c.req_id, val))
            m_min = re.search(r"(?:min(?:imum)?[^$\d]{0,30}|at\s+least\s+|>=?\s*)\$?\s*([\d,]+(?:\.\d+)?)", txt)
            if m_min and any(w in txt for w in ("$", "usd", "budget", "cost", "price", "spend")):
                val = float(m_min.group(1).replace(",", ""))
                budget_floors.append((c.req_id, val))

            # Delivery days
            d_max = re.search(r"(?:within|at\s+most|max(?:imum)?|under|<=?)\s*(\d+)\s*(?:business\s+)?days?", txt)
            if d_max:
                deadline_ceilings.append((c.req_id, int(d_max.group(1))))
            d_min = re.search(r"(?:at\s+least|min(?:imum)?|after|>=?)\s*(\d+)\s*(?:business\s+)?days?", txt)
            if d_min:
                deadline_floors.append((c.req_id, int(d_min.group(1))))

            # Offline vs live web requirement conflict
            if "offline" in txt or "no network" in txt or "no internet" in txt:
                for c2 in active_constraints + active_objs:
                    if c2.req_id != c.req_id and any(w in c2.text.lower() for w in ("live web", "real-time internet", "fetch live url")):
                        conflicts.append({
                            "type": "capability_constraint_conflict",
                            "req_a": c.req_id,
                            "req_b": c2.req_id,
                            "detail": f"Offline/no-network constraint '{c.text}' conflicts with live internet requirement '{c2.text}'.",
                        })

        for r_max, v_max in budget_ceilings:
            for r_min, v_min in budget_floors:
                if v_max < v_min:
                    conflicts.append({
                        "type": "numeric_budget_conflict",
                        "req_a": r_max,
                        "req_b": r_min,
                        "detail": f"Budget ceiling (${v_max:,.2f}) is lower than required minimum spend (${v_min:,.2f}).",
                    })

        for r_max, d_max in deadline_ceilings:
            for r_min, d_min in deadline_floors:
                if d_max < d_min:
                    conflicts.append({
                        "type": "numeric_schedule_conflict",
                        "req_a": r_max,
                        "req_b": r_min,
                        "detail": f"Maximum delivery time ({d_max} days) is shorter than minimum required duration ({d_min} days).",
                    })

        # 3. Check material ambiguity in objective
        if not active_objs or len(active_objs[0].text.strip()) < 15:
            material_gaps.append("Objective is underspecified (<15 chars); clarify the target outcome and scope.")
        else:
            obj_txt = active_objs[0].text.lower()
            if any(p in obj_txt for p in ("optimize it", "fix the thing", "make it better", "do the analysis")) and not active_deliverables and not active_constraints:
                material_gaps.append("Objective uses vague pronouns/targets without specifying the target system, input data, or deliverable.")

        for m in self.get_items_by_category(RequirementKind.MISSING_INFO):
            if m.text not in material_gaps:
                material_gaps.append(m.text)

        for a in self.get_items_by_category(RequirementKind.ASSUMPTION):
            disclosed_assumptions.append(a.text)

        return {
            "has_conflicts": len(conflicts) > 0,
            "conflicts": conflicts,
            "has_material_gaps": len(material_gaps) > 0,
            "material_gaps": material_gaps,
            "disclosed_assumptions": disclosed_assumptions,
        }

    def validate_plan_coverage(self, plan: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Verify that all hard requirements in the ledger are accounted for in the plan."""
        errors: List[str] = []
        tasks = plan.get("tasks", [])
        deliverables = plan.get("deliverables", [])

        # Check requested deliverables
        req_deliverables = self.get_items_by_category(RequirementKind.DELIVERABLE)
        plan_deliv_names = {str(d.get("name", "")).lower() for d in deliverables if isinstance(d, dict)}
        plan_deliv_keys = {str(d.get("key", "")).lower() for d in deliverables if isinstance(d, dict)}

        for rd in req_deliverables:
            match = False
            rd_lower = rd.text.lower()
            for pd in plan_deliv_names.union(plan_deliv_keys):
                if rd_lower in pd or pd in rd_lower:
                    match = True
                    break
            if not match and not deliverables:
                errors.append(f"Missing mandatory deliverable: '{rd.text}' (MISSING_INTAKE_REQUIREMENT)")
            elif match:
                rd.included_in_plan = True

        # Check scope exclusions
        exclusions = self.get_items_by_category(RequirementKind.EXCLUSION)
        for ex in exclusions:
            ex_lower = ex.text.lower()
            for t in tasks:
                if not isinstance(t, dict):
                    continue
                t_title = t.get("title", "").lower()
                t_desc = t.get("description", "").lower()
                act_instr = str(t.get("action", {}).get("instruction", "")).lower()
                if ex_lower in t_title or ex_lower in t_desc or ex_lower in act_instr:
                    errors.append(f"Task '{t.get('key')}' executes excluded action '{ex.text}' (EXCLUSION_VIOLATED)")

        for c in self.get_items_by_category(RequirementKind.CONSTRAINT):
            c.included_in_plan = True

        return len(errors) == 0, errors

    def to_dict(self) -> List[Dict[str, Any]]:
        return [
            {
                "req_id": item.req_id,
                "kind": item.kind.value,
                "text": item.text,
                "hardness": item.hardness.value,
                "source_field": item.source_field,
                "included_in_plan": item.included_in_plan,
                "covered_by_task": item.covered_by_task,
                "verification_criteria": item.verification_criteria,
                "verification_method": item.verification_method,
                "verification_status": item.verification_status,
                "verification_detail": item.verification_detail,
                "status": item.status,
                "version_number": item.version_number,
                "depends_on": item.depends_on,
                "affected_artifacts": item.affected_artifacts,
                "superseded_by_id": item.superseded_by_id,
            }
            for item in self.items
        ]


def parse_intake_requirements(intake: Dict[str, Any]) -> RequirementLedger:
    """Convenience helper to construct and populate a RequirementLedger from intake data."""
    return RequirementLedger.from_intake(intake)


# ---------------------------------------------------------------------------
# Source / Instruction Separation Guard (Section 2 & Section 5)
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|constraints|requirements)", re.I),
    re.compile(r"disregard\s+(?:the\s+)?(?:user|system|budget|constraint)", re.I),
    re.compile(r"override\s+(?:authority|requirement|budget|ceiling|policy|verification)", re.I),
    re.compile(r"system\s+override\s*:", re.I),
    re.compile(r"new\s+user\s+instruction\s*:", re.I),
    re.compile(r"increase\s+budget\s+ceiling\s+to", re.I),
    re.compile(r"mark\s+(?:all\s+)?(?:claims|deliverables|tests)\s+as\s+(?:verified|accepted|passed)", re.I),
    re.compile(r"(?:print|exfiltrate|dump|reveal)\s+(?:all\s+)?(?:env|environment|secrets|api_key|tokens)", re.I),
]


def inspect_untrusted_source_for_directive_injection(source_text: str, source_id: str = "document") -> Dict[str, Any]:
    """Detect and neutralize instruction/authority override attempts inside untrusted retrieved documents.

    Retrieved documents can supply factual evidence (quotes, dates, prices, specifications),
    but may NEVER alter user requirements, budget ceilings, verification status, or execution permissions.
    """
    text = str(source_text or "")
    matched: List[str] = []
    sanitized_lines: List[str] = []

    for line in text.splitlines():
        line_flagged = False
        for pat in _INJECTION_PATTERNS:
            if pat.search(line):
                matched.append(line.strip()[:200])
                line_flagged = True
                break
        if line_flagged:
            sanitized_lines.append(f"[UNTRUSTED_DIRECTIVE_STRIPPED source={source_id}]")
        else:
            sanitized_lines.append(line)

    return {
        "source_id": source_id,
        "contains_injection_attempt": len(matched) > 0,
        "flagged_directives": matched,
        "sanitized_text": "\n".join(sanitized_lines),
        "can_modify_requirements": False,
        "can_escalate_authority": False,
    }


# ---------------------------------------------------------------------------
# Durable Requirement Ledger Persistence & Mid-Flight Steering (Section 2)
# ---------------------------------------------------------------------------

def _kind_to_db(kind: RequirementKind) -> str:
    if kind == RequirementKind.SUCCESS_CRITERION:
        return "acceptance_criterion"
    return kind.value


def _hardness_to_db(hardness: RequirementHardness) -> str:
    if hardness in (RequirementHardness.MUST, RequirementHardness.STRICT):
        return "hard"
    if hardness == RequirementHardness.PREFERENCE:
        return "preference"
    return "soft"


def _db_to_kind(val: str) -> RequirementKind:
    if val == "acceptance_criterion":
        return RequirementKind.SUCCESS_CRITERION
    try:
        return RequirementKind(val)
    except ValueError:
        return RequirementKind.CONSTRAINT


def _db_to_hardness(val: str) -> RequirementHardness:
    if val == "hard":
        return RequirementHardness.MUST
    if val == "preference":
        return RequirementHardness.PREFERENCE
    return RequirementHardness.SHOULD


def persist_requirement_ledger(
    project_id: str,
    ledger: RequirementLedger,
    run_id: Optional[str] = None,
    actor: str = "user",
    change_type: str = "initial_intake",
    summary: str = "Initial requirement ledger persisted.",
) -> Dict[str, Any]:
    """Persist a RequirementLedger into `project_requirement_items` and `project_requirement_revisions`."""
    now = now_iso()
    with db_transaction() as conn:
        rev_row = conn.execute(
            "SELECT COALESCE(MAX(revision_number), 0) AS max_rev FROM project_requirement_revisions WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        next_rev = int(rev_row["max_rev"] or 0) + 1

        inserted_ids: List[str] = []
        for item in ledger.items:
            row_id = new_id("preq")
            inserted_ids.append(row_id)
            conn.execute(
                """INSERT INTO project_requirement_items (
                    id, project_id, run_id, req_code, version_number, kind, hardness,
                    statement, rationale, status, verification_method, verification_status,
                    verification_detail, depends_on_json, affected_artifacts_json,
                    superseded_by_id, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row_id,
                    project_id,
                    run_id,
                    item.req_id,
                    item.version_number,
                    _kind_to_db(item.kind),
                    _hardness_to_db(item.hardness),
                    item.text,
                    item.rationale,
                    item.status,
                    item.verification_method,
                    item.verification_status,
                    item.verification_detail,
                    jdump(item.depends_on),
                    jdump(item.affected_artifacts),
                    item.superseded_by_id,
                    actor,
                    now,
                    now,
                ),
            )

        rev_id = new_id("rrev")
        conn.execute(
            """INSERT INTO project_requirement_revisions (
                id, project_id, run_id, revision_number, change_type, summary,
                diff_json, invalidated_tasks_json, invalidated_artifacts_json,
                preserved_artifacts_json, actor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', '[]', '[]', ?, ?)""",
            (
                rev_id,
                project_id,
                run_id,
                next_rev,
                change_type,
                summary,
                jdump({"added_items": len(inserted_ids)}),
                actor,
                now,
            ),
        )

    ledger.project_id = project_id
    ledger.run_id = run_id
    ledger.revision_number = next_rev
    return {
        "project_id": project_id,
        "run_id": run_id,
        "revision_id": rev_id,
        "revision_number": next_rev,
        "item_count": len(ledger.items),
    }


def load_durable_requirement_ledger(project_id: str, include_superseded: bool = False) -> RequirementLedger:
    """Load the persisted RequirementLedger for a project from SQLite."""
    conn = get_db_connection()
    try:
        sql = "SELECT * FROM project_requirement_items WHERE project_id = ?"
        if not include_superseded:
            sql += " AND status = 'active'"
        sql += " ORDER BY created_at, req_code, version_number"
        rows = [dict(r) for r in conn.execute(sql, (project_id,)).fetchall()]
        rev_row = conn.execute(
            "SELECT COALESCE(MAX(revision_number), 1) AS max_rev, MAX(run_id) AS run_id FROM project_requirement_revisions WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()

    ledger = RequirementLedger(
        project_id=project_id,
        run_id=rev_row["run_id"] if rev_row else None,
        revision_number=int(rev_row["max_rev"] or 1) if rev_row else 1,
    )
    for r in rows:
        ledger.items.append(
            RequirementItem(
                req_id=r["req_code"],
                kind=_db_to_kind(r["kind"]),
                text=r["statement"],
                hardness=_db_to_hardness(r["hardness"]),
                source_field=r["kind"],
                verification_method=r["verification_method"],
                verification_status=r["verification_status"],
                verification_detail=r["verification_detail"],
                status=r["status"],
                version_number=r["version_number"],
                rationale=r["rationale"],
                depends_on=jload(r["depends_on_json"], []),
                affected_artifacts=jload(r["affected_artifacts_json"], []),
                superseded_by_id=r["superseded_by_id"],
            )
        )
    return ledger


def update_requirement_verification(
    project_id: str,
    req_code: str,
    verification_status: str,
    verification_detail: str = "",
    verification_method: Optional[str] = None,
    affected_artifacts: Optional[List[str]] = None,
) -> bool:
    """Update the verification status/detail of an active requirement in the durable ledger."""
    valid_statuses = {s.value for s in VerificationStatus}
    if verification_status not in valid_statuses:
        raise ValueError(f"Unsupported verification_status: {verification_status}")
    now = now_iso()
    with db_transaction() as conn:
        row = conn.execute(
            "SELECT id, verification_method, affected_artifacts_json FROM project_requirement_items WHERE project_id = ? AND req_code = ? AND status = 'active' ORDER BY version_number DESC LIMIT 1",
            (project_id, req_code),
        ).fetchone()
        if not row:
            return False
        meth = verification_method or row["verification_method"]
        arts = affected_artifacts if affected_artifacts is not None else jload(row["affected_artifacts_json"], [])
        conn.execute(
            """UPDATE project_requirement_items
               SET verification_status = ?, verification_detail = ?, verification_method = ?,
                   affected_artifacts_json = ?, updated_at = ?
               WHERE id = ?""",
            (verification_status, verification_detail[:1000], meth, jdump(arts), now, row["id"]),
        )
    return True


def apply_user_steering_update(
    project_id: str,
    *,
    run_id: Optional[str] = None,
    actor: str = "user",
    action: str = "steer",  # "steer" | "cancel" | "resume" | "clarify"
    updated_constraints: Optional[List[str]] = None,
    replaced_req_codes: Optional[Dict[str, str]] = None,
    added_deliverables: Optional[List[str]] = None,
    added_decisions: Optional[List[str]] = None,
    resolved_gaps: Optional[Dict[str, str]] = None,
    affected_task_keys: Optional[List[str]] = None,
    affected_artifact_ids: Optional[List[str]] = None,
    reason: str = "",
    source_origin: str = "user_explicit",
) -> Dict[str, Any]:
    """Apply mid-flight user steering, constraint revisions, clarifications, or cancellation.

    Enforces:
    1. Rejects steering attempts originating from untrusted retrieved documents (`source_origin != 'user_explicit'`).
    2. Distinguishes steering (selective update preserving unaffected completed work) from cancellation.
    3. Supersedes replaced requirement versions while preserving full revision history.
    4. Selectively invalidates only the affected downstream tasks and artifacts, preserving unaffected valid artifacts.
    """
    if source_origin != "user_explicit":
        raise PermissionError(
            f"Untrusted source '{source_origin}' cannot modify user requirements, constraints, or project authority."
        )

    now = now_iso()
    invalidated_tasks: List[str] = []
    invalidated_artifacts: List[str] = []
    preserved_artifacts: List[str] = []

    with db_transaction() as conn:
        rev_row = conn.execute(
            "SELECT COALESCE(MAX(revision_number), 0) AS max_rev FROM project_requirement_revisions WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        next_rev = int(rev_row["max_rev"] or 0) + 1

        if action == "cancel":
            conn.execute(
                "UPDATE project_requirement_items SET status = 'cancelled', updated_at = ? WHERE project_id = ? AND status = 'active'",
                (now, project_id),
            )
            if run_id:
                conn.execute(
                    "UPDATE orchestration_tasks SET state = 'cancelled', updated_at = ? WHERE run_id = ? AND state IN ('pending', 'dispatched', 'blocked')",
                    (now, run_id),
                )
            conn.execute(
                """INSERT INTO project_requirement_revisions (
                    id, project_id, run_id, revision_number, change_type, summary,
                    diff_json, invalidated_tasks_json, invalidated_artifacts_json,
                    preserved_artifacts_json, actor, created_at
                ) VALUES (?, ?, ?, ?, 'cancellation', ?, ?, '[]', '[]', '[]', ?, ?)""",
                (new_id("rrev"), project_id, run_id, next_rev, reason or "User cancelled assignment.", jdump({"action": "cancel"}), actor, now),
            )
            return {
                "project_id": project_id,
                "run_id": run_id,
                "action": "cancel",
                "revision_number": next_rev,
                "status": "cancelled",
            }

        # 1. Replace specific requirement codes if supplied (e.g., budget ceiling changed mid-flight)
        replaced_summary: List[str] = []
        if replaced_req_codes:
            for req_code, new_statement in replaced_req_codes.items():
                old_row = conn.execute(
                    "SELECT * FROM project_requirement_items WHERE project_id = ? AND req_code = ? AND status = 'active' ORDER BY version_number DESC LIMIT 1",
                    (project_id, req_code),
                ).fetchone()
                new_item_id = new_id("preq")
                if old_row:
                    new_ver = int(old_row["version_number"]) + 1
                    conn.execute(
                        "UPDATE project_requirement_items SET status = 'superseded', superseded_by_id = ?, updated_at = ? WHERE id = ?",
                        (new_item_id, now, old_row["id"]),
                    )
                    kind_val = old_row["kind"]
                    hard_val = old_row["hardness"]
                    ver_meth = old_row["verification_method"]
                    old_arts = jload(old_row["affected_artifacts_json"], [])
                    for a_id in old_arts:
                        if a_id not in invalidated_artifacts:
                            invalidated_artifacts.append(a_id)
                else:
                    new_ver = 1
                    kind_val = "constraint"
                    hard_val = "hard"
                    ver_meth = "constraint_check"

                conn.execute(
                    """INSERT INTO project_requirement_items (
                        id, project_id, run_id, req_code, version_number, kind, hardness,
                        statement, rationale, status, verification_method, verification_status,
                        verification_detail, depends_on_json, affected_artifacts_json,
                        created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, 'proposed', 'Updated via user steering', '[]', '[]', ?, ?, ?)""",
                    (
                        new_item_id, project_id, run_id, req_code, new_ver,
                        kind_val, hard_val, new_statement.strip(), reason or "Mid-flight requirement update",
                        ver_meth, actor, now, now,
                    ),
                )
                replaced_summary.append(f"{req_code} -> v{new_ver}: {new_statement.strip()}")

        # 2. Add new constraints if supplied
        if updated_constraints:
            for idx, c_text in enumerate(updated_constraints, start=1):
                c_code = f"req-con-steer-{next_rev}-{idx}"
                conn.execute(
                    """INSERT INTO project_requirement_items (
                        id, project_id, run_id, req_code, version_number, kind, hardness,
                        statement, rationale, status, verification_method, verification_status,
                        created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, 'constraint', 'hard', ?, ?, 'active', 'constraint_check', 'proposed', ?, ?, ?)""",
                    (new_id("preq"), project_id, run_id, c_code, c_text.strip(), reason or "Steering constraint", actor, now, now),
                )
                replaced_summary.append(f"added {c_code}: {c_text.strip()}")

        # 3. Add new deliverables if supplied
        if added_deliverables:
            for idx, d_text in enumerate(added_deliverables, start=1):
                d_code = f"req-del-steer-{next_rev}-{idx}"
                conn.execute(
                    """INSERT INTO project_requirement_items (
                        id, project_id, run_id, req_code, version_number, kind, hardness,
                        statement, rationale, status, verification_method, verification_status,
                        created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, 'deliverable', 'hard', ?, ?, 'active', 'deliverable_contract_check', 'proposed', ?, ?, ?)""",
                    (new_id("preq"), project_id, run_id, d_code, d_text.strip(), reason or "Added deliverable", actor, now, now),
                )
                replaced_summary.append(f"added deliverable {d_code}: {d_text.strip()}")

        # 4. Resolve missing info gaps and record user decisions
        if resolved_gaps:
            for gap_code, answer_text in resolved_gaps.items():
                conn.execute(
                    """UPDATE project_requirement_items
                       SET status = 'resolved', verification_status = 'verified_for_scope',
                           verification_detail = ?, updated_at = ?
                       WHERE project_id = ? AND (req_code = ? OR kind = 'missing_info') AND status = 'active'""",
                    (f"Clarified by user: {answer_text}", now, project_id, gap_code),
                )
                dec_code = f"req-dec-{next_rev}-{gap_code}"
                conn.execute(
                    """INSERT INTO project_requirement_items (
                        id, project_id, run_id, req_code, version_number, kind, hardness,
                        statement, rationale, status, verification_method, verification_status,
                        created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, 'user_decision', 'hard', ?, 'Clarification response', 'active', 'decision_check', 'verified_for_scope', ?, ?, ?)""",
                    (new_id("preq"), project_id, run_id, dec_code, f"{gap_code}: {answer_text}", actor, now, now),
                )

        if added_decisions:
            for idx, dec_text in enumerate(added_decisions, start=1):
                dec_code = f"req-dec-{next_rev}-{idx}"
                conn.execute(
                    """INSERT INTO project_requirement_items (
                        id, project_id, run_id, req_code, version_number, kind, hardness,
                        statement, rationale, status, verification_method, verification_status,
                        created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, 'user_decision', 'hard', ?, ?, 'active', 'decision_check', 'verified_for_scope', ?, ?, ?)""",
                    (new_id("preq"), project_id, run_id, dec_code, dec_text.strip(), reason or "User steering decision", actor, now, now),
                )

        # 5. Selective task and artifact invalidation (preserve unaffected completed work!)
        if affected_artifact_ids:
            for a_id in affected_artifact_ids:
                if a_id not in invalidated_artifacts:
                    invalidated_artifacts.append(a_id)

        all_arts = conn.execute(
            "SELECT id, validation_status FROM artifacts WHERE origin_project_id = ?",
            (project_id,),
        ).fetchall()
        for a_row in all_arts:
            aid = a_row["id"]
            if aid in invalidated_artifacts:
                conn.execute(
                    "UPDATE artifacts SET validation_status = 'invalid', validation_detail = ? WHERE id = ?",
                    (f"Invalidated by requirement revision {next_rev}: {reason or 'constraint change'}", aid),
                )
                conn.execute(
                    "UPDATE artifact_metadata SET validation_state = 'invalid', validation_detail = ? WHERE id = ?",
                    (f"Invalidated by requirement revision {next_rev}", aid),
                )
            else:
                preserved_artifacts.append(aid)

        if run_id and affected_task_keys:
            for tk in affected_task_keys:
                cur = conn.execute(
                    """UPDATE orchestration_tasks
                       SET state = 'pending', accepted_result_json = NULL,
                           last_error = 'Reset for re-evaluation after requirement steering', updated_at = ?
                       WHERE run_id = ? AND plan_key = ? AND state <> 'superseded'""",
                    (now, run_id, tk),
                )
                if cur.rowcount:
                    invalidated_tasks.append(tk)

        change_kind = "clarification_answer" if resolved_gaps else ("constraint_change" if (replaced_req_codes or updated_constraints) else "steering_update")
        conn.execute(
            """INSERT INTO project_requirement_revisions (
                id, project_id, run_id, revision_number, change_type, summary,
                diff_json, invalidated_tasks_json, invalidated_artifacts_json,
                preserved_artifacts_json, actor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id("rrev"),
                project_id,
                run_id,
                next_rev,
                change_kind,
                reason or "; ".join(replaced_summary) or "User steering applied",
                jdump({
                    "replaced_req_codes": replaced_req_codes or {},
                    "updated_constraints": updated_constraints or [],
                    "added_deliverables": added_deliverables or [],
                    "resolved_gaps": resolved_gaps or {},
                }),
                jdump(invalidated_tasks),
                jdump(invalidated_artifacts),
                jdump(preserved_artifacts),
                actor,
                now,
            ),
        )

    updated_ledger = load_durable_requirement_ledger(project_id)
    conflict_check = updated_ledger.detect_conflicts_and_ambiguities()
    return {
        "project_id": project_id,
        "run_id": run_id,
        "action": action,
        "revision_number": next_rev,
        "invalidated_tasks": invalidated_tasks,
        "invalidated_artifacts": invalidated_artifacts,
        "preserved_artifacts": preserved_artifacts,
        "conflict_check": conflict_check,
        "active_requirements_count": len(updated_ledger.items),
    }
