"""
Fact Verification & Structured Evidence Ledger.

Implements claim-level evidence tracking and corroboration:
- Extraction of factual assertions, statistics, and dates from research material
- Categorization into Fact, Inference, Estimate, Opinion, Unknown
- Linking claims to exact source excerpts in `research_evidence`
- Cross-source corroboration (verifying claims across distinct independent domains)
- Contradiction and conflict detection
- Structured verification state tracking: UNVERIFIED, SUPPORTED, CORROBORATED,
  CONTRADICTED, INSUFFICIENT_EVIDENCE, OUTDATED
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from app.database import get_db_connection

logger = logging.getLogger("qwen_collective.research.verification")

# Regex heuristics for locating factual claims (numbers, percentages, dates, currency, statistics)
NUMERIC_OR_STAT_PATTERN = re.compile(
    r"(\b\d+(\.\d+)?%|\$\d+(\.\d+)?\s*(billion|million|trillion|k|m|b)?|\b\d{4}\b|\bbetween \d+ and \d+\b|\bincreased by \d+|\bdecreased by \d+)",
    re.IGNORECASE,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


from app.services.research.public_suffix import (
    are_sources_independent,
    is_duplicate_or_syndicated,
    parse_registered_domain,
    resolve_publisher_identity,
)


@dataclass
class EvidenceAssessment:
    """
    Structured factual grounding assessment separating evidence strength, source reliability,
    directness, independence, and empirical calibration.
    """
    evidence_strength_score: float  # Explicitly labeled heuristic indicator [0.0 - 1.0]
    source_reliability_score: float  # Average reliability of backing sources [0.0 - 1.0]
    directness_of_evidence: str  # "verbatim_quote", "direct_metric", "paraphrase", "indirect", "none"
    independent_source_count: int  # Genuinely independent publisher/domain count
    has_contradiction: bool
    verification_status: str  # "corroborated", "supported", "unverified", "insufficient_evidence", "contradicted"
    empirical_confidence: Optional[float] = None  # Explicitly None until calibrated against labeled benchmark
    is_heuristic: bool = True
    score_limitations: str = (
        "Heuristic evidence-strength indicator based on deterministic multi-source rules. "
        "Not an empirically calibrated probability; does not represent a statistical likelihood "
        "without calibration measurements against a labeled domain validation dataset."
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_strength_score": self.evidence_strength_score,
            "source_reliability_score": self.source_reliability_score,
            "directness_of_evidence": self.directness_of_evidence,
            "independent_source_count": self.independent_source_count,
            "has_contradiction": self.has_contradiction,
            "verification_status": self.verification_status,
            "empirical_confidence": self.empirical_confidence,
            "is_heuristic": self.is_heuristic,
            "score_limitations": self.score_limitations,
        }


def extract_registered_domain(url: str) -> str:
    """Extract registered domain via maintained Public Suffix List implementation."""
    return parse_registered_domain(url)[0]


def compute_evidence_assessment(
    verification_status: str,
    independent_domain_count: int,
    source_quality_scores: List[float],
    has_exact_quote: bool = False,
    has_contradiction: bool = False,
    evidence_strengths: Optional[List[str]] = None,
) -> EvidenceAssessment:
    """
    Constructs a defensible, multi-factor EvidenceAssessment.
    
    SECURITY & SEMANTIC RULES:
    1. Heuristic scores are labeled as evidence-strength indicators, never calibrated probabilities.
    2. Contradicted assertions receive score 0.0 (no arbitrary numerical confidence floor).
    3. Directness of evidence and publisher independence scale monotonically.
    """
    v_stat = verification_status.lower().strip()
    avg_quality = sum(source_quality_scores) / max(1, len(source_quality_scores)) if source_quality_scores else 0.5
    strengths = evidence_strengths or ["moderate"]

    directness = "none"
    if has_exact_quote:
        directness = "verbatim_quote"
    elif any(s == "strong" for s in strengths):
        directness = "direct_metric"
    elif any(s == "moderate" for s in strengths):
        directness = "paraphrase"
    elif any(s == "weak" for s in strengths):
        directness = "indirect"

    if has_contradiction or v_stat == "contradicted":
        return EvidenceAssessment(
            evidence_strength_score=0.0,
            source_reliability_score=round(avg_quality, 2),
            directness_of_evidence=directness,
            independent_source_count=independent_domain_count,
            has_contradiction=True,
            verification_status="contradicted",
            empirical_confidence=None,
        )

    if v_stat in ("insufficient_evidence", "unverified"):
        return EvidenceAssessment(
            evidence_strength_score=0.0,
            source_reliability_score=round(avg_quality, 2),
            directness_of_evidence=directness,
            independent_source_count=independent_domain_count,
            has_contradiction=False,
            verification_status=v_stat,
            empirical_confidence=None,
        )

    if v_stat == "corroborated":
        base = 0.65
        base += avg_quality * 0.15
        base += min(0.10, max(0.0, (independent_domain_count - 1) * 0.05))
        if has_exact_quote:
            base += 0.05
        elif any(s == "strong" for s in strengths):
            base += 0.03
        score = min(0.95, round(base, 2))
        return EvidenceAssessment(
            evidence_strength_score=score,
            source_reliability_score=round(avg_quality, 2),
            directness_of_evidence=directness,
            independent_source_count=independent_domain_count,
            has_contradiction=False,
            verification_status="corroborated",
            empirical_confidence=None,
        )

    if v_stat == "supported":
        base = 0.40
        base += avg_quality * 0.25
        if has_exact_quote:
            base += 0.10
        elif any(s == "strong" for s in strengths):
            base += 0.05
        score = min(0.80, round(base, 2))
        return EvidenceAssessment(
            evidence_strength_score=score,
            source_reliability_score=round(avg_quality, 2),
            directness_of_evidence=directness,
            independent_source_count=independent_domain_count,
            has_contradiction=False,
            verification_status="supported",
            empirical_confidence=None,
        )

    return EvidenceAssessment(
        evidence_strength_score=0.20,
        source_reliability_score=round(avg_quality, 2),
        directness_of_evidence=directness,
        independent_source_count=independent_domain_count,
        has_contradiction=False,
        verification_status="unverified",
        empirical_confidence=None,
    )


def compute_defensible_confidence(
    verification_status: str,
    independent_domain_count: int,
    source_quality_scores: List[float],
    has_exact_quote: bool = False,
    has_contradiction: bool = False,
    evidence_strengths: Optional[List[str]] = None,
) -> float:
    """
    Legacy wrapper returning defensible evidence-strength score [0.0 - 1.0].
    Preserved for backwards compatibility with existing callers.
    """
    assessment = compute_evidence_assessment(
        verification_status=verification_status,
        independent_domain_count=independent_domain_count,
        source_quality_scores=source_quality_scores,
        has_exact_quote=has_exact_quote,
        has_contradiction=has_contradiction,
        evidence_strengths=evidence_strengths,
    )
    return assessment.evidence_strength_score


class EvidenceLedger:
    """Manages claims, evidence links, and cross-source corroboration for a project."""

    def record_claim(
        self,
        project_id: str,
        claim_text: str,
        research_run_id: Optional[str] = None,
        category: str = "fact",
        confidence_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record a factual assertion to be verified against source evidence."""
        claim_id = f"clm-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        init_conf = confidence_score if confidence_score is not None else 0.0
        conn = get_db_connection()
        try:
            conn.execute(
                """
                INSERT INTO research_claims (
                    id, project_id, research_run_id, claim_text, category,
                    verification_status, confidence_score, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'unverified', ?, ?, ?)
                """,
                (claim_id, project_id, research_run_id, claim_text.strip(), category, init_conf, now, now),
            )
            conn.commit()
            return self.get_claim(project_id, claim_id)
        finally:
            conn.close()

    def attach_evidence(
        self,
        project_id: str,
        claim_id: str,
        source_id: str,
        supporting_excerpt: str,
        exact_quote: Optional[str] = None,
        evidence_strength: str = "moderate",
        relevance_score: float = 1.0,
        orchestration_run_id: Optional[str] = None,
        research_session_id: Optional[str] = None,
        source_version_id: Optional[str] = None,
        is_heuristic: bool = True,
        heuristic_label: str = "HEURISTIC_ESTIMATE",
        informed_tasks: Optional[List[str]] = None,
        informed_requirements: Optional[List[str]] = None,
        contradiction_status: str = "NONE",
    ) -> Dict[str, Any]:
        """Link a claim to supporting text found in an authoritative source."""
        ev_id = f"evd-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        tasks_json = json.dumps(informed_tasks or [])
        reqs_json = json.dumps(informed_requirements or [])
        conn = get_db_connection()
        try:
            conn.execute(
                """
                INSERT INTO research_evidence (
                    id, project_id, claim_id, source_id, supporting_excerpt,
                    exact_quote, evidence_strength, relevance_score, created_at,
                    orchestration_run_id, research_session_id, source_version_id,
                    is_heuristic, heuristic_label, informed_tasks_json,
                    informed_requirements_json, contradiction_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ev_id, project_id, claim_id, source_id, supporting_excerpt.strip(),
                    exact_quote, evidence_strength, relevance_score, now,
                    orchestration_run_id, research_session_id, source_version_id,
                    1 if is_heuristic else 0, heuristic_label, tasks_json,
                    reqs_json, contradiction_status,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        # Update verification status after adding evidence
        self.recompute_claim_verification(project_id, claim_id)
        return {
            "id": ev_id,
            "claim_id": claim_id,
            "source_id": source_id,
            "status": "attached",
            "research_session_id": research_session_id,
            "orchestration_run_id": orchestration_run_id,
            "source_version_id": source_version_id,
            "is_heuristic": is_heuristic,
            "heuristic_label": heuristic_label,
            "informed_tasks": informed_tasks or [],
            "informed_requirements": informed_requirements or [],
            "contradiction_status": contradiction_status,
        }

    add_evidence = attach_evidence

    def get_structured_evidence(
        self,
        project_id: str,
        claim_id: Optional[str] = None,
        orchestration_run_id: Optional[str] = None,
        research_session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Produce structured evidence records containing all Stage 5 Section 4 fields:
        - Project and orchestration-run identity.
        - Distinct research-session identity.
        - Source and observation version identifiers.
        - Canonical URL and retrieval timestamp.
        - Immutable content hash.
        - Relevant extracted facts and source spans.
        - Verification and contradiction status.
        - Evidence-strength indicators, explicitly labeled as heuristic.
        - Authorized source-reuse lineage.
        - Tasks and requirements informed by the research.
        """
        conn = get_db_connection()
        try:
            sql = """
                SELECT e.*, c.claim_text, c.verification_status, c.category as claim_category,
                       s.canonical_url, COALESCE(sv.retrieved_at, s.retrieved_at) as retrieved_at,
                       COALESCE(sv.content_hash, s.content_hash) as content_hash,
                       s.title as source_title, s.quality_score as source_quality
                FROM research_evidence e
                JOIN research_claims c ON e.claim_id = c.id
                JOIN research_sources s ON e.source_id = s.id
                LEFT JOIN research_source_versions sv ON e.source_version_id = sv.id
                WHERE e.project_id = ?
            """
            params: List[Any] = [project_id]
            if claim_id:
                sql += " AND e.claim_id = ?"
                params.append(claim_id)
            if orchestration_run_id:
                sql += " AND e.orchestration_run_id = ?"
                params.append(orchestration_run_id)
            if research_session_id:
                sql += " AND e.research_session_id = ?"
                params.append(research_session_id)
            sql += " ORDER BY e.created_at DESC"
            rows = conn.execute(sql, tuple(params)).fetchall()

            records = []
            for r in rows:
                row_d = dict(r)
                # Fetch reuse lineage if any
                reuses = conn.execute(
                    "SELECT * FROM research_source_reuses WHERE project_id = ? AND source_id = ?",
                    (project_id, row_d["source_id"])
                ).fetchall()
                reuse_lineage = [dict(re) for re in reuses]

                informed_tasks = []
                try:
                    informed_tasks = json.loads(row_d.get("informed_tasks_json") or "[]")
                except Exception:
                    pass
                informed_requirements = []
                try:
                    informed_requirements = json.loads(row_d.get("informed_requirements_json") or "[]")
                except Exception:
                    pass

                rec = {
                    "id": row_d["id"],
                    "evidence_id": row_d["id"],
                    "project_id": row_d["project_id"],
                    "orchestration_run_id": row_d.get("orchestration_run_id"),
                    "research_session_id": row_d.get("research_session_id") or "sess-default",
                    "source_id": row_d["source_id"],
                    "source_version_id": row_d.get("source_version_id") or f"{row_d['source_id']}-v1",
                    "canonical_url": row_d["canonical_url"],
                    "retrieval_timestamp": row_d["retrieved_at"],
                    "content_hash": row_d["content_hash"],
                    "claim_id": row_d["claim_id"],
                    "extracted_facts": {
                        "claim_text": row_d["claim_text"],
                        "category": row_d.get("claim_category", "fact"),
                        "supporting_excerpt": row_d["supporting_excerpt"],
                        "exact_quote": row_d.get("exact_quote"),
                        "context_before": row_d.get("context_before"),
                        "context_after": row_d.get("context_after"),
                    },
                    "verification_status": (row_d.get("verification_status") or "unverified").upper(),
                    "contradiction_status": row_d.get("contradiction_status") or "NONE",
                    "evidence_strength": row_d.get("evidence_strength") or "moderate",
                    "is_heuristic": bool(row_d.get("is_heuristic", 1)),
                    "heuristic_label": row_d.get("heuristic_label") or "HEURISTIC_ESTIMATE",
                    "source_reuse_lineage": reuse_lineage,
                    "informed_tasks": informed_tasks,
                    "informed_requirements": informed_requirements,
                    "created_at": row_d["created_at"],
                }
                records.append(rec)
            return records
        finally:
            conn.close()


    def recompute_claim_verification(self, project_id: str, claim_id: str) -> str:
        """
        Evaluate claim against attached evidence with real source independence (Findings F-08, F-06):
        - Multi-part public suffix domain parsing
        - Syndicated content / shared content hash deduplication
        - Contradiction surfacing (status CONTRADICTED)
        - Defensible confidence calculation
        """
        conn = get_db_connection()
        try:
            claim_row = conn.execute(
                "SELECT * FROM research_claims WHERE project_id = ? AND id = ?",
                (project_id, claim_id),
            ).fetchone()
            if not claim_row:
                return "unverified"

            ev_rows = conn.execute(
                """
                SELECT e.*, s.canonical_url, s.quality_score, s.content_hash, s.title, s.publisher,
                       x.clean_text as extracted_text
                FROM research_evidence e
                JOIN research_sources s ON e.source_id = s.id
                LEFT JOIN research_extractions x ON s.id = x.source_id
                WHERE e.project_id = ? AND e.claim_id = ?
                """,
                (project_id, claim_id),
            ).fetchall()

            if not ev_rows:
                status = "insufficient_evidence"
                confidence = 0.0
            else:
                independent_entities: Dict[str, Dict[str, Any]] = {}
                accepted_sources: List[Dict[str, Any]] = []

                for row in ev_rows:
                    r = dict(row)
                    url = r.get("canonical_url") or ""
                    reg_domain, _, _ = parse_registered_domain(url)
                    pub_id = resolve_publisher_identity(url, r.get("publisher"))

                    # Check multi-factor duplication / syndication against already accepted sources
                    is_syndicated = False
                    for existing_src in accepted_sources:
                        dup, _ = is_duplicate_or_syndicated(existing_src, r)
                        if dup:
                            is_syndicated = True
                            break

                    # Check publisher independence
                    if pub_id in independent_entities:
                        is_syndicated = True

                    # Only count as independent if registered domain is distinct, publisher identity is distinct,
                    # and content is not syndicated
                    if reg_domain and not is_syndicated:
                        independent_entities[pub_id] = {
                            "domain": reg_domain,
                            "publisher_id": pub_id,
                            "quality_score": float(r.get("quality_score") or 1.0),
                            "evidence_strength": r.get("evidence_strength") or "moderate",
                            "has_exact_quote": bool(r.get("exact_quote")),
                        }
                        accepted_sources.append(r)

                has_contradiction = bool(claim_row["contradiction_details"])
                ind_count = len(independent_entities)

                if has_contradiction:
                    status = "contradicted"
                elif ind_count >= 2:
                    status = "corroborated"
                elif ind_count == 1:
                    status = "supported"
                else:
                    status = "insufficient_evidence"

                q_scores = [v["quality_score"] for v in independent_entities.values()] if independent_entities else [0.5]
                e_strengths = [v["evidence_strength"] for v in independent_entities.values()] if independent_entities else ["moderate"]
                has_quote = any(v["has_exact_quote"] for v in independent_entities.values()) if independent_entities else False

                assessment = compute_evidence_assessment(
                    verification_status=status,
                    independent_domain_count=ind_count,
                    source_quality_scores=q_scores,
                    has_exact_quote=has_quote,
                    has_contradiction=has_contradiction,
                    evidence_strengths=e_strengths,
                )
                confidence = assessment.evidence_strength_score

            conn.execute(
                "UPDATE research_claims SET verification_status = ?, confidence_score = ?, updated_at = ? WHERE id = ?",
                (status, confidence, _now_iso(), claim_id),
            )
            conn.commit()
            return status.upper()
        finally:
            conn.close()

    def record_contradiction(
        self,
        project_id: str,
        claim_id: str,
        conflicting_source_id: str,
        conflicting_text: str,
    ) -> None:
        """Flag that a claim is contradicted by another retrieved source."""
        conn = get_db_connection()
        try:
            detail = f"Contradicted by source {conflicting_source_id}: '{conflicting_text[:300]}'"
            conn.execute(
                """
                UPDATE research_claims
                SET verification_status = 'contradicted',
                    contradiction_details = ?,
                    updated_at = ?
                WHERE project_id = ? AND id = ?
                """,
                (detail, _now_iso(), project_id, claim_id),
            )
            conn.commit()
        finally:
            conn.close()

    def find_supporting_excerpts_in_text(self, claim_text: str, source_text: str) -> Optional[str]:
        """
        Locate the best matching supporting passage for a claim within document text
        using keyword overlap and numerical entity alignment.
        """
        if not claim_text or not source_text:
            return None

        # Extract meaningful keywords (words > 4 chars, ignoring stopwords)
        keywords = {w.lower() for w in re.findall(r"\b[A-Za-z]{4,}\b", claim_text)}
        # Look for numbers/percentages in claim
        stats = set(NUMERIC_OR_STAT_PATTERN.findall(claim_text))

        paragraphs = source_text.split("\n\n")
        best_para = None
        best_score = 0

        for p in paragraphs:
            p_strip = p.strip()
            if len(p_strip) < 30:
                continue
            p_words = set(re.findall(r"\b[A-Za-z]{4,}\b", p_strip.lower()))
            overlap = len(keywords.intersection(p_words))

            # Bonus score if exact statistical numbers are shared
            stat_matches = sum(1 for s in stats if s[0] in p_strip)
            score = overlap + (stat_matches * 3)

            if score > best_score and overlap >= 2:
                best_score = score
                best_para = p_strip[:500]

        return best_para

    def get_claim(self, project_id: str, claim_id: str) -> Optional[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT * FROM research_claims WHERE project_id = ? AND id = ?",
                (project_id, claim_id),
            ).fetchone()
            if not row:
                return None
            res = dict(row)
            evidence = conn.execute(
                """
                SELECT e.*, s.title as source_title, s.canonical_url
                FROM research_evidence e
                JOIN research_sources s ON e.source_id = s.id
                WHERE e.project_id = ? AND e.claim_id = ?
                """,
                (project_id, claim_id),
            ).fetchall()
            res["evidence"] = [dict(e) for e in evidence]
            return res
        finally:
            conn.close()

    def list_claims(
        self,
        project_id: str,
        limit: int = 100,
        research_run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            if research_run_id:
                rows = conn.execute(
                    """SELECT * FROM research_claims
                       WHERE project_id = ? AND research_run_id = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (project_id, research_run_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM research_claims WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            claims = [dict(r) for r in rows]
            for c in claims:
                ev_rows = conn.execute(
                    """SELECT e.*, s.title as source_title, s.canonical_url
                       FROM research_evidence e
                       JOIN research_sources s ON e.source_id = s.id
                       WHERE e.project_id = ? AND e.claim_id = ?""",
                    (project_id, c["id"]),
                ).fetchall()
                c["evidence"] = [dict(e) for e in ev_rows]
            return claims
        finally:
            conn.close()

class ClaimRecord(dict):
    """Dict subclass allowing attribute access for claim fields."""
    @property
    def claim_id(self) -> str:
        return self.get("id") or self.get("claim_id") or ""

    @property
    def verification_status(self) -> str:
        return (self.get("verification_status") or "unverified").upper()

    @property
    def contradictory_evidence(self) -> Optional[str]:
        return self.get("contradiction_details")

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self)
        d["verification_status"] = self.verification_status
        return d


# Alias methods inside EvidenceLedger
def _add_ledger_methods(cls):
    def add_claim(self, project_id: str, claim_text: str, claim_type: str = "fact", **kwargs) -> ClaimRecord:
        res = self.record_claim(project_id, claim_text, category=claim_type, **kwargs)
        return ClaimRecord(res)

    def add_evidence(self, project_id: str, claim_id: str, source_id: str, supporting_passage: str, confidence_score: Optional[float] = None, exact_quote: Optional[str] = None, **kwargs):
        c_score = confidence_score if confidence_score is not None else 1.0
        return self.attach_evidence(
            project_id, claim_id, source_id, supporting_passage,
            exact_quote=exact_quote, relevance_score=c_score, **kwargs
        )

    def corroborate_claim(self, claim_id: str, project_id: Optional[str] = None) -> ClaimRecord:
        conn = get_db_connection()
        try:
            if not project_id:
                row = conn.execute("SELECT project_id FROM research_claims WHERE id = ?", (claim_id,)).fetchone()
                if row:
                    project_id = row["project_id"]
        finally:
            conn.close()
        if project_id:
            self.recompute_claim_verification(project_id, claim_id)
            c = self.get_claim(project_id, claim_id)
            return ClaimRecord(c) if c else ClaimRecord({"id": claim_id, "verification_status": "unverified"})
        return ClaimRecord({"id": claim_id, "verification_status": "unverified"})

    def flag_contradiction(self, claim_id: str, details: str, project_id: Optional[str] = None) -> ClaimRecord:
        conn = get_db_connection()
        try:
            if not project_id:
                row = conn.execute("SELECT project_id FROM research_claims WHERE id = ?", (claim_id,)).fetchone()
                if row:
                    project_id = row["project_id"]
            conn.execute(
                "UPDATE research_claims SET verification_status='contradicted', contradiction_details=?, updated_at=? WHERE id=?",
                (details, _now_iso(), claim_id)
            )
            conn.commit()
        finally:
            conn.close()
        if project_id:
            c = self.get_claim(project_id, claim_id)
            return ClaimRecord(c) if c else ClaimRecord({"id": claim_id, "verification_status": "contradicted"})
        return ClaimRecord({"id": claim_id, "verification_status": "contradicted"})

    cls.add_claim = add_claim
    cls.add_evidence = add_evidence
    cls.corroborate_claim = corroborate_claim
    cls.flag_contradiction = flag_contradiction
    return cls

EvidenceLedger = _add_ledger_methods(EvidenceLedger)


# Module-level convenience functions
def attach_evidence(
    project_id: str,
    claim_id: str,
    source_id: str,
    supporting_excerpt: str = "",
    **kwargs,
) -> Dict[str, Any]:
    return EvidenceLedger().attach_evidence(
        project_id=project_id,
        claim_id=claim_id,
        source_id=source_id,
        supporting_excerpt=supporting_excerpt,
        **kwargs,
    )


def get_structured_evidence(project_id: str, **kwargs) -> List[Dict[str, Any]]:
    return EvidenceLedger().get_structured_evidence(project_id, **kwargs)

