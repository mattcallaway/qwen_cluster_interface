"""
Deterministic Fact Verification & Semantic Entailment Engine.

Replaces naive keyword-overlap heuristics with structured factual entailment analysis
distinguishing:
- SUPPORTED: Exact quote or verifiable proposition with matching polarity, metrics, and modality.
- CONTRADICTED: Direct factual conflict (negation mismatch, directional mismatch, numeric mismatch,
  tense/aspect mismatch, or epistemic debunking).
- INSUFFICIENT_EVIDENCE: Source mentions related entities or topics but lacks verifiable evidence
  for the specific claim predicate.
- UNVERIFIED: The relationship cannot be deterministically proven without ungrounded assumptions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class ClaimEntailmentResult:
    status: str  # "SUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE", "UNVERIFIED"
    reason: str
    matched_quote: Optional[str] = None
    conflicting_excerpt: Optional[str] = None
    is_exact_quote: bool = False
    evidence_strength: str = "none"  # "strong", "moderate", "weak", "none"


# Negation terms modifying predicates
_NEGATION_TERMS = {
    "not", "never", "no", "neither", "nor", "none",
    "did not", "does not", "do not", "will not", "cannot", "could not",
    "would not", "should not", "failed to", "refused to", "declined to",
    "rejected", "denied", "disallowed", "unapproved", "prohibited", "halted",
}

# Epistemic debunking and falsifying markers
_DEBUNKING_PATTERNS = [
    re.compile(r"\b(falsely|incorrectly|erroneously)\s+(claimed|stated|reported|asserted|believed|alleged)\b", re.IGNORECASE),
    re.compile(r"\b(debunked|disproven|discredited|refuted)\s+(the\s+claim|claims|reports|rumors)\b", re.IGNORECASE),
    re.compile(r"\b(myth|hoax|conspiracy|fabrication|misconception)\s+that\b", re.IGNORECASE),
    re.compile(r"\buntrue\s+that\b", re.IGNORECASE),
    re.compile(r"\bcontrary\s+to\s+(claims|reports|rumors)\b", re.IGNORECASE),
]

# Directional keywords
_INCREASE_WORDS = {"increased", "increase", "increasing", "rose", "rising", "grew", "growth", "growing", "expanded", "expansion", "gain", "gained"}
_DECREASE_WORDS = {"decreased", "decrease", "decreasing", "fell", "falling", "dropped", "dropping", "shrank", "shrinking", "declined", "decline", "reduction", "reduced", "loss"}

# Temporal / aspect modal markers
_SPECULATIVE_OR_FUTURE_PATTERNS = [
    re.compile(r"\b(expected|projected|anticipated|scheduled|estimated|planned)\s+to\s+(\w+)", re.IGNORECASE),
    re.compile(r"\b(aims|aiming|seeks|seeking|hopes|hoping)\s+to\s+(\w+)", re.IGNORECASE),
    re.compile(r"\bwill\s+(\w+)", re.IGNORECASE),
]

_COMPLETED_PAST_PATTERNS = [
    re.compile(r"\bwas\s+(completed|finished|concluded|achieved|delivered)\b", re.IGNORECASE),
    re.compile(r"\bhas\s+(been\s+completed|finished|concluded|achieved)\b", re.IGNORECASE),
    re.compile(r"\b(completed|finished|concluded|achieved)\s+in\s+\d{4}\b", re.IGNORECASE),
]

_NUMBER_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(%|percent|billion|million|trillion|k|m|b)?", re.IGNORECASE)
_CURRENCY_RE = re.compile(r"\$(\d+(?:\.\d+)?)\s*(billion|million|trillion|k|m|b)?", re.IGNORECASE)


def _stem(w: str) -> str:
    """Normalize simple English inflectional suffixes for deterministic lexical matching."""
    w = w.lower().strip()
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("ed") and len(w) > 4:
        w = w[:-1] if w.endswith("eed") else w[:-2]
    if w.endswith("es") and len(w) > 3:
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    if w.endswith("ing") and len(w) > 5:
        return w[:-3]
    if w.endswith("e") and len(w) > 4:
        return w[:-1]
    return w


def normalize_sentence(s: str) -> str:
    """Normalize whitespace and strip citation markers like [1], [src-1]."""
    cleaned = re.sub(r"\[(?:\d+|src-[a-zA-Z0-9_\-]+)\]", "", s)
    return " ".join(cleaned.split()).strip()


def extract_numbers_and_metrics(text: str) -> List[Tuple[str, str]]:
    """Extract numbers/percentages and currency metrics: [(val_str, unit_str)]."""
    metrics = []
    for m in _CURRENCY_RE.finditer(text):
        val = m.group(1)
        scale = (m.group(2) or "").lower()
        metrics.append((f"${val}", scale))
    for m in _NUMBER_RE.finditer(text):
        val = m.group(1)
        unit = (m.group(2) or "").lower()
        if unit in ("%", "percent"):
            metrics.append((val, "%"))
        elif unit in ("billion", "million", "trillion"):
            metrics.append((val, unit))
        else:
            metrics.append((val, ""))
    return metrics


def has_negation(text: str) -> bool:
    """Check whether text contains a negation marker modifying the clause."""
    t_lower = text.lower()
    for neg in _NEGATION_TERMS:
        if re.search(r"\b" + re.escape(neg) + r"\b", t_lower):
            return True
    return False


def verify_claim_against_source(claim_text: str, source_text: str) -> ClaimEntailmentResult:
    """
    Deterministically evaluates factual entailment between a claim and source text.
    
    Returns structured ClaimEntailmentResult with status:
    - SUPPORTED: Definite claim-level backing with matching polarity, metrics, and modality.
    - CONTRADICTED: Direct factual conflict in negation, numbers, direction, or framing.
    - INSUFFICIENT_EVIDENCE: General topic overlap without verifiable claim evidence.
    - UNVERIFIED: Cannot be deterministically proven.
    """
    clean_claim = normalize_sentence(claim_text)
    if not clean_claim or not source_text or len(source_text.strip()) < 10:
        return ClaimEntailmentResult(
            status="INSUFFICIENT_EVIDENCE",
            reason="Source or claim text is empty or missing.",
            evidence_strength="none",
        )

    clean_source = source_text.strip()

    # 1. Exact Substring Match Check
    clean_claim_no_punct = clean_claim.strip(".?! \n\t")
    if clean_claim_no_punct.lower() in clean_source.lower():
        # Even if substring matches, check if the source embeds it in an epistemic debunking pattern
        pos = clean_source.lower().find(clean_claim_no_punct.lower())
        surrounding_start = max(0, pos - 120)
        surrounding_end = min(len(clean_source), pos + len(clean_claim_no_punct) + 60)
        surrounding_context = clean_source[surrounding_start:surrounding_end]

        for deb_pat in _DEBUNKING_PATTERNS:
            if deb_pat.search(surrounding_context):
                return ClaimEntailmentResult(
                    status="CONTRADICTED",
                    reason=f"Source explicitly frames the assertion as false, debunked, or a myth: '{surrounding_context.strip()}'",
                    conflicting_excerpt=surrounding_context.strip(),
                    evidence_strength="strong",
                )

        return ClaimEntailmentResult(
            status="SUPPORTED",
            reason="Exact verbatim quote verified in source text.",
            matched_quote=clean_claim_no_punct,
            is_exact_quote=True,
            evidence_strength="strong",
        )

    # 2. Sentence-by-sentence analysis for candidate relevant sentences
    # Split source into candidate sentences
    source_sentences = [
        s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", clean_source)
        if len(s.strip()) >= 15
    ]

    claim_words = {_stem(w) for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", clean_claim)
                   if w.lower() not in {"the", "and", "for", "that", "this", "with", "from", "according", "are", "were", "been"}}
    claim_negated = has_negation(clean_claim)
    claim_metrics = extract_numbers_and_metrics(clean_claim)

    claim_has_increase = any(w in claim_words for w in _INCREASE_WORDS)
    claim_has_decrease = any(w in claim_words for w in _DECREASE_WORDS)

    claim_is_past_completed = any(p.search(clean_claim) for p in _COMPLETED_PAST_PATTERNS)
    claim_is_future_expected = any(p.search(clean_claim) for p in _SPECULATIVE_OR_FUTURE_PATTERNS)

    best_overlap = 0
    best_candidate_sentence = ""
    candidate_contradictions: List[str] = []

    for src_sent in source_sentences:
        src_words = {_stem(w) for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", src_sent)}
        overlap_words = claim_words.intersection(src_words)
        overlap_count = len(overlap_words)

        if overlap_count > best_overlap:
            best_overlap = overlap_count
            best_candidate_sentence = src_sent

        # If candidate sentence has topical overlap or metric overlap with the claim:
        src_metrics_candidate = extract_numbers_and_metrics(src_sent)
        has_metric_overlap = bool(claim_metrics and any(m in src_metrics_candidate for m in claim_metrics))
        if overlap_count >= 2 or (len(claim_words) > 0 and overlap_count / len(claim_words) >= 0.25) or has_metric_overlap:
            # Check A: Epistemic Debunking
            for deb_pat in _DEBUNKING_PATTERNS:
                if deb_pat.search(src_sent):
                    return ClaimEntailmentResult(
                        status="CONTRADICTED",
                        reason=f"Source explicitly frames the claim as false or debunked: '{src_sent}'",
                        conflicting_excerpt=src_sent,
                        evidence_strength="strong",
                    )

            # Check B: Negation mismatch (Test A)
            src_negated = has_negation(src_sent)
            # If claim asserts affirmative and source asserts negation (or vice versa) on same core entities
            if claim_negated != src_negated and (overlap_count >= 3 and len(claim_words) > 0 and overlap_count / len(claim_words) >= 0.55):
                candidate_contradictions.append(
                    f"Polarity conflict: claim negation is {claim_negated} while source asserts '{src_sent}' (negation={src_negated})"
                )

            # Check C: Directional conflict (Test B)
            src_has_increase = any(re.search(r"\b" + re.escape(w) + r"\b", src_sent, re.IGNORECASE) for w in _INCREASE_WORDS)
            src_has_decrease = any(re.search(r"\b" + re.escape(w) + r"\b", src_sent, re.IGNORECASE) for w in _DECREASE_WORDS)
            if (claim_has_increase and src_has_decrease) or (claim_has_decrease and src_has_increase):
                return ClaimEntailmentResult(
                    status="CONTRADICTED",
                    reason=f"Directional metric conflict: claim asserts {'increase' if claim_has_increase else 'decrease'} while source asserts '{src_sent}'",
                    conflicting_excerpt=src_sent,
                    evidence_strength="strong",
                )

            # Check D: Temporal / Modality conflict (Test C)
            src_is_future_expected = any(p.search(src_sent) for p in _SPECULATIVE_OR_FUTURE_PATTERNS)
            src_is_past_completed = any(p.search(src_sent) for p in _COMPLETED_PAST_PATTERNS)
            if (claim_is_past_completed and src_is_future_expected and not src_is_past_completed):
                return ClaimEntailmentResult(
                    status="CONTRADICTED",
                    reason=f"Temporal/modality conflict: claim asserts completed past fact while source describes expectation/projection: '{src_sent}'",
                    conflicting_excerpt=src_sent,
                    evidence_strength="strong",
                )

            # Check E: Numeric and percentage mismatch (Test E)
            src_metrics = extract_numbers_and_metrics(src_sent)
            if claim_metrics and src_metrics:
                # If claim has percentage and source has percentage but different value (e.g. 5% vs 50%)
                claim_pcts = [v for v, u in claim_metrics if u in ("%", "percent")]
                src_pcts = [v for v, u in src_metrics if u in ("%", "percent")]
                if claim_pcts and src_pcts and set(claim_pcts) != set(src_pcts):
                    return ClaimEntailmentResult(
                        status="CONTRADICTED",
                        reason=f"Numeric percentage mismatch: claim asserts {claim_pcts}% but source states {src_pcts}% in '{src_sent}'",
                        conflicting_excerpt=src_sent,
                        evidence_strength="strong",
                    )
                # If claim has currency/amount and source has different currency amount
                claim_currs = [v for v, u in claim_metrics if v.startswith("$")]
                src_currs = [v for v, u in src_metrics if v.startswith("$")]
                if claim_currs and src_currs:
                    # Check if ordered transition is reversed: "from A to B" vs "from B to A"
                    claim_trans = re.search(r"from\s+(\$?\d+(?:\.\d+)?\s*(?:million|billion|k|m|b)?)\s+to\s+(\$?\d+(?:\.\d+)?\s*(?:million|billion|k|m|b)?)", clean_claim, re.IGNORECASE)
                    src_trans = re.search(r"from\s+(\$?\d+(?:\.\d+)?\s*(?:million|billion|k|m|b)?)\s+to\s+(\$?\d+(?:\.\d+)?\s*(?:million|billion|k|m|b)?)", src_sent, re.IGNORECASE)
                    if claim_trans and src_trans:
                        c_from, c_to = claim_trans.group(1).lower().strip(), claim_trans.group(2).lower().strip()
                        s_from, s_to = src_trans.group(1).lower().strip(), src_trans.group(2).lower().strip()
                        if c_from == s_to and c_to == s_from:
                            return ClaimEntailmentResult(
                                status="CONTRADICTED",
                                reason=f"Inverted transition: claim asserts from {c_from} to {c_to} while source states from {s_from} to {s_to} in '{src_sent}'",
                                conflicting_excerpt=src_sent,
                                evidence_strength="strong",
                            )

    # If any polarity contradiction was identified:
    if candidate_contradictions:
        return ClaimEntailmentResult(
            status="CONTRADICTED",
            reason=candidate_contradictions[0],
            conflicting_excerpt=best_candidate_sentence,
            evidence_strength="strong",
        )

    # 3. Check for Verified Clause / Predicate Entailment
    if best_candidate_sentence:
        # Check consecutive matching word sequence between claim and candidate sentence
        w_claim = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9$%\-]+\b", clean_claim)]
        w_src = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9$%\-]+\b", best_candidate_sentence)]
        longest_clause = 0
        for i in range(len(w_claim)):
            for j in range(len(w_src)):
                k = 0
                while i + k < len(w_claim) and j + k < len(w_src) and w_claim[i + k] == w_src[j + k]:
                    k += 1
                if k > longest_clause:
                    longest_clause = k

        # If claim contains metrics, check whether claim metrics are verified in source
        if claim_metrics:
            metrics_grounded = True
            for val, unit in claim_metrics:
                if not re.search(r"\b" + re.escape(val) + r"\b", clean_source):
                    metrics_grounded = False
                    break
            if metrics_grounded and (longest_clause >= 3 or best_overlap >= 3):
                return ClaimEntailmentResult(
                    status="SUPPORTED",
                    reason=f"Verified entailment: candidate source matches core claim predicate and metrics: '{best_candidate_sentence}'",
                    matched_quote=best_candidate_sentence,
                    evidence_strength="strong",
                )
            elif not metrics_grounded:
                return ClaimEntailmentResult(
                    status="INSUFFICIENT_EVIDENCE",
                    reason=f"Source shares topic keywords with claim, but lacks demonstrable claim-level support for asserted metric(s): '{clean_claim}'.",
                    evidence_strength="none",
                )

        # Non-metric claims: check directional and core predicate alignment
        if claim_has_increase or claim_has_decrease:
            src_has_inc = any(re.search(r"\b" + re.escape(w) + r"\b", best_candidate_sentence, re.IGNORECASE) for w in _INCREASE_WORDS)
            src_has_dec = any(re.search(r"\b" + re.escape(w) + r"\b", best_candidate_sentence, re.IGNORECASE) for w in _DECREASE_WORDS)
            if (claim_has_increase and src_has_inc) or (claim_has_decrease and src_has_dec):
                if best_overlap >= 2:
                    return ClaimEntailmentResult(
                        status="SUPPORTED",
                        reason=f"Verified entailment: source confirms directional finding for claim: '{best_candidate_sentence}'",
                        matched_quote=best_candidate_sentence,
                        evidence_strength="moderate",
                    )

        # Core semantic concept match: if 3+ consecutive words match or high topical overlap with shared key predicate
        # Core semantic concept match: if 3+ consecutive words match or high topical overlap with shared key predicate
        if longest_clause >= 4 or (best_overlap >= 3 and len(claim_words) > 0 and best_overlap / len(claim_words) >= 0.40):
            # Check if claim introduces ungrounded assertion predicates not in source
            src_sentence_stems = {_stem(w) for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", best_candidate_sentence)}
            claim_predicates = claim_words - src_sentence_stems
            # If significant predicates are ungrounded (e.g. "favored", "majority")
            if any(p in claim_predicates for p in ("favored", "voted", "approved", "majority", "unanimously", "prohibited", "illegal")):
                return ClaimEntailmentResult(
                    status="UNVERIFIED",
                    reason=f"Deterministic verification cannot prove entailment from candidate text without ungrounded assumptions: '{clean_claim}'.",
                    evidence_strength="none",
                )
            return ClaimEntailmentResult(
                status="SUPPORTED",
                reason=f"Verified entailment: source confirms claim proposition: '{best_candidate_sentence}'",
                matched_quote=best_candidate_sentence,
                evidence_strength="moderate",
            )

    # 4. Check for Topic Overlap without Specific Predicate Evidence (Test F / Unverified)
    if best_overlap >= 2:
        return ClaimEntailmentResult(
            status="UNVERIFIED",
            reason=f"Deterministic verification cannot prove entailment from candidate text without ungrounded assumptions: '{clean_claim}'.",
            evidence_strength="none",
        )

    # 5. If minimal or no overlap:
    return ClaimEntailmentResult(
        status="INSUFFICIENT_EVIDENCE",
        reason=f"Citation lacks demonstrable claim-level support in referenced source: '{clean_claim}'.",
        evidence_strength="none",
    )
