"""Adversarial Critic & Independent Audit Pass (Phase C Milestone 3).

Separates generative drafting from critique by dispatching an adversarial auditor pass
that actively searches for unsupported assumptions, prohibited claims, arithmetic discrepancies,
and expired deadlines against the DeliverableRequirementContract and EvidenceLedger.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Union

from app.services.orchestration.verification import DeterministicVerificationEngine
from app.services.research.citations import CitationEngine

logger = logging.getLogger("qc.research.critic")

CRITIC_SYSTEM_PROMPT = """You are an adversarial compliance auditor.
Your job is to find inaccuracies, unverified assumptions, unsupported dates, and mathematical errors in the provided draft report.
Do not summarize the draft. Do not praise the draft.
Evaluate the draft strictly against the provided Evidence Ledger and Task Contract.
Search specifically for:
1. Claims that assert an unannounced future funding round as confirmed.
2. Mandatory match assertions when guidelines recommend or permit variances.
3. Conflated program names, measure letters, or agency email domains.
4. Any mathematical or percentage calculation discrepancies.
5. Factual assertions lacking citation references [src-XXX] or [N].
"""


@dataclass
class CriticReport:
    """Structured report returned by the adversarial critique pass."""
    passed: bool
    score: float  # 0.0 to 1.0
    fatal_errors: List[Dict[str, Any]] = field(default_factory=list)
    negative_violations: List[Dict[str, Any]] = field(default_factory=list)
    arithmetic_errors: List[Dict[str, Any]] = field(default_factory=list)
    unverified_assumptions: List[Dict[str, Any]] = field(default_factory=list)
    expired_deadlines: List[Dict[str, Any]] = field(default_factory=list)
    missing_categories: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 2),
            "fatal_errors": self.fatal_errors,
            "negative_violations": self.negative_violations,
            "arithmetic_errors": self.arithmetic_errors,
            "unverified_assumptions": self.unverified_assumptions,
            "expired_deadlines": self.expired_deadlines,
            "missing_categories": self.missing_categories,
            "recommendations": self.recommendations,
        }


class AdversarialCritic:
    """Independent auditor evaluating deliverable drafts against strict contracts."""

    def __init__(self, citation_engine: Optional[CitationEngine] = None):
        self.citation_engine = citation_engine or CitationEngine()
        self.math_engine = DeterministicVerificationEngine()

    def audit_draft(
        self,
        draft_markdown: str,
        contract: Optional[Any] = None,
        sources: Optional[List[Dict[str, Any]]] = None,
        claims: Optional[List[Dict[str, Any]]] = None,
        project_id: Optional[str] = None,
        reference_date: Optional[Union[str, date]] = None,
    ) -> CriticReport:
        """Execute a comprehensive adversarial audit on a deliverable draft."""
        sources = sources or []
        claims = claims or []

        # Extract contract fields safely
        if hasattr(contract, "to_dict"):
            cd = contract.to_dict()
        elif isinstance(contract, dict):
            cd = contract
        else:
            cd = {}

        crit = cd.get("measurable_acceptance_criteria", {})
        disallowed = cd.get("disallowed_claims") or crit.get("disallowed_claims") or []
        numerical = cd.get("numerical_constraints") or crit.get("numerical_constraints") or []
        temporal = cd.get("temporal_constraints") or crit.get("temporal_constraints") or []
        required_cats = cd.get("required_info_categories") or []

        fatal_errors: List[Dict[str, Any]] = []
        recommendations: List[str] = []

        # 1. Negative Constraint / Policy Conflation Audit
        negative_violations = self.math_engine.verify_negative_constraints(draft_markdown, disallowed)
        for v in negative_violations:
            fatal_errors.append({
                "type": "PROHIBITED_CLAIM_VIOLATION",
                "pattern": v["pattern"],
                "detail": f"Draft contains prohibited assertion: '{v['description']}'",
                "snippet": v.get("snippet", ""),
            })
            recommendations.append(f"Remove or correct prohibited assertion: '{v['description']}'")

        # 2. Arithmetic & Formula Audit
        arithmetic_errors = self.math_engine.verify_required_numerical_assertions(draft_markdown, numerical)
        for a_err in arithmetic_errors:
            fatal_errors.append({
                "type": "ARITHMETIC_ERROR",
                "name": a_err["name"],
                "detail": a_err["detail"],
            })
            recommendations.append(f"Recalculate '{a_err['name']}': expected {a_err.get('expected_value')}")

        # 3. Temporal & Deadline Expiration Audit
        expired_deadlines: List[Dict[str, Any]] = []
        if temporal:
            date_matches = re.finditer(
                r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b",
                draft_markdown,
            )
            for m in date_matches:
                date_str = m.group(0)
                snippet = draft_markdown[max(0, m.start() - 80): min(len(draft_markdown), m.end() + 80)].lower()

                # Ignore citation, publication, adoption, or historical reference dates
                if any(ref_kw in snippet for ref_kw in ("adopted", "published", "enacted", "approved", "effective", "passed", "accessed", "retrieved", "source:", "reference", "provenance")):
                    continue

                res_d = self.math_engine.verify_deadline(date_str, reference_date=reference_date, must_be_future=True)
                if not res_d["passed"]:
                    # Check if unannounced / expired caveat is present in the immediate context
                    if not any(k in snippet for k in ("unannounced", "closed", "past", "uncertain", "pending", "provisional", "historical", "tbd", "tba")):
                        expired_deadlines.append({
                            "date": date_str,
                            "detail": res_d["detail"],
                            "snippet": draft_markdown[max(0, m.start() - 30): min(len(draft_markdown), m.end() + 30)],
                        })
                        fatal_errors.append({
                            "type": "EXPIRED_UNFLAGGED_DEADLINE",
                            "date": date_str,
                            "detail": res_d["detail"],
                        })
                        recommendations.append(f"Update expired deadline '{date_str}' or add an explicit unannounced/closed cycle caveat.")

        # 4. Category Completeness Audit
        missing_categories: List[str] = []
        for cat in required_cats:
            words = [w.lower() for w in re.findall(r"\b[A-Za-z]{3,}\b", cat) if w.lower() not in ("and", "the", "for", "with")]
            if words and not any(w in draft_markdown.lower() for w in words):
                missing_categories.append(cat)
                recommendations.append(f"Address missing requirement category: '{cat}'")

        # 5. Unverified Factual Claim Screening (detecting assertions without [src-...] citations)
        unverified_assumptions: List[Dict[str, Any]] = []
        sentences = re.split(r"(?<=[.!?])\s+", draft_markdown)
        for sent in sentences:
            sent_str = sent.strip()
            # If sentence makes specific numerical or policy assertion but lacks citation
            has_quant = bool(re.search(r"\$\d+|\d+%\s+match|\d+\s+days|statutory|mandatory", sent_str, re.IGNORECASE))
            has_cit = bool(re.search(r"\[(?:\d+|src-[a-zA-Z0-9_\-]+)\]", sent_str))
            # Ignore headers, tables, bullet markers
            if has_quant and not has_cit and len(sent_str) > 40 and not sent_str.startswith("#"):
                unverified_assumptions.append({
                    "sentence": sent_str[:120],
                    "reason": "Specific numerical or statutory assertion lacks an inline source citation [src-XXX]",
                })

        # Calculate compliance score
        total_checks = (
            len(disallowed)
            + len(numerical)
            + (len(required_cats) if required_cats else 1)
            + (1 if expired_deadlines else 0)
        )
        total_issues = len(fatal_errors) + len(missing_categories)
        if total_issues == 0:
            score = 1.0
            passed = True
        elif len(fatal_errors) > 0:
            score = max(0.2, 0.7 - (0.15 * len(fatal_errors)))
            passed = False
        else:
            score = max(0.5, 0.9 - (0.10 * len(missing_categories)))
            passed = False

        return CriticReport(
            passed=passed,
            score=score,
            fatal_errors=fatal_errors,
            negative_violations=negative_violations,
            arithmetic_errors=arithmetic_errors,
            unverified_assumptions=unverified_assumptions,
            expired_deadlines=expired_deadlines,
            missing_categories=missing_categories,
            recommendations=recommendations,
        )
