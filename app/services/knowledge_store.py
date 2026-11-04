"""Canonical structured knowledge store for Project and Collective scopes.

Supports claim-level evidence, provenance, FTS5 search, freshness evaluation,
knowledge impact tracking, contradiction detection, and collective promotion governance.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.config import settings
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
VALID_ACCESS_POLICIES = ("private_project", "standard", "restricted", "collective_shareable")

# Stage 7 Trust Tier Lifecycle: Captured -> Candidate -> Corroborated -> Reviewed -> Reusable
VALID_TRUST_TIERS = ("captured", "candidate", "corroborated", "reviewed", "reusable")

VALID_TIER_TRANSITIONS = {
    "captured": {"candidate", "corroborated"},
    "candidate": {"corroborated", "reviewed"},
    "corroborated": {"reviewed", "reusable"},
    "reviewed": {"reusable", "corroborated"},
    "reusable": set(),
}

VALID_CONFLICT_TYPES = (
    "direct_contradiction", "scope_conflict", "version_supersession", "semantic_divergence"
)
VALID_CONFLICT_STATUSES = (
    "unresolved", "resolved_superseded", "resolved_coexist", "resolved_dismissed"
)

VALID_USAGE_TYPES = (
    "planning_assumption", "task_context", "verification_evidence",
    "deliverable_claim", "decision_basis", "retrospective"
)


# ---------------------------------------------------------------------------
# Operator Authority & Verification (Stage 7 Security Gate)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OperatorAuthority:
    """Strongly-typed, cryptographically signed operator authority context.
    
    A caller cannot gain privileges by instantiating this dataclass directly;
    every valid instance must possess a cryptographic HMAC signature issued
    by the server-side authority factory using the application's secret key.
    Only authenticated human sessions carry is_human=True; internal services
    cannot satisfy human-review requirements.
    """
    principal: str
    is_operator: bool = True
    can_force_transition: bool = False
    session_verified: bool = True
    audit_reason: str = ""
    issued_at: str = ""
    allowed_operations: Tuple[str, ...] = ("review", "promote")
    is_human: bool = False
    signature: str = ""


# Scoped internal service principals: services may propose promotions, collect corroborations,
# or execute previously authorized transitions, but CANNOT perform human review or bypass human review.
TRUSTED_SERVICE_PRINCIPALS: Dict[str, Dict[str, Any]] = {
    "qc:service:governance_engine": {
        "granted_operations": ("propose", "promote", "execute_transition"),
        "can_force": False,
        "is_operator": True,
    },
    "qc:service:knowledge_migrator": {
        "granted_operations": ("import", "corroborate", "propose"),
        "can_force": False,
        "is_operator": True,
    },
    "qc:service:system_controller": {
        "granted_operations": ("propose", "promote", "execute_transition"),
        "can_force": False,
        "is_operator": True,
    },
    "qc:service:test_harness": {
        "granted_operations": ("corroborate", "propose", "promote", "execute_transition"),
        "can_force": False,
        "is_operator": True,
    },
    "qc:service:auto_promotion": {
        "granted_operations": ("propose", "promote", "execute_transition"),
        "can_force": False,
        "is_operator": True,
    },
}


def compute_authority_signature(
    principal: str,
    is_operator: bool,
    can_force_transition: bool,
    session_verified: bool,
    issued_at: str,
    allowed_operations: Tuple[str, ...],
    is_human: bool = False,
) -> str:
    """Compute server-side cryptographic HMAC signature for an OperatorAuthority."""
    ops_str = ",".join(sorted(allowed_operations))
    payload = f"QC_AUTH_V2:{principal}:{int(is_operator)}:{int(can_force_transition)}:{int(session_verified)}:{int(is_human)}:{issued_at}:{ops_str}"
    return hmac.new(settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_operator_authority_from_session(
    session_token: Optional[str] = None,
    *,
    request: Optional[Any] = None,
    audit_reason: str = "",
    allowed_operations: Optional[Tuple[str, ...]] = None,
) -> OperatorAuthority:
    """Derive OperatorAuthority from an authenticated HTTP session or session token.
    
    Validates user against SQLite users table and checks authoritative administrative roles.
    Human operator sessions carry is_human=True.
    Ordinary users do NOT receive operator or forced-transition privileges.
    """
    from app.auth import verify_session_token
    token = session_token
    if not token and request is not None and hasattr(request, "cookies"):
        token = request.cookies.get("qc_session")
    if not token:
        raise PermissionError("Unauthorized: Missing session token or unauthenticated HTTP request.")

    username = verify_session_token(token)
    if not username:
        raise PermissionError("Unauthorized: Session token is invalid, expired, or tampered.")

    conn = get_db_connection()
    try:
        user_row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not user_row:
            raise PermissionError(f"Unauthorized: Authenticated user '{username}' not found in authoritative users database.")
        
        user_dict = dict(user_row)
        user_role = user_dict.get("role") or ("admin" if username == "admin" else "user")
        is_admin = (user_role == "admin" or username == "admin")
        
        is_op = is_admin
        can_force = is_admin
        ops = allowed_operations or (("review", "promote", "force_transition") if is_admin else ())
        now_ts = now_iso()
        is_human = True
        sig = compute_authority_signature(username, is_op, can_force, True, now_ts, ops, is_human=is_human)
        return OperatorAuthority(
            principal=username,
            is_operator=is_op,
            can_force_transition=can_force,
            session_verified=True,
            audit_reason=audit_reason,
            issued_at=now_ts,
            allowed_operations=ops,
            is_human=is_human,
            signature=sig,
        )
    finally:
        conn.close()


def create_internal_service_authority(
    service_principal: str,
    operation: str,
    service_key: str,
    audit_reason: str = "",
) -> OperatorAuthority:
    """Mint an OperatorAuthority for an audited internal service principal.
    
    Verifies service_key against server secret and enforces granted operation boundaries.
    Internal services are NEVER human operators (is_human=False) and cannot satisfy human review.
    Test principals (qc:service:test_harness) are strictly blocked in production mode.
    """
    # Correction 2: Disallow qc:service:test_harness in production environment
    if service_principal == "qc:service:test_harness" and settings.environment == "production":
        raise PermissionError(
            "Production security restriction: Test harness principal 'qc:service:test_harness' cannot obtain "
            "authority in production environment."
        )

    if not service_key or not (
        hmac.compare_digest(service_key, settings.secret_key)
        or hmac.compare_digest(service_key, hmac.new(settings.secret_key.encode("utf-8"), f"service_key:{service_principal}".encode("utf-8"), hashlib.sha256).hexdigest())
    ):
        raise PermissionError(f"Unauthorized: Invalid service secret key for internal service principal '{service_principal}'.")

    spec = TRUSTED_SERVICE_PRINCIPALS.get(service_principal)
    if not spec:
        raise PermissionError(f"Unauthorized: Unknown internal service principal '{service_principal}'.")

    if operation not in spec["granted_operations"]:
        raise PermissionError(
            f"Unauthorized: Service principal '{service_principal}' is not granted operation '{operation}'. "
            f"Granted operations: {spec['granted_operations']}"
        )

    can_force = spec.get("can_force", False) and (operation == "force_transition")
    is_op = spec.get("is_operator", True)
    ops = tuple(spec["granted_operations"])
    now_ts = now_iso()
    is_human = False
    sig = compute_authority_signature(service_principal, is_op, can_force, True, now_ts, ops, is_human=is_human)
    return OperatorAuthority(
        principal=service_principal,
        is_operator=is_op,
        can_force_transition=can_force,
        session_verified=True,
        audit_reason=audit_reason,
        issued_at=now_ts,
        allowed_operations=ops,
        is_human=is_human,
        signature=sig,
    )


def validate_operator_authority(
    authority: Optional[Union[OperatorAuthority, Any]],
    *,
    require_force_permission: bool = False,
    operation: str = "review"
) -> OperatorAuthority:
    """Validate operator authority at the server-side boundary.
    
    Enforces that:
    1. The authority object is a valid OperatorAuthority possessing a valid cryptographic signature.
    2. Caller-supplied booleans are NOT trusted; signature proves attributes were minted server-side.
    3. The principal is authorized for the requested operation.
    4. Plain string identifiers or dictionaries without cryptographic proof are strictly rejected.
    """
    if not authority:
        raise PermissionError(f"Unauthorized: Operator authority required for {operation}.")

    if isinstance(authority, str):
        raise PermissionError(
            f"Unauthorized: Plain username string '{authority}' cannot provide operator authority. "
            "An authenticated session token or signed OperatorAuthority object is required."
        )

    if isinstance(authority, dict):
        raise PermissionError("Unauthorized: Raw dictionaries cannot provide operator authority; signed OperatorAuthority required.")

    if not isinstance(authority, OperatorAuthority):
        raise PermissionError(f"Unauthorized: Unsupported authority type '{type(authority).__name__}'.")

    # Block test harness in production mode
    if authority.principal == "qc:service:test_harness" and settings.environment == "production":
        raise PermissionError("Production security restriction: Test harness authority is strictly forbidden in production mode.")

    # 1. Cryptographic Signature Verification
    expected_sig = compute_authority_signature(
        principal=authority.principal,
        is_operator=authority.is_operator,
        can_force_transition=authority.can_force_transition,
        session_verified=authority.session_verified,
        issued_at=authority.issued_at,
        allowed_operations=authority.allowed_operations,
        is_human=authority.is_human,
    )
    if not authority.signature or not hmac.compare_digest(authority.signature, expected_sig):
        raise PermissionError("Unauthorized: Invalid, unverified, or forged OperatorAuthority signature (tamper/forgery detected).")

    # 2. Session verification check
    if not authority.session_verified or not authority.is_operator:
        raise PermissionError(f"Unauthorized: Operator session unverified or invalid for {operation}.")

    # 3. Operation grant check
    op_key = "force_transition" if require_force_permission else operation
    if op_key not in authority.allowed_operations and "force_transition" not in authority.allowed_operations:
        raise PermissionError(f"Unauthorized: Principal '{authority.principal}' is not granted operation '{operation}'.")

    # 4. Force transition permission check
    if require_force_permission:
        if not authority.can_force_transition:
            raise PermissionError(f"Unauthorized: Principal '{authority.principal}' lacks 'can_force_transition' privilege.")
        if not (authority.audit_reason or "").strip() or len(authority.audit_reason.strip()) < 5:
            raise PermissionError("Unauthorized: Explicit audit reason (at least 5 characters) required for administrative bypass.")

    return authority


# ---------------------------------------------------------------------------
# Deep Sensitive Content Scanner (Multi-Vector Pattern & Credential Detection)
# ---------------------------------------------------------------------------

_CREDENTIAL_PATTERNS = [
    # DB / Service connection strings with credentials (postgres://user:pass@host)
    (re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^:\s@]+:[^@\s]+@[^\s]+"), "Database/Service Connection URI with Credentials"),
    # Bearer / Auth tokens
    (re.compile(r"\b(?:Bearer|Token)\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), "HTTP Authorization Bearer Token"),
    # JWT tokens
    (re.compile(r"\bey[A-Za-z0-9-_]{10,}\.ey[A-Za-z0-9-_]{10,}\.[A-Za-z0-9-_]{10,}\b"), "JSON Web Token (JWT)"),
    # Private Key blocks
    (re.compile(r"-----BEGIN\s+(?:[A-Z0-9_-]+\s+)?PRIVATE\s+KEY-----"), "Cryptographic Private Key Block"),
    # AWS Access Key ID
    (re.compile(r"\b(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b"), "AWS Access Key Identifier"),
    # GitHub personal access tokens
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,255}\b"), "GitHub Personal Access Token"),
    # Generic key assignment patterns: api_key = "...", password: "..."
    (re.compile(r"(?:api[_-]?key|secret|auth[_-]?token|password|passwd|private[_-]?key)\s*[:=]\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE), "Inline Secret/Password Assignment"),
    # Social security numbers
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "Social Security Number (PII)"),
    # Credit card numbers
    (re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"), "Credit Card Number (Financial PII)"),
    # Internal private IP addresses with sensitive service ports
    (re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}):(?:22|3306|5432|6379|27017|8080|8443|9200)\b"), "Internal Infrastructure Endpoint with Sensitive Port"),
    # Keyword indicators
    (re.compile(r"\b(password|secret|api_key|token|internal_ip|credential|confidential|proprietary|private_key)\b", re.IGNORECASE), "Confidentiality/Secret Keyword"),
]


def scan_sensitive_content(text: str) -> List[str]:
    """Scan text for credentials, connection strings, auth tokens, and sensitive patterns."""
    findings = []
    for pattern, description in _CREDENTIAL_PATTERNS:
        if pattern.search(text):
            findings.append(description)
    return findings


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
    trust_tier: str = "captured",
    review_notes: str = "",
    reviewed_by: Optional[str] = None,
    vault_note_path: Optional[str] = None,
    sync_status: str = "synced",
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
    if trust_tier not in VALID_TRUST_TIERS:
        raise ValueError(f"Invalid trust_tier '{trust_tier}'; must be one of {VALID_TRUST_TIERS}")
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
                promotion_policy, access_policy, updated_at, trust_tier, review_notes,
                reviewed_by, reviewed_at, vault_note_path, sync_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                kid, scope, project_id, effective_source_project, clean_subject, knowledge_type,
                clean_statement, summary, s_ids, e_ids, verification_status,
                verification_method, confidence_assessment, now, now if verification_status in ("supported", "corroborated") else None,
                valid_from, valid_until, superseded_by, contradicts, ents,
                tops, keys, embedding_reference, promoted_by, now if promoted_by else None,
                promotion_policy, access_policy, now, trust_tier, review_notes,
                reviewed_by, now if reviewed_by else None, vault_note_path, sync_status
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
    rec.setdefault("trust_tier", "captured")
    rec.setdefault("review_notes", "")
    rec.setdefault("sync_status", "synced")
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


def record_knowledge_conflict(
    record_a_id: str,
    record_b_id: str,
    conflict_type: str = "direct_contradiction",
    details: str = "",
) -> str:
    """Record an explicit conflict between two knowledge records into the conflict ledger."""
    if conflict_type not in VALID_CONFLICT_TYPES:
        conflict_type = "direct_contradiction"

    cid = new_id("cnf")
    now = now_iso()

    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_conflicts (
                id, record_a_id, record_b_id, conflict_type, status, details, created_at
            ) VALUES (?, ?, ?, ?, 'unresolved', ?, ?)""",
            (cid, record_a_id, record_b_id, conflict_type, details, now)
        )
        # Mark both records as contradicted, demote trust tier, and invalidate prior human review
        conn.execute(
            """UPDATE knowledge_records
               SET contradicts = ?,
                   verification_status = 'contradicted',
                   trust_tier = CASE WHEN trust_tier IN ('reusable', 'reviewed') THEN 'candidate' ELSE trust_tier END,
                   reviewed_by = NULL,
                   reviewed_at = NULL,
                   review_notes = '',
                   updated_at = ?
               WHERE id = ?""",
            (record_b_id, now, record_a_id)
        )
        conn.execute(
            """UPDATE knowledge_records
               SET contradicts = ?,
                   verification_status = 'contradicted',
                   trust_tier = CASE WHEN trust_tier IN ('reusable', 'reviewed') THEN 'candidate' ELSE trust_tier END,
                   reviewed_by = NULL,
                   reviewed_at = NULL,
                   review_notes = '',
                   updated_at = ?
               WHERE id = ?""",
            (record_a_id, now, record_b_id)
        )

    flag_affected_impacts(record_a_id, f"Contradiction conflict {cid} detected with {record_b_id}: {details}")
    flag_affected_impacts(record_b_id, f"Contradiction conflict {cid} detected with {record_a_id}: {details}")
    logger.warning("Knowledge conflict %s logged between %s and %s", cid, record_a_id, record_b_id)
    return cid


