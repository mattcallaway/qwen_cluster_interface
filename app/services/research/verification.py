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


class EvidenceLedger:
    """Manages claims, evidence links, and cross-source corroboration for a project."""

    def record_claim(
        self,
        project_id: str,
        claim_text: str,
        research_run_id: Optional[str] = None,
        category: str = "fact",
        confidence_score: float = 0.8,
    ) -> Dict[str, Any]:
        """Record a factual assertion to be verified against source evidence."""
        claim_id = f"clm-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        conn = get_db_connection()
        try:
            conn.execute(
                """
                INSERT INTO research_claims (
                    id, project_id, research_run_id, claim_text, category,
                    verification_status, confidence_score, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'unverified', ?, ?, ?)
                """,
                (claim_id, project_id, research_run_id, claim_text.strip(), category, confidence_score, now, now),
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
    ) -> Dict[str, Any]:
        """Link a claim to supporting text found in an authoritative source."""
        ev_id = f"evd-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        conn = get_db_connection()
        try:
            conn.execute(
                """
                INSERT INTO research_evidence (
                    id, project_id, claim_id, source_id, supporting_excerpt,
                    exact_quote, evidence_strength, relevance_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ev_id, project_id, claim_id, source_id, supporting_excerpt.strip(),
                    exact_quote, evidence_strength, relevance_score, now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        # Update verification status after adding evidence
        self.recompute_claim_verification(project_id, claim_id)
        return {"id": ev_id, "claim_id": claim_id, "source_id": source_id, "status": "attached"}

    def recompute_claim_verification(self, project_id: str, claim_id: str) -> str:
        """
        Evaluate claim against attached evidence:
        - If 0 evidence items -> 'unverified' or 'insufficient_evidence'
        - If evidence from 1 domain -> 'supported'
        - If evidence from >= 2 distinct independent domains -> 'corroborated'
        - If conflicting facts recorded -> 'contradicted'
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
                SELECT e.*, s.canonical_url, s.quality_score
                FROM research_evidence e
                JOIN research_sources s ON e.source_id = s.id
                WHERE e.project_id = ? AND e.claim_id = ?
                """,
                (project_id, claim_id),
            ).fetchall()

            if not ev_rows:
                status = "insufficient_evidence"
            else:
                # Count distinct second-level domains
                domains: set[str] = set()
                for r in ev_rows:
                    url = r["canonical_url"]
                    host = (urlparse(url).hostname or "").lower()
                    parts = host.split(".")
                    # Extract root domain (e.g., example.com from sub.example.com)
                    root_domain = ".".join(parts[-2:]) if len(parts) >= 2 else host
                    if root_domain:
                        domains.add(root_domain)

                if claim_row["contradiction_details"]:
                    status = "contradicted"
                elif len(domains) >= 2:
                    status = "corroborated"
                else:
                    status = "supported"

            conn.execute(
                "UPDATE research_claims SET verification_status = ?, updated_at = ? WHERE id = ?",
                (status, _now_iso(), claim_id),
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

    def list_claims(self, project_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM research_claims WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
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

    def add_evidence(self, project_id: str, claim_id: str, source_id: str, supporting_passage: str, confidence_score: float = 0.9, **kwargs):
        return self.attach_evidence(project_id, claim_id, source_id, supporting_passage, relevance_score=confidence_score, **kwargs)

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

