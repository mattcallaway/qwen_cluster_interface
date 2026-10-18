"""Canonical structured knowledge store for Project and Collective scopes.

Supports claim-level evidence, provenance, FTS5 search, freshness evaluation,
knowledge impact tracking, contradiction detection, and collective promotion governance.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import db_transaction, get_db_connection
from app.services.orchestration.common import new_id, now_iso

logger = logging.getLogger("qc.knowledge_store")

VALID_SCOPES = ("project", "collective")
VALID_KNOWLEDGE_TYPES = (
    "fact", "source_observation", "inference", "estimate", "hypothesis",
    "decision", "procedure", "lesson_learned", "contradiction", "open_question"
)
VALID_VERIFICATION_STATUSES = (
    "unverified", "supported", "corroborated", "contradicted", "insufficient_evidence", "outdated"
)


VALID_USAGE_TYPES = (
    "planning_assumption", "task_context", "verification_evidence",
    "deliverable_claim", "decision_basis", "retrospective"
)

# Common indicators of private or project-specific data that disallow automatic collective promotion
_PRIVATE_INDICATORS = re.compile(
    r"\b(password|secret|api_key|token|internal_ip|credential|confidential|proprietary|private_key)\b",
    re.IGNORECASE
)


def _clean_str(val: Optional[str], default: str = "") -> str:
    return (val or default).strip()


def _ensure_json_str(val: Any) -> str:
    if isinstance(val, str):
        try:
            json.loads(val)
            return val
        except Exception:
            return json.dumps([val] if val else [])
    elif isinstance(val, (list, dict)):
        return json.dumps(val)
    return "[]"


# ---------------------------------------------------------------------------
# Knowledge Records CRUD
# ---------------------------------------------------------------------------

def create_knowledge_record(
    *,
    subject: str,
    statement: str,
    knowledge_type: str = "fact",
    scope: str = "project",
    project_id: Optional[str] = None,
    source_project_id: Optional[str] = None,
    summary: str = "",
    source_ids: Optional[List[str]] = None,
    evidence_ids: Optional[List[str]] = None,
    verification_status: str = "unverified",
    verification_method: str = "deterministic",
    confidence_assessment: str = "moderate",
    valid_from: Optional[str] = None,
    valid_until: Optional[str] = None,
    superseded_by: Optional[str] = None,
    contradicts: Optional[str] = None,
    related_entities: Optional[List[str]] = None,
    topics: Optional[List[str]] = None,
    keywords: Optional[List[str]] = None,
    embedding_reference: Optional[str] = None,
    promoted_by: Optional[str] = None,
    promotion_policy: Optional[str] = None,
    access_policy: str = "standard",
) -> Dict[str, Any]:
    """Create a structured, validated knowledge record."""
    clean_subject = _clean_str(subject)
    clean_statement = _clean_str(statement)
    if not clean_subject:
        raise ValueError("Subject is required.")
    if not clean_statement:
        raise ValueError("Statement is required.")
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope '{scope}'; must be one of {VALID_SCOPES}")
    if knowledge_type not in VALID_KNOWLEDGE_TYPES:
        raise ValueError(f"Invalid knowledge_type '{knowledge_type}'")
    if verification_status not in VALID_VERIFICATION_STATUSES:
        raise ValueError(f"Invalid verification_status '{verification_status}'")
    if scope == "project" and not project_id:
        raise ValueError("project_id is required for project-scoped knowledge.")

    kid = new_id("kn")
    now = now_iso()
    effective_source_project = source_project_id or project_id

    s_ids = _ensure_json_str(source_ids or [])
    e_ids = _ensure_json_str(evidence_ids or [])
    ents = _ensure_json_str(related_entities or [])
    tops = _ensure_json_str(topics or [])
    keys = _ensure_json_str(keywords or [])

    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_records (
                id, scope, project_id, source_project_id, subject, knowledge_type,
                statement, summary, source_ids_json, evidence_ids_json, verification_status,
                verification_method, confidence_assessment, created_at, last_verified_at,
                valid_from, valid_until, superseded_by, contradicts, related_entities_json,
                topics_json, keywords_json, embedding_reference, promoted_by, promoted_at,
                promotion_policy, access_policy, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                kid, scope, project_id, effective_source_project, clean_subject, knowledge_type,
                clean_statement, summary, s_ids, e_ids, verification_status,
                verification_method, confidence_assessment, now, now if verification_status in ("supported", "corroborated") else None,
                valid_from, valid_until, superseded_by, contradicts, ents,
                tops, keys, embedding_reference, promoted_by, now if promoted_by else None,
                promotion_policy, access_policy, now
            )
        )

    # Check for direct contradictions with existing records on same subject
    _detect_and_link_contradictions(kid, clean_subject, clean_statement, project_id)

    return get_knowledge_record(kid)  # type: ignore


def get_knowledge_record(knowledge_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single knowledge record with parsed JSON fields."""
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_records WHERE id = ?", (knowledge_id,)
        ).fetchone()
        if not row:
            return None
        return _format_record(dict(row))
    finally:
        conn.close()


