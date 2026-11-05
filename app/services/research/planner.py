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

    def retrieval_task_from_feedback(self,ledger,path_id,url,worker_id,stage_key):
        """Propose graph data; approval/materialization remain controller duties."""
        path=next((p for p in ledger.items('path') if p['id']==path_id),None)
        if not path: raise ValueError('Missing scoped feedback proposal')
        if path.get('action')!='research_evidence_gap':
            raise ValueError('This proposal requires internal revalidation, not web retrieval')
        return {'key':'fetch_'+path_id[:20],'title':'Retrieve evidence for '+path['requirement_id'],
            'description':'Retrieve source bytes to investigate: '+path['question'],
            'stage':stage_key,'agent_role':'research',
            'action':{'type':'research_fetch','url':url,'feedback_path_id':path_id},
            'acceptance':{'kind':'json_fields','fields':['raw_sha256','extracted_sha256']},
            'allowed_nodes':[worker_id],'approval':'required','risk':'medium','depends_on':[],
            'max_attempts':1,'timeout_seconds':30,'required_capabilities':[]}

    def plan_evidence_feedback(self, ledger, budget, max_paths=3):
        """Return durable research proposals without queueing or authorizing work.

        Query/document counters remain execution counters, not proposal counts.
        The ledger bounds proposal cycles independently and suppresses duplicates.
        """
        if (not budget.can_iterate() or not budget.can_search() or not budget.can_retrieve()
                or budget.browsing_seconds_used >= budget.max_browsing_seconds
                or budget.tokens_consumed >= budget.max_inference_tokens):
            return {'assessment':ledger.assess(),'paths':[],'stop_reason':'RESEARCH_BUDGET_EXHAUSTED'}
        remaining=max(0,budget.max_search_queries-budget.queries_executed)
        return ledger.propose_paths(max_paths=min(max_paths,remaining),max_cycles=budget.max_iterations)

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
        deliverable_contracts: Optional[List[Dict[str, Any]]] = None,
    ) -> ResearchStrategy:
        """Deconstructs project objective and deliverable contracts into a structured research strategy."""
        budget = custom_budget or ResearchBudget()

        # Extract main question
        main_q = self._extract_main_question(objective)

        # Decompose into subquestions
        subqs = self._generate_subquestions(objective, instructions)

        # Generate targeted initial search queries incorporating deliverable needs
        queries = self._generate_queries(
            main_q, subqs, objective=objective, instructions=instructions,
            deliverable_contracts=deliverable_contracts,
        )

        # Cap queries to budget
        queries = queries[:budget.max_search_queries]

        anchor = self._topic_anchor(objective, instructions)
        required_evidence = [
            f"Official documentation or primary sources for {anchor}",
            f"Recent quantitative data or statistics about {anchor}",
            f"Authoritative independent analysis or evaluations of {anchor}",
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
        coverage_assessment: Optional[Dict[str, Any]] = None,
        deliverable_contracts: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        """Identifies unanswered subquestions, uncorroborated claims, and missing contract categories."""
        if not strategy.budget.can_iterate():
            return []

        follow_up_queries: List[str] = []

        # 1. Target missing categories from evidence coverage assessment (Phase 2 & 3)
        if coverage_assessment:
            for dev_ass in coverage_assessment.get("deliverable_assessments", []):
                d_name = dev_ass.get("name", "")
                missing = dev_ass.get("missing_categories", [])
                for m_cat in missing:
                    clean_cat = " ".join(m_cat.split("_"))
                    anchor = self._topic_anchor(strategy.main_question)
                    follow_up_queries.append(f"{anchor} {clean_cat}")

        # 2. Find unverified or contradicted claims
        for claim in claims:
            status = claim.get("verification_status")
            if status in ("UNVERIFIED", "INSUFFICIENT_EVIDENCE", "CONTRADICTED"):
                claim_text = claim.get("claim_text", "")
                if claim_text:
                    clean_words = [w for w in re.findall(r"\w+", claim_text) if len(w) > 3][:6]
                    if clean_words:
                        follow_up_queries.append(" ".join(clean_words))

        # 3. Check subquestion coverage
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

    # Words that carry no topical signal for search engines.
    _FILLER_WORDS = {
        "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at", "by", "with", "from",
        "into", "about", "as", "is", "are", "was", "were", "be", "been", "being", "it", "its",
        "this", "that", "these", "those", "what", "which", "who", "whom", "how", "why", "when",
        "where", "key", "facts", "regarding", "findings", "developments", "latest", "current",
        "major", "principal", "please", "research", "investigate", "produce", "create", "write",
        "prepare", "generate", "deliver", "provide", "detailed", "comprehensive", "report",
        "reports", "citations", "cited", "sources", "reputable", "identify", "compare",
        "including", "include", "should", "must", "will", "would", "could", "can", "also",
        "using", "use", "based", "each", "all", "any", "our", "their", "my", "your", "we",
        "brief", "memo", "summary", "analysis", "document", "deliverable", "deliverables",
        "following", "set", "list", "plan", "draft", "final", "overview", "find", "out",
        "gather", "evidence", "state", "art", "new", "recent", "other", "such", "than", "then",
        "assess", "evaluate", "analyze", "analyse", "examine", "review", "summarize", "explore",
        "under", "over", "between", "across", "within", "without", "via", "per", "among",
        "executive", "register", "stakeholder", "map", "memo", "need", "needs", "want", "help",
    }
    # Leading capitalized words that are not proper nouns.
    _NON_ENTITY_CAPS = {
        "Research", "Investigate", "Produce", "Create", "Write", "Prepare", "Please", "Compare",
        "Identify", "Provide", "Deliver", "Generate", "Analyze", "Analyse", "Evaluate", "Assess",
        "Review", "Summarize", "Find", "Gather", "Build", "Develop", "Draft", "What", "How",
        "Why", "When", "Where", "Which", "Who", "The", "A", "An", "Include", "Using", "Each",
        "Deliverables", "Deliverable", "Objective", "Instructions", "Context", "Report",
    }

    def _extract_entities_and_topics(self, text: str) -> Dict[str, List[str]]:
        """Extract named entities (multi-word proper nouns) and content keywords, in order."""
        text = text or ""
        entities: List[str] = []
        # Multi-word capitalized phrases, optionally joined by "of"/"and"/"de" (e.g. "Sonoma County Water Agency").
        for m in re.finditer(r"\b([A-Z][a-zA-Z0-9\-]+(?:\s+(?:of|and|de|the)?\s*[A-Z][a-zA-Z0-9\-]+)*)", text):
            phrase = m.group(1).strip()
            tokens = phrase.split()
            while tokens and tokens[0] in self._NON_ENTITY_CAPS:
                tokens = tokens[1:]
            while tokens and tokens[-1].lower() in {"of", "and", "de", "the"}:
                tokens = tokens[:-1]
            if not tokens:
                continue
            phrase = " ".join(tokens)
            # Single capitalized word at sentence start is weak; keep only if acronym/long or repeated.
            if len(tokens) == 1 and not (tokens[0].isupper() and len(tokens[0]) >= 2) and text.count(tokens[0]) < 2:
                # Still keep if not at sentence start
                start = m.start(1)
                prefix = text[max(0, start - 2):start]
                if start == 0 or prefix.strip().endswith((".", "!", "?", ":")) or prefix.endswith("\n"):
                    continue
            if phrase not in entities:
                entities.append(phrase)

        entity_tokens = {t.lower() for e in entities for t in e.split()}
        keywords: List[str] = []
        for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text):
            lw = w.lower()
            if lw in self._FILLER_WORDS or lw in entity_tokens:
                continue
            if lw not in keywords:
                keywords.append(lw)
        return {"entities": entities, "keywords": keywords}

    def _topic_anchor(self, objective: str, instructions: str = "") -> str:
        info = self._extract_entities_and_topics(f"{objective}\n{instructions}")
        parts: List[str] = []
        for e in info["entities"][:2]:
            parts.append(e)
        for k in info["keywords"][:4]:
            parts.append(k)
        anchor = " ".join(parts).strip()
        if not anchor:
            anchor = " ".join(re.findall(r"\w+", objective)[:6])
        return anchor

    def _generate_subquestions(self, objective: str, instructions: str) -> List[str]:
        info = self._extract_entities_and_topics(f"{objective}\n{instructions}")
        anchor = self._topic_anchor(objective, instructions)
        entity = info["entities"][0] if info["entities"] else anchor
        topic = " ".join(info["keywords"][:3]) or anchor
        subqs = [
            f"What is the current state of {topic} in {entity}?" if entity != topic else f"What is the current state of {topic}?",
            f"What official data, statistics, or measurements exist for {anchor}?",
            f"What risks, constraints, or challenges affect {anchor}?",
            f"What policies, plans, projects, or recommended actions address {anchor}?",
            f"What costs, funding sources, or economic impacts are associated with {anchor}?",
        ]
        return list(dict.fromkeys(subqs))

    def _generate_queries(
        self,
        main_question: str,
        subquestions: List[str],
        objective: str = "",
        instructions: str = "",
        deliverable_contracts: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        source_text = f"{objective}\n{instructions}" if objective else main_question
        info = self._extract_entities_and_topics(source_text)
        entities = info["entities"]
        keywords = info["keywords"]
        anchor = self._topic_anchor(objective or main_question, instructions)
        primary_entity = entities[0] if entities else ""

        queries: List[str] = [anchor]

        # 1. Deliverable-specific targeted queries (Phase 1 & 2)
        if deliverable_contracts:
            for contract in deliverable_contracts:
                d_name = contract.get("name", "")
                cats = contract.get("required_info_categories", [])
                t_kws = contract.get("topic_keywords", [])
                topic_core = " ".join(t_kws[:2]) if t_kws else " ".join(keywords[:2])
                # Direct deliverable query
                queries.append(f"{primary_entity} {d_name}".strip())
                # Category-targeted queries
                for cat in cats[:2]:
                    cat_readable = " ".join(cat.split("_"))
                    queries.append(f"{primary_entity} {topic_core} {cat_readable}".strip())

        facets = [
            "official data statistics",
            "risks challenges",
            "plan policy recommendations",
            "funding costs",
            "report pdf",
        ]
        core_topic = " ".join(keywords[:3])
        for facet in facets:
            q = " ".join(p for p in (primary_entity, core_topic, facet) if p).strip()
            queries.append(q)
        # Additional entity-specific queries
        for ent in entities[1:4]:
            queries.append(" ".join(p for p in (ent, core_topic) if p))
        # Keyword-pair queries to broaden coverage while staying on-topic
        for i in range(3, min(len(keywords), 9), 2):
            pair = " ".join(keywords[i:i + 2])
            queries.append(" ".join(p for p in (primary_entity, pair) if p))

        cleaned = []
        for q in queries:
            seen_tokens: List[str] = []
            for tok in re.sub(r"\s+", " ", q).strip().split(" "):
                if tok and tok.lower() not in [t.lower() for t in seen_tokens]:
                    seen_tokens.append(tok)
            q = " ".join(seen_tokens)
            if q and len(q.split()) >= 2 and q not in cleaned:
                cleaned.append(q)
        return cleaned
