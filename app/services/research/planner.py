"""Autonomous research strategy planner and budget manager.

Generates research plans, decomposes questions into subquestions and targeted queries,
enforces research budgets, and identifies evidence gaps for bounded iterative research.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ResearchBudget:
    max_search_queries: int = 15
    max_retrieved_documents: int = 10
    max_browsing_seconds: int = 120
    max_inference_tokens: int = 32000
    max_depth: int = 2
    max_iterations: int = 3
    # Live counters
    queries_executed: int = 0
    documents_retrieved: int = 0
    browsing_seconds_used: float = 0.0
    tokens_consumed: int = 0
    depth_reached: int = 0
    iterations_run: int = 0

    def can_search(self) -> bool:
        return self.queries_executed < self.max_search_queries

    def can_retrieve(self) -> bool:
        return self.documents_retrieved < self.max_retrieved_documents

    def can_iterate(self) -> bool:
        return self.iterations_run < self.max_iterations and self.depth_reached < self.max_depth

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_search_queries": self.max_search_queries,
            "max_retrieved_documents": self.max_retrieved_documents,
            "max_browsing_seconds": self.max_browsing_seconds,
            "max_inference_tokens": self.max_inference_tokens,
            "max_depth": self.max_depth,
            "max_iterations": self.max_iterations,
            "queries_executed": self.queries_executed,
            "documents_retrieved": self.documents_retrieved,
            "browsing_seconds_used": round(self.browsing_seconds_used, 2),
            "tokens_consumed": self.tokens_consumed,
            "depth_reached": self.depth_reached,
            "iterations_run": self.iterations_run,
        }


@dataclass
class ResearchStrategy:
    main_question: str
    subquestions: List[str]
    initial_queries: List[str]
    desired_source_types: List[str] = field(default_factory=lambda: ["official", "peer_reviewed", "industry_report", "news"])
    publication_date_requirement: Optional[str] = None
    geographic_scope: Optional[str] = None
    required_evidence: List[str] = field(default_factory=list)
    verification_standards: str = "Corroborate quantitative claims with at least 2 independent reputable sources."
    expected_deliverables: List[str] = field(default_factory=lambda: ["Markdown Research Report with deterministic citations", "Evidence Ledger", "Source Library Snapshot"])
    budget: ResearchBudget = field(default_factory=ResearchBudget)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "main_question": self.main_question,
            "subquestions": self.subquestions,
            "initial_queries": self.initial_queries,
            "desired_source_types": self.desired_source_types,
            "publication_date_requirement": self.publication_date_requirement,
            "geographic_scope": self.geographic_scope,
            "required_evidence": self.required_evidence,
            "verification_standards": self.verification_standards,
            "expected_deliverables": self.expected_deliverables,
            "budget": self.budget.to_dict(),
            "created_at": self.created_at,
        }


class ResearchPlanner:
    """Creates, validates, and refines autonomous research strategies."""

    # Keywords signaling research requirement in a project objective
    RESEARCH_SIGNALS = [
        "research", "investigate", "find sources", "web search", "compare technologies",
        "market analysis", "state of the art", "latest developments", "literature review",
        "fact check", "citations", "reputable sources", "gather evidence", "recent news",
        "pricing", "benchmarks", "overview of"
    ]

    def __init__(self, default_budget: Optional[ResearchBudget] = None):
        self.default_budget = default_budget or ResearchBudget()

    def requires_research(self, objective: str, instructions: str = "") -> bool:
        """Determines if the project objective/instructions indicate an external research requirement."""
        combined = f"{objective} {instructions}".lower()
        return any(signal in combined for signal in self.RESEARCH_SIGNALS)

    def generate_strategy(
        self,
        objective: str,
        instructions: str = "",
        custom_budget: Optional[ResearchBudget] = None,
    ) -> ResearchStrategy:
        """Deconstructs project objective into a structured research strategy."""
        budget = custom_budget or ResearchBudget()

        # Extract main question
        main_q = self._extract_main_question(objective)

        # Decompose into subquestions
        subqs = self._generate_subquestions(objective, instructions)

        # Generate targeted initial search queries
        queries = self._generate_queries(main_q, subqs)

        # Cap queries to budget
        queries = queries[:budget.max_search_queries]

        required_evidence = [
            f"Official documentation or primary sources for {main_q}",
            "Recent comparative metrics, benchmarks, or statistics",
            "Authoritative independent analysis or evaluations",
        ]

        return ResearchStrategy(
            main_question=main_q,
            subquestions=subqs,
            initial_queries=queries,
            required_evidence=required_evidence,
            budget=budget,
        )

    def identify_evidence_gaps(
        self,
        strategy: ResearchStrategy,
        claims: List[Dict[str, Any]],
        retrieved_sources: List[Dict[str, Any]],
    ) -> List[str]:
        """Identifies unanswered subquestions or uncorroborated claims to propose follow-up queries."""
        if not strategy.budget.can_iterate():
            return []

        follow_up_queries: List[str] = []

        # Find unverified or contradicted claims
        for claim in claims:
            status = claim.get("verification_status")
            if status in ("UNVERIFIED", "INSUFFICIENT_EVIDENCE", "CONTRADICTED"):
                claim_text = claim.get("claim_text", "")
                if claim_text:
                    # Clean query words
                    clean_words = [w for w in re.findall(r"\w+", claim_text) if len(w) > 3][:6]
                    if clean_words:
                        follow_up_queries.append(" ".join(clean_words))

        # Check subquestion coverage
        all_text = " ".join([s.get("title", "") + " " + s.get("snippet", "") for s in retrieved_sources]).lower()
        for subq in strategy.subquestions:
            key_words = [w.lower() for w in re.findall(r"\w+", subq) if len(w) > 4]
            matches = sum(1 for kw in key_words if kw in all_text)
            if matches < max(1, len(key_words) // 2):
                follow_up_queries.append(subq)

        # Deduplicate and respect budget
        unique_queries = list(dict.fromkeys(follow_up_queries))
        remaining = strategy.budget.max_search_queries - strategy.budget.queries_executed
        return unique_queries[:max(0, remaining)]

    def _extract_main_question(self, objective: str) -> str:
        clean = re.sub(r"^(please\s+|research\s+|investigate\s+|find\s+out\s+)", "", objective, flags=re.IGNORECASE).strip()
        if not clean.endswith("?"):
            clean = f"What are the key facts, developments, and findings regarding {clean}?"
        return clean

    def _generate_subquestions(self, objective: str, instructions: str) -> List[str]:
        subqs = [
            f"What is the current technical or industry state regarding {objective[:60].strip()}?",
            "What are the major competing technologies, approaches, or key figures?",
            "What quantitative benchmarks, costs, or verified statistics exist?",
            "What are the principal limitations, criticisms, or conflicting perspectives?",
        ]
        return subqs

    def _generate_queries(self, main_question: str, subquestions: List[str]) -> List[str]:
        # Formulate concise search queries from questions
        queries = []
        # Main query
        words = [w for w in re.findall(r"\w+", main_question) if w.lower() not in {"what", "are", "the", "key", "facts", "regarding", "and", "for", "with"}]
        if words:
            queries.append(" ".join(words[:7]))

        for subq in subquestions:
            sw = [w for w in re.findall(r"\w+", subq) if w.lower() not in {"what", "is", "are", "the", "current", "major", "principal", "or", "regarding"}]
            if sw:
                queries.append(" ".join(sw[:6]))

        # Add an official / documentation / benchmark modifier
        if words:
            queries.append(f"{' '.join(words[:4])} official documentation report")

        return list(dict.fromkeys(queries))