def _format_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    for json_field in ("source_ids_json", "evidence_ids_json", "related_entities_json", "topics_json", "keywords_json"):
        val = rec.get(json_field)
        key = json_field.replace("_json", "")
        if isinstance(val, str):
            try:
                rec[key] = json.loads(val)
            except Exception:
                rec[key] = []
        else:
            rec[key] = val or []
    return rec


def list_knowledge_records(
    *,
    project_id: Optional[str] = None,
    scope: Optional[str] = None,
    include_collective: bool = True,
    knowledge_type: Optional[str] = None,
    verification_status: Optional[str] = None,
    topic: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """List knowledge records enforcing strict project isolation.
    
    If project_id is provided and include_collective is True, returns:
      - Records belonging to that specific project_id
      - Plus collective records (scope='collective')
    Never leaks private knowledge from unrelated projects.
    """
    conditions: List[str] = []
    params: List[Any] = []

    if project_id:
        if scope == "project":
            conditions.append("project_id = ? AND scope = 'project'")
            params.append(project_id)
        elif include_collective:
            conditions.append("(project_id = ? OR scope = 'collective')")
            params.append(project_id)
        else:
            conditions.append("project_id = ?")
            params.append(project_id)
    elif scope:
        conditions.append("scope = ?")
        params.append(scope)

    if knowledge_type:
        conditions.append("knowledge_type = ?")
        params.append(knowledge_type)

    if verification_status:
        conditions.append("verification_status = ?")
        params.append(verification_status)

    if topic:
        conditions.append("topics_json LIKE ?")
        params.append(f"%{topic}%")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""SELECT * FROM knowledge_records {where_clause}
              ORDER BY created_at DESC LIMIT ? OFFSET ?"""
    params.extend([limit, offset])

    conn = get_db_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [_format_record(dict(r)) for r in rows]
    finally:
        conn.close()


def search_knowledge_records(
    query: str,
    *,
    project_id: Optional[str] = None,
    scope: Optional[str] = None,
    include_collective: bool = True,
    verification_status: Optional[str] = None,
    limit: int = 25
) -> List[Dict[str, Any]]:
    """Search knowledge records using SQLite FTS5 with relevance and freshness ranking."""
    q = query.strip()
    if not q:
        return []

    clean_terms = re.findall(r"\w+", q)
    if not clean_terms:
        return []
    stopwords = {"the", "a", "an", "and", "or", "in", "on", "for", "of", "to", "with", "is", "are", "use", "using", "by", "as", "at"}
    meaningful = [t for t in clean_terms if t.lower() not in stopwords]
    search_terms = meaningful if meaningful else clean_terms
    fts_query = " OR ".join([f'"{t}"*' for t in search_terms])

    conn = get_db_connection()
    try:
        fts_rows = conn.execute(
            """SELECT record_id, rank FROM knowledge_records_fts
               WHERE knowledge_records_fts MATCH ?
               ORDER BY rank LIMIT 100""",
            (fts_query,)
        ).fetchall()
        matched_ids = [r["record_id"] for r in fts_rows]

    except Exception:
        # Fallback to LIKE if FTS query syntax error
        matched_ids = []
    finally:
        conn.close()

    if matched_ids:
        placeholders = ",".join("?" for _ in matched_ids)
        sql = f"SELECT * FROM knowledge_records WHERE id IN ({placeholders})"
        params = list(matched_ids)
    else:
        like_term = f"%{q}%"
        sql = "SELECT * FROM knowledge_records WHERE subject LIKE ? OR statement LIKE ? OR summary LIKE ?"
        params = [like_term, like_term, like_term]

    # Add project scope / isolation filtering in Python or SQL
    conn = get_db_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        records = [_format_record(dict(r)) for r in rows]
    finally:
        conn.close()

    # Enforce project isolation and score
    filtered: List[Dict[str, Any]] = []
    for r in records:
        if project_id:
            if scope == "project" and r["project_id"] != project_id:
                continue
            if not include_collective and r["project_id"] != project_id:
                continue
            if r["scope"] == "project" and r["project_id"] != project_id:
                continue  # Never leak private knowledge from another project!
        elif scope and r["scope"] != scope:
            continue

        if verification_status and r["verification_status"] != verification_status:
            continue

        # Calculate rank score
        # Priority: Corroborated / Supported > Freshness > Direct Project Match
        status_weights = {
            "corroborated": 30.0,
            "supported": 20.0,
            "unverified": 5.0,
            "insufficient_evidence": 0.0,
            "outdated": -10.0,
            "contradicted": -20.0,
        }
        score = status_weights.get(r["verification_status"], 0.0)
        if project_id and r.get("project_id") == project_id:
            score += 15.0  # Contextually nearest

        freshness = evaluate_freshness(r)
        if freshness["is_fresh"]:
            score += 10.0
        else:
            score -= 15.0

        r["search_score"] = score
        r["freshness"] = freshness
        filtered.append(r)

    filtered.sort(key=lambda x: x.get("search_score", 0), reverse=True)
    return filtered[:limit]


# ---------------------------------------------------------------------------
# Contradictions, Supersession, and Impact Tracking
# ---------------------------------------------------------------------------

def _detect_and_link_contradictions(new_kid: str, subject: str, statement: str, project_id: Optional[str]) -> None:
    """Detect if the new record explicitly opposes or contradicts an existing record."""
    low_stmt = statement.lower()
    negations = ("is not", "cannot", "does not", "deprecated", "obsolete", "removed in", "false", "no longer")
    has_negation = any(n in low_stmt for n in negations)

    if not has_negation:
        return

    # Check existing records on same subject
    conn = get_db_connection()
    try:
        candidates = conn.execute(
            """SELECT id, statement, verification_status FROM knowledge_records
               WHERE subject = ? AND id != ? AND verification_status IN ('supported', 'corroborated', 'unverified')
               LIMIT 5""",
            (subject, new_kid)
        ).fetchall()
    finally:
        conn.close()

    for cand in candidates:
        cand_id = cand["id"]
        # Record contradiction relation
        with db_transaction() as c:
            c.execute(
                "UPDATE knowledge_records SET contradicts = ?, updated_at = ? WHERE id = ?",
                (cand_id, now_iso(), new_kid)
            )
        logger.info("Contradiction identified between knowledge %s and existing record %s on '%s'", new_kid, cand_id, subject)


def record_contradiction(record_a_id: str, record_b_id: str, details: str = "") -> None:
    """Formally record a contradiction between two knowledge records and flag affected impacts."""
    now = now_iso()
    with db_transaction() as conn:
        conn.execute(
            "UPDATE knowledge_records SET contradicts = ?, verification_status = 'contradicted', updated_at = ? WHERE id = ?",
            (record_b_id, now, record_a_id)
        )
        conn.execute(
            "UPDATE knowledge_records SET contradicts = ?, verification_status = 'contradicted', updated_at = ? WHERE id = ?",
            (record_a_id, now, record_b_id)
        )

    # Flag all project decisions and tasks that depended on either record!
    flag_affected_impacts(record_a_id, f"Contradiction detected with record {record_b_id}: {details}")
    flag_affected_impacts(record_b_id, f"Contradiction detected with record {record_a_id}: {details}")


def supersede_knowledge_record(
    old_id: str = "",
    new_id: str = "",
    reason: str = "",
    old_knowledge_id: str = "",
    new_knowledge_id: str = ""
) -> None:
    """Supersede an outdated knowledge record with a newer record and flag affected impacts."""
    target_old = old_id or old_knowledge_id
    target_new = new_id or new_knowledge_id
    now = now_iso()
    with db_transaction() as conn:
        conn.execute(
            """UPDATE knowledge_records
               SET superseded_by = ?, verification_status = 'outdated', updated_at = ?
               WHERE id = ?""",
            (target_new, now, target_old)
        )

    flag_affected_impacts(target_old, f"Knowledge superseded by {target_new}: {reason or 'Newer evidence established'}")



def record_knowledge_impact(
    *,
    knowledge_id: str,
    project_id: str,
    usage_type: str,
    run_id: Optional[str] = None,
    stage_id: Optional[str] = None,
    task_id: Optional[str] = None,
    deliverable_id: Optional[str] = None,
    impact_summary: str = ""
) -> str:
    """Record that a knowledge record was used in a planning decision, task, or deliverable."""
    if usage_type not in VALID_USAGE_TYPES:
        raise ValueError(f"Invalid usage_type '{usage_type}'")

    imp_id = new_id("imp")
    now = now_iso()
    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_impacts (
                id, knowledge_id, project_id, run_id, stage_id, task_id,
                deliverable_id, usage_type, impact_summary, is_affected, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (imp_id, knowledge_id, project_id, run_id, stage_id, task_id, deliverable_id, usage_type, impact_summary, now)
        )
    return imp_id


def flag_affected_impacts(knowledge_id: str, reason: str) -> int:
    """Flag all decisions, tasks, and deliverables that relied on this knowledge record."""
    now = now_iso()
    with db_transaction() as conn:
        cur = conn.execute(
            """UPDATE knowledge_impacts
               SET is_affected = 1, affected_reason = ?, flagged_at = ?
               WHERE knowledge_id = ? AND is_affected = 0""",
            (reason, now, knowledge_id)
        )
        count = cur.rowcount
    if count > 0:
        logger.warning("Flagged %d project asset(s) affected by updated knowledge %s: %s", count, knowledge_id, reason)
    return count


def list_affected_impacts(project_id: str) -> List[Dict[str, Any]]:
    """Return all assets in a project affected by superseded, contradicted, or stale knowledge."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """SELECT i.*, k.subject, k.statement, k.verification_status, k.superseded_by, k.contradicts
               FROM knowledge_impacts i
               JOIN knowledge_records k ON k.id = i.knowledge_id
               WHERE i.project_id = ? AND i.is_affected = 1
               ORDER BY i.flagged_at DESC""",
            (project_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def record_contradiction(
    knowledge_id: str,
    contradicting_knowledge_id: str,
    reason: str = "Evidence conflicts with previously established finding",
) -> None:
    """Record that a knowledge record contradicts another, and flag dependent impacts."""
    now = now_iso()
    with db_transaction() as conn:
        conn.execute(
            """UPDATE knowledge_records
               SET contradicts = ?, verification_status = 'contradicted', updated_at = ?
               WHERE id = ?""",
            (contradicting_knowledge_id, now, knowledge_id)
        )
    flag_affected_impacts(knowledge_id, f"Contradicted by record {contradicting_knowledge_id}: {reason}")


# ---------------------------------------------------------------------------


# Freshness Policies & Evaluation
# ---------------------------------------------------------------------------

def get_freshness_policy(subject_or_type: str) -> Dict[str, Any]:
    """Look up freshness threshold for a subject or information type."""
    conn = get_db_connection()
    try:
        # Check specific subject pattern first, then fall back to policy_type, then 'standard'
        row = conn.execute(
            "SELECT * FROM knowledge_freshness_policies WHERE subject_pattern = ?",
            (subject_or_type,)
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT * FROM knowledge_freshness_policies WHERE policy_type = ?",
                (subject_or_type,)
            ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT * FROM knowledge_freshness_policies WHERE subject_pattern = 'standard'"
            ).fetchone()
        return dict(row) if row else {"max_age_days": 30, "policy_type": "standard"}
    finally:
        conn.close()


def evaluate_freshness(record: Dict[str, Any]) -> Dict[str, Any]:
    """Assess whether a knowledge record is current according to freshness policy."""
    policy = get_freshness_policy(record.get("knowledge_type") or "standard")
    max_days = policy.get("max_age_days", 30)

    # Calculate age from last_verified_at or created_at
    ref_date_str = record.get("last_verified_at") or record.get("created_at")
    age_days = 0.0
    if ref_date_str:
        try:
            ref_dt = datetime.fromisoformat(ref_date_str.replace("Z", "+00:00"))
            age_days = max(0.0, (datetime.now(timezone.utc) - ref_dt).total_seconds() / 86400.0)
        except Exception:
            age_days = 0.0

    is_fresh = (
        age_days <= max_days
        and record.get("verification_status") not in ("outdated", "contradicted")
        and not record.get("superseded_by")
    )

    return {
        "is_fresh": is_fresh,
        "age_days": round(age_days, 1),
        "max_age_days": max_days,
        "policy_type": policy.get("policy_type", "standard"),
        "needs_refresh": not is_fresh,
        "reason": "Knowledge exceeds freshness threshold" if age_days > max_days else (
            "Knowledge is marked outdated/contradicted" if not is_fresh else "Fresh"
        )
    }


# ---------------------------------------------------------------------------
# Collective Knowledge Promotion Governance
# ---------------------------------------------------------------------------

def evaluate_promotion_eligibility(record: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate whether a project knowledge record satisfies collective promotion policy."""
    statement = record.get("statement", "")
    summary = record.get("summary", "")
    full_text = f"{statement} {summary}"

    # 1. Privacy / Confidentiality check
    if _PRIVATE_INDICATORS.search(full_text):
        return {
            "eligible": False,
            "auto_promotable": False,
            "reason": "Contains indicators of sensitive, private, or credential information."
        }

    # 2. Verification status check
    v_status = record.get("verification_status")
    if v_status not in ("supported", "corroborated"):
        return {
            "eligible": False,
            "auto_promotable": False,
            "reason": f"Verification status '{v_status}' is insufficient for collective promotion; must be 'supported' or 'corroborated'."
        }

    # 3. Source requirement
    sources = record.get("source_ids") or []
    if not sources:
        return {
            "eligible": False,
            "auto_promotable": False,
            "reason": "Material factual claims must have at least one traceable source reference."
        }

    # 4. Conservative automatic promotion:
    # Only corroborated facts/procedures with multiple sources and high confidence
    auto_promotable = (
        v_status == "corroborated"
        and len(sources) >= 2
        and record.get("confidence_assessment") in ("high", "verified")
    )

    return {
        "eligible": True,
        "auto_promotable": auto_promotable,
        "reason": "Verified technical finding with established source provenance."
    }


def propose_promotion(
    knowledge_id: str,
    source_project_id: str,
    policy: str = "public_technical",
    rationale: str = ""
) -> Dict[str, Any]:
    """Propose a project knowledge record for promotion to the collective knowledge base."""
    rec = get_knowledge_record(knowledge_id)
    if not rec:
        raise ValueError("Knowledge record not found.")

    eligibility = evaluate_promotion_eligibility(rec)
    now = now_iso()
    prm_id = new_id("prm")

    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_promotions (
                id, knowledge_id, source_project_id, status, promotion_policy,
                quality_assessment, privacy_check_passed, rationale, created_at
            ) VALUES (?, ?, ?, 'proposed', ?, ?, ?, ?, ?)""",
            (
                prm_id, knowledge_id, source_project_id, policy,
                eligibility["reason"], 1 if eligibility["eligible"] else 0,
                rationale or eligibility["reason"], now
            )
        )

    # If policy allows auto-promotion and record is strictly eligible:
    if eligibility["auto_promotable"]:
        approve_promotion(prm_id, reviewer="system_auto_promotion", rationale="Satisfied conservative automatic promotion policy")

    return get_promotion(prm_id)  # type: ignore


def get_promotion(promotion_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM knowledge_promotions WHERE id = ?", (promotion_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def approve_promotion(promotion_id: str, reviewer: str, rationale: str = "") -> Dict[str, Any]:
    """Approve a knowledge promotion, elevating the record to collective scope."""
    prm = get_promotion(promotion_id)
    if not prm:
        raise ValueError("Promotion request not found.")

    now = now_iso()
    with db_transaction() as conn:
        conn.execute(
            """UPDATE knowledge_promotions
               SET status = 'approved', reviewed_by = ?, reviewed_at = ?, rationale = COALESCE(?, rationale)
               WHERE id = ?""",
            (reviewer, now, rationale, promotion_id)
        )
        conn.execute(
            """UPDATE knowledge_records
               SET scope = 'collective', promoted_by = ?, promoted_at = ?, promotion_policy = ?, updated_at = ?
               WHERE id = ?""",
            (reviewer, now, prm["promotion_policy"], now, prm["knowledge_id"])
        )

    logger.info("Knowledge record %s promoted to collective scope by %s", prm["knowledge_id"], reviewer)
    return get_promotion(promotion_id)  # type: ignore


def reject_promotion(promotion_id: str, reviewer: str, rationale: str = "") -> Dict[str, Any]:
    """Reject a knowledge promotion request."""
    now = now_iso()
    with db_transaction() as conn:
        conn.execute(
            """UPDATE knowledge_promotions
               SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, rationale = ?
               WHERE id = ?""",
            (reviewer, now, rationale or "Rejected by reviewer.", promotion_id)
        )
    return get_promotion(promotion_id)  # type: ignore


def list_promotions(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """List promotion requests with associated knowledge details."""
    conn = get_db_connection()
    try:
        where = "WHERE p.status = ?" if status else ""
        params = [status] if status else []
        sql = f"""SELECT p.*, k.subject, k.statement, k.knowledge_type, k.verification_status, k.scope
                  FROM knowledge_promotions p
                  JOIN knowledge_records k ON k.id = p.knowledge_id
                  {where}
                  ORDER BY p.created_at DESC"""
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