def resolve_knowledge_conflict(
    conflict_id: str,
    resolution_status: str,
    resolved_by: str,
    resolution_notes: str = "",
    winning_record_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a recorded knowledge conflict."""
    if resolution_status not in VALID_CONFLICT_STATUSES:
        raise ValueError(f"Invalid resolution_status '{resolution_status}'; must be one of {VALID_CONFLICT_STATUSES}")
    if not (resolved_by or "").strip():
        raise ValueError("Resolver identity is required.")

    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM knowledge_conflicts WHERE id = ?", (conflict_id,)).fetchone()
        if not row:
            raise ValueError(f"Conflict '{conflict_id}' not found.")
        conflict = dict(row)
    finally:
        conn.close()

    now = now_iso()
    with db_transaction() as conn:
        conn.execute(
            """UPDATE knowledge_conflicts
               SET status = ?, resolved_by = ?, resolved_at = ?, resolution_notes = ?
               WHERE id = ?""",
            (resolution_status, resolved_by, now, resolution_notes, conflict_id)
        )

        if resolution_status == "resolved_superseded" and winning_record_id:
            losing_id = conflict["record_b_id"] if winning_record_id == conflict["record_a_id"] else conflict["record_a_id"]
            conn.execute(
                """UPDATE knowledge_records
               SET superseded_by = ?, verification_status = 'outdated', updated_at = ?
               WHERE id = ?""",
                (winning_record_id, now, losing_id)
            )
            conn.execute(
                """UPDATE knowledge_records
               SET verification_status = 'supported', updated_at = ?
               WHERE id = ?""",
                (now, winning_record_id)
            )

    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM knowledge_conflicts WHERE id = ?", (conflict_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def list_knowledge_conflicts(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """List knowledge conflicts with associated record summaries."""
    conn = get_db_connection()
    try:
        where = "WHERE c.status = ?" if status else ""
        params = [status] if status else []
        sql = f"""SELECT c.*,
                         ka.subject as record_a_subject, ka.statement as record_a_statement,
                         kb.subject as record_b_subject, kb.statement as record_b_statement
                  FROM knowledge_conflicts c
                  JOIN knowledge_records ka ON ka.id = c.record_a_id
                  JOIN knowledge_records kb ON kb.id = c.record_b_id
                  {where}
                  ORDER BY c.created_at DESC"""
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def record_contradiction(record_a_id: str, record_b_id: str, details: str = "") -> None:
    """Formally record a contradiction between two knowledge records and flag affected impacts."""
    record_knowledge_conflict(record_a_id, record_b_id, conflict_type="direct_contradiction", details=details)


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
    source_project_id: Optional[str] = None,
    policy: str = "public_technical",
    rationale: str = "",
    allow_auto_promote: bool = False,
    project_id: Optional[str] = None,
    reason: str = "",
    target_tier: str = "reusable",
) -> Dict[str, Any]:
    """Propose a project knowledge record for promotion to the collective knowledge base.
    
    Stage 5 / Stage 7 Trust Tier Governance:
    Newly discovered information enters the candidate knowledge pipeline without automatic promotion
    to permanently trusted facts unless explicit approval / review is granted.
    """
    rec = get_knowledge_record(knowledge_id)
    if not rec:
        raise ValueError("Knowledge record not found.")

    eff_project_id = source_project_id or project_id or rec.get("project_id") or ""
    eff_rationale = rationale or reason

    eligibility = evaluate_promotion_eligibility(rec)
    now = now_iso()
    prm_id = new_id("prm")

    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_promotions (
                id, knowledge_id, source_project_id, status, promotion_policy,
                quality_assessment, privacy_check_passed, rationale, created_at, target_tier
            ) VALUES (?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?)""",
            (
                prm_id, knowledge_id, eff_project_id, policy,
                eligibility["reason"], 1 if eligibility["eligible"] else 0,
                eff_rationale or eligibility["reason"], now, target_tier
            )
        )

    # Only auto-promote if caller explicitly authorized auto-promotion AND record is strictly eligible
    if allow_auto_promote and eligibility["auto_promotable"]:
        auto_auth = create_internal_service_authority(
            "qc:service:auto_promotion",
            operation="promote",
            service_key=settings.secret_key,
            audit_reason="Satisfied conservative automatic promotion policy"
        )
        approve_promotion(prm_id, authority=auto_auth, rationale="Satisfied conservative automatic promotion policy")

    return get_promotion(prm_id)  # type: ignore



