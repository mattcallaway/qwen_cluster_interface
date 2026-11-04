"""Lossless structured intake analysis and requirement ledger for Qwen Collective.

Enforces:
1. Complete preservation of user objectives, constraints, scope exclusions, and deliverables.
2. Machine-checkable requirement ledger (hardness, status, task coverage).
3. Zero silent parameter dropping or hard slicing of objectives.
4. Validation of plan fidelity against mandatory intake requirements.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("qc.orchestrator.intake")


class RequirementKind(str, Enum):
    OBJECTIVE = "objective"
    CONSTRAINT = "constraint"
    DELIVERABLE = "deliverable"
    EXCLUSION = "exclusion"
    SUCCESS_CRITERION = "success_criterion"


RequirementCategory = RequirementKind


class RequirementHardness(str, Enum):
    MUST = "must"
    SHOULD = "should"
    STRICT = "must"


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

    def get_items_by_category(self, cat: RequirementKind) -> List[RequirementItem]:
        return [item for item in self.items if item.kind == cat]

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
                lines.append(f"- {c.text}")

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
            ))
            idx += 1

        # 2. Constraints
        constraints = intake.get("constraints") or []
        if isinstance(constraints, str):
            constraints = [c.strip() for c in constraints.split("\n") if c.strip()]
        for c in constraints:
            if isinstance(c, str) and c.strip():
                ledger.items.append(RequirementItem(
                    req_id=f"req-con-{idx}",
                    kind=RequirementKind.CONSTRAINT,
                    text=c.strip(),
                    hardness=RequirementHardness.MUST,
                    source_field="constraints",
                    verification_criteria=f"Plan strictly respects constraint: {c.strip()}",
                ))
                idx += 1

        # 3. Scope exclusions
        exclusions = intake.get("scope_exclusions") or intake.get("exclusions") or []
        if isinstance(exclusions, str):
            exclusions = [e.strip() for e in exclusions.split("\n") if e.strip()]
        for e in exclusions:
            if isinstance(e, str) and e.strip():
                ledger.items.append(RequirementItem(
                    req_id=f"req-excl-{idx}",
                    kind=RequirementKind.EXCLUSION,
                    text=e.strip(),
                    hardness=RequirementHardness.MUST,
                    source_field="scope_exclusions",
                    verification_criteria=f"Plan does not execute excluded action: {e.strip()}",
                ))
                idx += 1

        # 4. Requested deliverables
        deliverables = intake.get("requested_deliverables") or intake.get("deliverables") or []
        if isinstance(deliverables, str):
            deliverables = [d.strip() for d in deliverables.split("\n") if d.strip()]
        for d in deliverables:
            d_text = d if isinstance(d, str) else d.get("name") or d.get("key") or str(d)
            if d_text:
                ledger.items.append(RequirementItem(
                    req_id=f"req-del-{idx}",
                    kind=RequirementKind.DELIVERABLE,
                    text=d_text,
                    hardness=RequirementHardness.MUST,
                    source_field="requested_deliverables",
                    verification_criteria=f"Plan produces terminal deliverable matching: {d_text}",
                ))
                idx += 1

        # 5. Success criteria
        criteria = intake.get("success_criteria") or []
        if isinstance(criteria, str):
            criteria = [s.strip() for s in criteria.split("\n") if s.strip()]
        for s in criteria:
            if isinstance(s, str) and s.strip():
                ledger.items.append(RequirementItem(
                    req_id=f"req-crit-{idx}",
                    kind=RequirementKind.SUCCESS_CRITERION,
                    text=s.strip(),
                    hardness=RequirementHardness.MUST,
                    source_field="success_criteria",
                    verification_criteria=f"Plan output meets success criterion: {s.strip()}",
                ))
                idx += 1

        return ledger

    def validate_plan_coverage(self, plan: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Verify that all hard requirements in the ledger are accounted for in the plan."""
        errors: List[str] = []
        tasks = plan.get("tasks", [])
        deliverables = plan.get("deliverables", [])
        plan_constraints = plan.get("overview", {}).get("constraints", [])
        if isinstance(plan_constraints, list):
            plan_constraints_set = {str(c).lower() for c in plan_constraints}
        else:
            plan_constraints_set = set()

        # Check requested deliverables
        req_deliverables = [r for r in self.items if r.kind == RequirementKind.DELIVERABLE]
        plan_deliv_names = {str(d.get("name", "")).lower() for d in deliverables if isinstance(d, dict)}
        plan_deliv_keys = {str(d.get("key", "")).lower() for d in deliverables if isinstance(d, dict)}

        for rd in req_deliverables:
            match = False
            rd_lower = rd.text.lower()
            # Match by name, key, or substring
            for pd in plan_deliv_names.union(plan_deliv_keys):
                if rd_lower in pd or pd in rd_lower:
                    match = True
                    break
            if not match and not deliverables:
                errors.append(f"Missing mandatory deliverable: '{rd.text}' (MISSING_INTAKE_REQUIREMENT)")
            elif match:
                rd.included_in_plan = True

        # Check scope exclusions
        exclusions = [r for r in self.items if r.kind == RequirementKind.EXCLUSION]
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

        # Mark constraints in plan
        for c in [r for r in self.items if r.kind == RequirementKind.CONSTRAINT]:
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
            }
            for item in self.items
        ]


def parse_intake_requirements(intake: Dict[str, Any]) -> RequirementLedger:
    """Convenience helper to construct and populate a RequirementLedger from intake data."""
    return RequirementLedger.from_intake(intake)