def get_promotion(promotion_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM knowledge_promotions WHERE id = ?", (promotion_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def approve_promotion(
    promotion_id: str,
    reviewer: Optional[str] = None,
    rationale: str = "",
    authority: Optional[Union[OperatorAuthority, str]] = None,
    session_token: Optional[str] = None,
    service_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Approve a knowledge promotion, elevating the record to collective scope and reusable trust tier.
    
    Enforces independent authenticated operator authority. Unauthenticated callers cannot approve promotions.
    """
    prm = get_promotion(promotion_id)
    if not prm:
        raise ValueError("Promotion request not found.")

    eff_auth = authority
    if not eff_auth and session_token:
        eff_auth = create_operator_authority_from_session(session_token, audit_reason=rationale)
    elif not eff_auth and service_key and reviewer and isinstance(reviewer, str):
        eff_auth = create_internal_service_authority(reviewer, operation="promote", service_key=service_key, audit_reason=rationale)
    elif not eff_auth and reviewer:
        eff_auth = reviewer

    op_auth = validate_operator_authority(
        eff_auth,
        require_force_permission=False,
        operation="promote"
    )
    validated_reviewer = op_auth.principal

    target_tier = prm.get("target_tier") or "reusable"
    eff_rec = get_knowledge_record(prm["knowledge_id"])
    if not eff_rec:
        raise ValueError("Knowledge record not found.")

    # Human review invariant: Only records with verified prior human review can be approved for promotion
    prior_human_reviewer = eff_rec.get("reviewed_by")
    if not prior_human_reviewer or prior_human_reviewer.startswith("qc:service:") or prior_human_reviewer in TRUSTED_SERVICE_PRINCIPALS:
        raise PermissionError(
            f"Mandatory human review requirement: Knowledge record '{eff_rec['id']}' must have verified prior "
            "human operator review before collective promotion can be approved. Internal services cannot satisfy human review."
        )

    # Check that content was not modified since human review
    prior_notes = eff_rec.get("review_notes") or ""
    curr_bytes = f"{eff_rec.get('subject', '')}:::{eff_rec.get('statement', '')}".encode('utf-8')
    curr_digest = hashlib.sha256(curr_bytes).hexdigest()[:16]
    if "[digest:" in prior_notes and f"[digest:{curr_digest}]" not in prior_notes:
        raise ValueError(
            "Human review invalidated: Knowledge record content was modified after human review; "
            "must undergo fresh human operator review before promotion to 'reusable'."
        )

    curr_tier = eff_rec.get("trust_tier") or "captured"
    needs_force = target_tier not in VALID_TIER_TRANSITIONS.get(curr_tier, set())

    # Build approval metadata binding human reviewer, original record digest, timestamp, and authorized scope
    now = now_iso()
    auth_scope = "collective"
    full_digest = hashlib.sha256(curr_bytes).hexdigest()
    audit_metadata = f"[human_reviewer:{prior_human_reviewer}, digest:{full_digest}, scope:{auth_scope}, approved_at:{now}]"
    eff_rationale = f"{rationale} {audit_metadata}".strip() if rationale else audit_metadata

    # Route through transition_trust_tier to enforce all contradiction, privacy, and sensitive data gates
    transition_trust_tier(
        prm["knowledge_id"],
        target_tier=target_tier,
        authority=op_auth,
        notes=eff_rationale,
        force=needs_force,
        audit_reason=eff_rationale,
        sharing_authorized=True,
    )

    with db_transaction() as conn:
        conn.execute(
            """UPDATE knowledge_promotions
               SET status = 'approved', reviewed_by = ?, reviewed_at = ?, rationale = ?
               WHERE id = ?""",
            (prior_human_reviewer, now, eff_rationale, promotion_id)
        )
        conn.execute(
            """UPDATE knowledge_records
               SET scope = 'collective',
                   promoted_by = ?,
                   promoted_at = ?,
                   promotion_policy = ?,
                   updated_at = ?
               WHERE id = ?""",
            (validated_reviewer, now, prm["promotion_policy"], now, prm["knowledge_id"])
        )

    logger.info(
        "Knowledge record %s promoted to collective reusable scope by %s (human reviewer: %s)",
        prm["knowledge_id"], validated_reviewer, prior_human_reviewer
    )
    return get_promotion(promotion_id)  # type: ignore


def reject_promotion(
    promotion_id: str,
    reviewer: Optional[str] = None,
    rationale: str = "",
    authority: Optional[Union[OperatorAuthority, str]] = None,
    session_token: Optional[str] = None,
    service_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Reject a knowledge promotion request. Enforces authenticated operator authority."""
    eff_auth = authority
    if not eff_auth and session_token:
        eff_auth = create_operator_authority_from_session(session_token, audit_reason=rationale)
    elif not eff_auth and service_key and reviewer and isinstance(reviewer, str):
        eff_auth = create_internal_service_authority(reviewer, operation="promote", service_key=service_key, audit_reason=rationale)
    elif not eff_auth and reviewer:
        eff_auth = reviewer

    op_auth = validate_operator_authority(
        eff_auth,
        require_force_permission=False,
        operation="promote"
    )
    validated_reviewer = op_auth.principal

    now = now_iso()
    with db_transaction() as conn:
        conn.execute(
            """UPDATE knowledge_promotions
               SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, rationale = ?
               WHERE id = ?""",
            (validated_reviewer, now, rationale or "Rejected by reviewer.", promotion_id)
        )
    return get_promotion(promotion_id)  # type: ignore


def list_promotions(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """List promotion requests with associated knowledge details."""
    conn = get_db_connection()
    try:
        where = "WHERE p.status = ?" if status else ""
        params = [status] if status else []
        sql = f"""SELECT p.*, k.subject, k.statement, k.knowledge_type, k.verification_status, k.scope, k.trust_tier
                  FROM knowledge_promotions p
                  JOIN knowledge_records k ON k.id = p.knowledge_id
                  {where}
                  ORDER BY p.created_at DESC"""
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def transition_trust_tier(
    knowledge_id: str,
    target_tier: str,
    reviewer: Optional[Union[str, OperatorAuthority, Dict[str, Any]]] = None,
    notes: str = "",
    force: bool = False,
    authority: Optional[Union[OperatorAuthority, Dict[str, Any], str]] = None,
    session_token: Optional[str] = None,
    service_key: Optional[str] = None,
    audit_reason: str = "",
    sharing_authorized: bool = False,
) -> Dict[str, Any]:
    """Transition a knowledge record through the strict Trust Tier lifecycle.
    
    Captured -> Candidate -> Corroborated -> Reviewed -> Reusable.
    Enforces Invariant I-11: strict promotion lifecycle with verifiable evidence,
    cryptographically validated operator authority, and fail-closed sensitive data protection.
    """
    if target_tier not in VALID_TRUST_TIERS:
        raise ValueError(f"Invalid target trust tier '{target_tier}'; must be one of {VALID_TRUST_TIERS}")

    rec = get_knowledge_record(knowledge_id)
    if not rec:
        raise ValueError(f"Knowledge record '{knowledge_id}' not found.")

    current_tier = rec.get("trust_tier") or "captured"
    if current_tier == target_tier:
        return rec

    # Contradicted records cannot be promoted to corroborated, reviewed, or reusable under ANY condition
    if rec.get("verification_status") == "contradicted" and target_tier in ("corroborated", "reviewed", "reusable"):
        raise ValueError("Cannot promote contradicted knowledge record; resolve contradiction first.")

    allowed = VALID_TIER_TRANSITIONS.get(current_tier, set())

    # Derive effective authority
    eff_auth = authority
    if not eff_auth and session_token:
        eff_auth = create_operator_authority_from_session(session_token, audit_reason=audit_reason or notes)
    elif not eff_auth and service_key and reviewer and isinstance(reviewer, str):
        eff_auth = create_internal_service_authority(reviewer, operation="force_transition" if force else "review", service_key=service_key, audit_reason=audit_reason or notes)
    elif not eff_auth and reviewer:
        eff_auth = reviewer

    validated_reviewer: Optional[str] = None

    if force:
        # Administrative force bypass requires explicit, audited operator authority
        op_auth = validate_operator_authority(
            eff_auth,
            require_force_permission=True,
            operation="force_transition"
        )
        eff_reason = audit_reason or op_auth.audit_reason or notes
        if not (eff_reason or "").strip() or len(eff_reason.strip()) < 5:
            raise PermissionError("Administrative forced trust-tier transition requires explicit non-empty audit_reason.")

        # Human review invariant: force_transition must NEVER bypass mandatory human-review requirement for Reusable
        if target_tier == "reusable":
            prior_reviewer = rec.get("reviewed_by")
            if not prior_reviewer or prior_reviewer.startswith("qc:service:") or prior_reviewer in TRUSTED_SERVICE_PRINCIPALS:
                raise PermissionError(
                    "Mandatory human review requirement: Forced transition to 'reusable' is strictly prohibited "
                    "without prior authenticated human operator review."
                )

        validated_reviewer = op_auth.principal
        logger.warning(
            "AUDIT: Administrative forced trust-tier transition executed by %s for record %s (%s -> %s). Reason: %s",
            op_auth.principal, knowledge_id, current_tier, target_tier, eff_reason
        )
    else:
        if target_tier not in allowed:
            raise ValueError(
                f"Invalid trust tier transition from '{current_tier}' to '{target_tier}'. "
                f"Allowed transitions: {sorted(allowed) if allowed else 'none'}"
            )

    # Corroborated requires multiple sources or deterministic verification
    if target_tier == "corroborated":
        sources = rec.get("source_ids") or []
        method = rec.get("verification_method") or ""
        if len(sources) < 2 and method != "deterministic":
            raise ValueError(
                "Transition to 'corroborated' requires at least 2 independent sources or deterministic verification."
            )

    # Reviewed requires explicit, verified human operator attribution and non-empty rationale
    if target_tier == "reviewed":
        if not eff_auth:
            raise ValueError("Reviewer identity is required to transition to 'reviewed'.")
        op_auth = validate_operator_authority(
            eff_auth,
            require_force_permission=False,
            operation="review"
        )
        if not op_auth.is_human or op_auth.principal.startswith("qc:service:") or op_auth.principal in TRUSTED_SERVICE_PRINCIPALS:
            raise PermissionError(
                f"Human review requirement: Only an authenticated, authorized human operator session can transition "
                f"knowledge into the 'reviewed' tier. Principal '{op_auth.principal}' is an internal service, not a human operator."
            )
        validated_reviewer = op_auth.principal
        if not (notes or "").strip():
            raise ValueError("Review rationale/notes are required to transition to 'reviewed'.")
        # Bind human review to exact content version digest
        content_bytes = f"{rec.get('subject', '')}:::{rec.get('statement', '')}".encode('utf-8')
        rev_digest = hashlib.sha256(content_bytes).hexdigest()[:16]
        if f"[digest:{rev_digest}]" not in notes:
            notes = f"{notes} [digest:{rev_digest}]".strip()

    # Reusable requires operator review, supported/corroborated evidence, explicit shareable classification, and privacy scanning
    if target_tier == "reusable":
        # 1. Access Classification Gate: private project records cannot cross into collective reusable tier
        access_policy = rec.get("access_policy") or "standard"
        if access_policy in ("private_project", "restricted"):
            raise ValueError(
                f"Knowledge record access policy is '{access_policy}'; private project knowledge cannot cross into "
                "collective 'reusable' tier without explicit sharing authorization."
            )
        if rec.get("scope") == "project" and access_policy != "collective_shareable":
            is_op = (
                sharing_authorized
                or (isinstance(eff_auth, OperatorAuthority) and eff_auth.is_operator)
            )
            if not is_op:
                raise ValueError(
                    f"Knowledge record access policy is '{access_policy}'; collective 'reusable' status requires "
                    "explicit 'collective_shareable' classification and authorized operator review."
                )

        # 2. Deep Content Scanner: multi-vector credentials, URIs, tokens, private keys, PII
        full_text = f"{rec.get('subject', '')} {rec.get('statement', '')} {rec.get('summary', '')}"
        sensitive_findings = scan_sensitive_content(full_text)
        if sensitive_findings:
            raise ValueError(
                f"Contains indicators of private, credential, or confidential data; "
                f"cannot be marked reusable. Detected categories: {', '.join(sensitive_findings[:3])}"
            )

        # 3. Verification status requirement
        if rec.get("verification_status") not in ("supported", "corroborated"):
            raise ValueError(
                f"Verification status '{rec.get('verification_status')}' is insufficient for 'reusable'; "
                "must be 'supported' or 'corroborated'."
            )

        # 4. Mandatory prior human operator review
        prior_reviewer = rec.get("reviewed_by")
        if not prior_reviewer or prior_reviewer.startswith("qc:service:") or prior_reviewer in TRUSTED_SERVICE_PRINCIPALS:
            raise PermissionError(
                "Mandatory human review requirement: Knowledge records must have verified prior human operator review "
                "before being marked 'reusable'. Internal services or unreviewed records cannot be marked reusable."
            )

        # 5. Check subsequent content modification invalidation
        prior_notes = rec.get("review_notes") or ""
        curr_bytes = f"{rec.get('subject', '')}:::{rec.get('statement', '')}".encode('utf-8')
        curr_digest = hashlib.sha256(curr_bytes).hexdigest()[:16]
        if "[digest:" in prior_notes and f"[digest:{curr_digest}]" not in prior_notes:
            raise ValueError(
                "Human review invalidated: Knowledge record content was modified after human review; "
                "must undergo fresh human operator review before promotion to 'reusable'."
            )

    now = now_iso()
    rev_str = validated_reviewer or ""

    # Record durable transition audit in knowledge_tier_transitions
    with db_transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_tier_transitions (
                id TEXT PRIMARY KEY,
                record_id TEXT NOT NULL REFERENCES knowledge_records(id) ON DELETE CASCADE,
                previous_tier TEXT NOT NULL,
                new_tier TEXT NOT NULL,
                actor_principal TEXT NOT NULL,
                authorization_basis TEXT NOT NULL,
                reason TEXT NOT NULL,
                transitioned_at TEXT NOT NULL
            )
        """)
        auth_basis = "forced_override" if force else (
            "signed_operator_session" if session_token or (isinstance(eff_auth, OperatorAuthority) and not eff_auth.principal.startswith("qc:service:"))
            else ("internal_service" if isinstance(eff_auth, OperatorAuthority) else "lifecycle_validation")
        )
        conn.execute(
            """INSERT INTO knowledge_tier_transitions (
                id, record_id, previous_tier, new_tier, actor_principal, authorization_basis, reason, transitioned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id("ktt"), knowledge_id, current_tier, target_tier,
                validated_reviewer or "system", auth_basis,
                audit_reason or notes or "Standard lifecycle transition", now
            )
        )
        conn.execute(
            """UPDATE knowledge_records
               SET trust_tier = ?,
                   reviewed_by = CASE WHEN ? != '' THEN ? ELSE reviewed_by END,
                   reviewed_at = CASE WHEN ? != '' THEN ? ELSE reviewed_at END,
                   review_notes = CASE WHEN ? != '' THEN ? ELSE review_notes END,
                   updated_at = ?
               WHERE id = ?""",
            (target_tier, rev_str, rev_str, rev_str, now, notes, notes, now, knowledge_id)
        )

    logger.info("Knowledge record %s transitioned from %s to %s", knowledge_id, current_tier, target_tier)
    return get_knowledge_record(knowledge_id)  # type: ignore


def get_governance_review_queue(project_id: Optional[str] = None) -> Dict[str, Any]:
    """Return unified governance review queue for human operators."""
    promotions = list_promotions(status="proposed")
    conflicts = list_knowledge_conflicts(status="unresolved")

    candidates = list_knowledge_records(
        project_id=project_id,
        scope=None,
        include_collective=True,
        limit=100
    )
    unreviewed = [c for c in candidates if c.get("trust_tier") == "candidate" and not c.get("reviewed_by")]
    affected = list_affected_impacts(project_id) if project_id else []

    return {
        "pending_promotions": promotions,
        "unreviewed_candidates": unreviewed,
        "unresolved_conflicts": conflicts,
        "affected_impacts": affected,
        "total_pending": len(promotions) + len(unreviewed) + len(conflicts),
    }


def sync_record_to_vault(
    knowledge_id: str,
    category: str = "research",
    project_id: Optional[str] = None
) -> str:
    """Synchronize a structured knowledge record into a Markdown vault note with frontmatter."""
    from app.services.knowledge import write_note_atomic

    rec = get_knowledge_record(knowledge_id)
    if not rec:
        raise ValueError(f"Knowledge record '{knowledge_id}' not found.")

    eff_proj = project_id or rec.get("project_id")
    if eff_proj:
        rel_path = f"projects/{eff_proj}/{knowledge_id}.md"
    else:
        rel_path = f"{category}/{knowledge_id}.md"

    # Build frontmatter
    subject = rec.get("subject", "Knowledge Note").replace("\n", " ")
    sources_str = json.dumps(rec.get("source_ids", []))
    content = f"""---
title: {subject}
kn_id: {rec['id']}
trust_tier: {rec.get('trust_tier', 'captured')}
scope: {rec.get('scope', 'project')}
project_id: {rec.get('project_id') or ''}
verification_status: {rec.get('verification_status', 'unverified')}
confidence: {rec.get('confidence_assessment', 'moderate')}
sources: {sources_str}
updated_at: {rec.get('updated_at', now_iso())}
---

# {subject}

{rec.get('statement', '')}

## Summary
{rec.get('summary', '')}
"""
    write_note_atomic(rel_path, content)

    now = now_iso()
    with db_transaction() as conn:
        conn.execute(
            "UPDATE knowledge_records SET vault_note_path = ?, sync_status = 'synced', updated_at = ? WHERE id = ?",
            (rel_path, now, knowledge_id)
        )
    return rel_path


def sync_vault_to_record(
    rel_path: str,
    project_id: Optional[str] = None
) -> Dict[str, Any]:
    """Read a Markdown vault note and synchronize into structured knowledge records."""
    from app.services.knowledge import read_note

    note = read_note(rel_path)
    if not note:
        raise ValueError(f"Vault note '{rel_path}' not found or could not be read.")

    meta = note.get("meta", {})
    kn_id = meta.get("kn_id")
    title = note.get("title") or meta.get("title") or "Vault Note"
    body = note.get("raw_content", "")
    if "---" in body:
        parts = body.split("---", 2)
        body_text = parts[2].strip() if len(parts) >= 3 else body
    else:
        body_text = body

    if kn_id:
        existing = get_knowledge_record(kn_id)
        if existing:
            now = now_iso()
            # Frontmatter trust_tier is NEVER authoritative for promotion.
            # Preserve existing database trust_tier, reviewed_by, and review_notes.
            db_tier = existing.get("trust_tier") or "captured"
            frontmatter_tier = meta.get("trust_tier")
            sync_status = "synced"

            if frontmatter_tier and frontmatter_tier != db_tier:
                sync_status = f"metadata_conflict: unverified_trust_tier_in_frontmatter ({frontmatter_tier})"
                logger.warning(
                    "SECURITY: Vault note '%s' attempted unverified trust_tier change from '%s' to '%s'; ignored frontmatter tier.",
                    rel_path, db_tier, frontmatter_tier
                )

            # If body content changed, demote verification_status to unverified AND invalidate human review
            verif_status = existing.get("verification_status") or "unverified"
            db_tier = existing.get("trust_tier") or "captured"
            rev_by = existing.get("reviewed_by")
            rev_at = existing.get("reviewed_at")
            rev_notes = existing.get("review_notes") or ""
            if body_text.strip() != (existing.get("statement") or "").strip():
                if verif_status in ("supported", "corroborated"):
                    verif_status = "unverified"
                # Invalidate human review because content was modified
                rev_by = None
                rev_at = None
                rev_notes = ""
                if db_tier in ("reviewed", "reusable"):
                    db_tier = "candidate"
                logger.info("Content changed in vault note '%s'; invalidated prior human review and demoted trust tier.", rel_path)

            with db_transaction() as conn:
                conn.execute(
                    """UPDATE knowledge_records
                       SET statement = ?, subject = ?, verification_status = ?, trust_tier = ?,
                           reviewed_by = ?, reviewed_at = ?, review_notes = ?,
                           vault_note_path = ?, sync_status = ?, updated_at = ?
                       WHERE id = ?""",
                    (body_text, title, verif_status, db_tier, rev_by, rev_at, rev_notes, rel_path, sync_status, now, kn_id)
                )
            return get_knowledge_record(kn_id)  # type: ignore

    # Create new record from vault note:
    # Initial trust_tier is ALWAYS 'captured', regardless of what frontmatter claims!
    frontmatter_tier = meta.get("trust_tier")
    sync_status = "synced" if (not frontmatter_tier or frontmatter_tier == "captured") else f"metadata_conflict: unverified_initial_tier_demoted_to_captured ({frontmatter_tier})"
    created = create_knowledge_record(
        subject=title,
        statement=body_text,
        knowledge_type="fact",
        scope="project" if project_id else "collective",
        project_id=project_id,
        trust_tier="captured",
        vault_note_path=rel_path,
        sync_status=sync_status,
    )
    return created


def audit_vault_synchronization() -> Dict[str, Any]:
    """Audit database records against the Markdown vault filesystem."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT id, subject, vault_note_path, sync_status FROM knowledge_records WHERE vault_note_path IS NOT NULL"
        ).fetchall()
        records = [dict(r) for r in rows]
    finally:
        conn.close()

    synced = []
    missing_disk_files = []
    for r in records:
        path = r.get("vault_note_path")
        if path and (settings.knowledge_dir / path).is_file():
            synced.append(r["id"])
        else:
            missing_disk_files.append({"id": r["id"], "path": path})

    return {
        "total_vault_linked_records": len(records),
        "synced_count": len(synced),
        "missing_file_count": len(missing_disk_files),
        "missing_files": missing_disk_files,
        "status": "healthy" if not missing_disk_files else "drift_detected"
    }
