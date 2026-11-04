"""Dedicated tests for Stage 7 Security Gate Repairs and Operator Authorization Trust Boundary.

Validates:
1. Operator Authority Trust Boundary (Section 3 Cases A through H):
   - Case A: Arbitrary caller constructs OperatorAuthority with forged privileges -> denied with PermissionError.
   - Case B: Unauthenticated caller supplies existing administrator username -> denied with PermissionError.
   - Case C: Logged-in ordinary user attempts forced promotion -> denied with PermissionError.
   - Case D: Authenticated administrator with required permission executes authorized transition -> succeeds with durable audit record.
   - Case E: Trusted internal service principal can perform only its specifically granted operations.
   - Case F: Contradicted records remain protected by mandatory conflict-resolution requirements unconditionally.
   - Case G: Forged reviewer names cannot create human approval records.
   - Case H: Markdown vault synchronization cannot bypass the corrected authorization boundary.
2. Defect 4: Model capability fail-closed scheduling across scheduling call sites.
3. Defect 5: Sensitive data governance and multi-vector content scanning.
"""
import json
import uuid
import pytest
from pathlib import Path

from app.config import settings
from app.database import db_transaction, get_db_connection
from app.auth import create_session_token, ensure_default_admin, hash_password
from app.services import projects as ps
from app.services.knowledge_store import (
    create_knowledge_record,
    get_knowledge_record,
    transition_trust_tier,
    sync_record_to_vault,
    sync_vault_to_record,
    record_knowledge_conflict,
    OperatorAuthority,
    scan_sensitive_content,
    create_operator_authority_from_session,
    create_internal_service_authority,
    propose_promotion,
    approve_promotion,
    get_promotion,
)
from app.services.orchestration.model_router import (
    eligible_nodes,
    select_node,
    verify_node_model_capability,
)


@pytest.fixture
def test_project():
    """Create a temporary project for security gate testing."""
    return ps.create_project(f"SecGate Project {uuid.uuid4().hex[:8]}", owner="operator")


# ---------------------------------------------------------------------------
# Section 3: Required Security Tests (Cases A through H)
# ---------------------------------------------------------------------------

def test_case_a_unauthorized_operator_authority_construction_denied(test_project):
    """Case A: An arbitrary caller constructs OperatorAuthority(is_operator=True, session_verified=True, can_force_transition=True) and is denied privileged promotion."""
    rec = create_knowledge_record(
        subject="Arbitrary Construction Fact",
        statement="A statement where caller tries to fabricate authority.",
        project_id=test_project["id"],
        source_ids=["src-1"],
    )
    kid = rec["id"]

    # 1. Caller constructs OperatorAuthority with forged privileges directly
    forged_auth = OperatorAuthority(
        principal="mallory",
        is_operator=True,
        session_verified=True,
        can_force_transition=True,
        audit_reason="Malicious forced override",
    )

    # Must be rejected fail-closed with PermissionError due to signature mismatch
    with pytest.raises(PermissionError, match="Invalid, unverified, or forged OperatorAuthority signature"):
        transition_trust_tier(kid, target_tier="reusable", force=True, authority=forged_auth)

    # 2. Forged authority claiming to be 'admin' also fails
    forged_admin = OperatorAuthority(
        principal="admin",
        is_operator=True,
        session_verified=True,
        can_force_transition=True,
        audit_reason="Forged admin override",
    )
    with pytest.raises(PermissionError, match="Invalid, unverified, or forged OperatorAuthority signature"):
        transition_trust_tier(kid, target_tier="reusable", force=True, authority=forged_admin)


def test_case_b_unauthenticated_caller_supplying_admin_username_denied(test_project):
    """Case B: An unauthenticated caller supplies an existing administrator's username and is denied."""
    rec = create_knowledge_record(
        subject="Admin Identity Spoofing Fact",
        statement="Caller claims to be administrator by passing plain string.",
        project_id=test_project["id"],
        source_ids=["src-1", "src-2"],
        verification_status="supported",
    )
    kid = rec["id"]
    transition_trust_tier(kid, "candidate")
    transition_trust_tier(kid, "corroborated")

    # 1. Passing reviewer="admin" without session token is denied
    with pytest.raises(PermissionError, match="Plain username string 'admin' cannot provide operator authority"):
        transition_trust_tier(kid, target_tier="reviewed", reviewer="admin", notes="Spoofed review notes")

    # 2. Passing authority="admin" without session token is denied
    with pytest.raises(PermissionError, match="Plain username string 'admin' cannot provide operator authority"):
        transition_trust_tier(kid, target_tier="reusable", force=True, authority="admin", audit_reason="Spoofed admin override")

    # 3. Approving promotion with reviewer="admin" without session token is denied
    prm = propose_promotion(kid, source_project_id=test_project["id"], policy="public_technical", rationale="Test proposal")
    with pytest.raises(PermissionError, match="cannot provide operator authority"):
        approve_promotion(prm["id"], reviewer="admin", rationale="Spoofed approval")


def test_case_c_logged_in_ordinary_user_denied_forced_promotion(test_project):
    """Case C: A logged-in ordinary user attempts a forced promotion and is denied."""
    # Create ordinary non-admin user in database
    username = f"user_{uuid.uuid4().hex[:6]}"
    with db_transaction() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, datetime('now'))",
            (username, hash_password("Password123!"))
        )
    user_token = create_session_token(username)

    rec = create_knowledge_record(
        subject="Ordinary User Promotion",
        statement="A claim that should not be force-promoted by an ordinary user.",
        project_id=test_project["id"],
        source_ids=["src-1"],
    )
    kid = rec["id"]

    # Session authority is derived, but user lacks admin/force privileges
    user_auth = create_operator_authority_from_session(user_token)
    assert user_auth.principal == username
    assert user_auth.is_operator is False
    assert user_auth.can_force_transition is False

    # Attempting forced promotion with this user's token fails closed
    with pytest.raises(PermissionError, match="Operator session unverified or invalid for force_transition"):
        transition_trust_tier(kid, target_tier="reusable", force=True, session_token=user_token, audit_reason="Ordinary user override")

    # Attempting forced promotion passing the derived authority directly also fails closed
    with pytest.raises(PermissionError, match="Operator session unverified or invalid for force_transition"):
        transition_trust_tier(kid, target_tier="reusable", force=True, authority=user_auth, audit_reason="Ordinary user override")


def test_case_d_authenticated_admin_with_permission_authorized_transition(test_project):
    """Case D: An authenticated administrator with the required permission can perform an explicitly authorized transition."""
    ensure_default_admin()
    admin_token = create_session_token("admin")

    rec = create_knowledge_record(
        subject="Admin Authorized Transition",
        statement="Emergency configuration adjustment requiring forced transition.",
        project_id=test_project["id"],
        source_ids=["src-1"],
        access_policy="collective_shareable",
    )
    kid = rec["id"]
    assert rec["trust_tier"] == "captured"

    # Admin with valid session token and audit reason executes forced transition
    res = transition_trust_tier(
        kid,
        target_tier="candidate",
        force=True,
        session_token=admin_token,
        audit_reason="Authorized emergency operational adjustment by lead operator.",
    )
    assert res["trust_tier"] == "candidate"

    # Durable audit record must exist in knowledge_tier_transitions table
    conn = get_db_connection()
    try:
        row = conn.execute(
            """SELECT * FROM knowledge_tier_transitions
               WHERE record_id = ? AND actor_principal = 'admin' AND authorization_basis = 'forced_override'
               ORDER BY transitioned_at DESC LIMIT 1""",
            (kid,)
        ).fetchone()
        assert row is not None
        assert row["previous_tier"] == "captured"
        assert row["new_tier"] == "candidate"
        assert "Authorized emergency operational adjustment" in row["reason"]
    finally:
        conn.close()


def test_case_e_trusted_internal_service_principal_constrained_to_granted_operations(test_project):
    """Case E: A trusted internal service principal can perform only its specifically granted operations."""
    rec = create_knowledge_record(
        subject="Service Principal Test Fact",
        statement="Service principal performs scoped operations.",
        project_id=test_project["id"],
        source_ids=["src-1", "src-2"],
        verification_status="supported",
    )
    kid = rec["id"]
    transition_trust_tier(kid, "candidate")

    # 1. Granted operation 'corroborate' on 'qc:service:knowledge_migrator' succeeds
    migrator_auth = create_internal_service_authority(
        service_principal="qc:service:knowledge_migrator",
        operation="corroborate",
        service_key=settings.secret_key,
        audit_reason="Automated vault migration corroboration",
    )
    res = transition_trust_tier(
        kid,
        target_tier="corroborated",
        authority=migrator_auth,
        notes="Automated migration corroboration signoff",
    )
    assert res["trust_tier"] == "corroborated"

    # 2. Ungranted operation 'review' is denied for knowledge_migrator (review is human-only!)
    with pytest.raises(PermissionError, match="is not granted operation 'review'"):
        create_internal_service_authority(
            service_principal="qc:service:knowledge_migrator",
            operation="review",
            service_key=settings.secret_key,
            audit_reason="Unauthorized review attempt",
        )

    # 3. Ungranted operation 'force_transition' is denied for knowledge_migrator
    with pytest.raises(PermissionError, match="is not granted operation 'force_transition'"):
        create_internal_service_authority(
            service_principal="qc:service:knowledge_migrator",
            operation="force_transition",
            service_key=settings.secret_key,
            audit_reason="Unauthorized force attempt",
        )

    # 4. Invalid service key is denied
    with pytest.raises(PermissionError, match="Invalid service secret key"):
        create_internal_service_authority(
            service_principal="qc:service:knowledge_migrator",
            operation="propose",
            service_key="wrong-secret-key-123",
        )

    # 5. Unregistered service principal is denied
    with pytest.raises(PermissionError, match="Unknown internal service principal"):
        create_internal_service_authority(
            service_principal="qc:service:unregistered_bot",
            operation="propose",
            service_key=settings.secret_key,
        )


def test_case_f_contradicted_records_remain_protected_unconditionally(test_project):
    """Case F: Contradicted records remain protected by their existing mandatory conflict-resolution requirements."""
    admin_token = create_session_token("admin")
    rec = create_knowledge_record(
        subject="Contradicted Claim",
        statement="A claim that has been falsified.",
        project_id=test_project["id"],
        source_ids=["src-1"],
        verification_status="contradicted",
    )
    kid = rec["id"]

    # Even an authenticated administrator with force=True cannot promote a contradicted record
    with pytest.raises(ValueError, match="Cannot promote contradicted knowledge record; resolve contradiction first."):
        transition_trust_tier(
            kid,
            target_tier="corroborated",
            force=True,
            session_token=admin_token,
            audit_reason="Attempted override of contradicted fact",
        )

    with pytest.raises(ValueError, match="Cannot promote contradicted knowledge record; resolve contradiction first."):
        transition_trust_tier(
            kid,
            target_tier="reviewed",
            force=True,
            session_token=admin_token,
            audit_reason="Attempted override of contradicted fact",
        )

    with pytest.raises(ValueError, match="Cannot promote contradicted knowledge record; resolve contradiction first."):
        transition_trust_tier(
            kid,
            target_tier="reusable",
            force=True,
            session_token=admin_token,
            audit_reason="Attempted override of contradicted fact",
        )


def test_case_g_forged_reviewer_names_cannot_create_human_approval(test_project):
    """Case G: Forged reviewer names cannot create human approval records."""
    rec = create_knowledge_record(
        subject="Forged Reviewer Target",
        statement="Statement awaiting legitimate human review.",
        project_id=test_project["id"],
        source_ids=["src-1", "src-2"],
        verification_status="supported",
    )
    kid = rec["id"]
    transition_trust_tier(kid, "candidate")
    transition_trust_tier(kid, "corroborated")

    # 1. Attacker attempts to forge review signature with arbitrary name
    with pytest.raises(PermissionError, match="cannot provide operator authority"):
        transition_trust_tier(
            kid,
            target_tier="reviewed",
            reviewer="LeadArchitect_Alice",
            notes="Faked approval from Alice",
        )

    # 2. Attacker attempts to forge review signature claiming to be 'operator'
    with pytest.raises(PermissionError, match="cannot provide operator authority"):
        transition_trust_tier(
            kid,
            target_tier="reviewed",
            reviewer="operator",
            notes="Faked operator approval",
        )

    # Verify database remains untainted: record is still corroborated, not reviewed
    current = get_knowledge_record(kid)
    assert current["trust_tier"] == "corroborated"
    assert current["reviewed_by"] is None


def test_case_h_markdown_vault_sync_cannot_bypass_authorization_boundary(test_project):
    """Case H: Markdown vault synchronization cannot bypass the corrected authorization boundary."""
    rec = create_knowledge_record(
        subject="Vault Boundary Spec",
        statement="Security critical specification note.",
        project_id=test_project["id"],
        source_ids=["https://example.com/spec"],
        verification_status="supported",
        trust_tier="candidate",
    )
    rel_path = sync_record_to_vault(rec["id"], project_id=test_project["id"])
    vault_file = settings.knowledge_dir / rel_path
    assert vault_file.is_file()

    # Tamper with file to forge review attribution and elevated trust tier
    tampered_note = f"""---
title: Vault Boundary Spec
kn_id: {rec['id']}
trust_tier: reusable
reviewed_by: forged_human_auditor
verification_status: corroborated
---

# Vault Boundary Spec

Tampered content from filesystem sync.
"""
    vault_file.write_text(tampered_note, encoding="utf-8")

    # Sync back into database
    synced = sync_vault_to_record(rel_path, project_id=test_project["id"])

    # Trust tier MUST NOT be elevated to reusable; stays at candidate!
    assert synced["trust_tier"] == "candidate"
    assert synced["reviewed_by"] is None or synced["reviewed_by"] != "forged_human_auditor"
    assert "metadata_conflict" in synced["sync_status"]
    # Verification status is safely demoted to unverified due to content changes
    assert synced["verification_status"] == "unverified"


# ---------------------------------------------------------------------------
# Defect 4: Model Capability Fail-Closed Scheduling
# ---------------------------------------------------------------------------

def test_defect4_model_capability_fail_closed_scheduling(test_project):
    """Defect 4: Unverified model capabilities fail closed; verified probes permit scheduling."""
    node_id = f"node-{uuid.uuid4().hex[:6]}"
    model_name = "qwen2.5:7b-test"

    # Register an inference node with no verification probe
    conn = get_db_connection()
    try:
        conn.execute(
            """INSERT INTO infrastructure_nodes (
                id, name, role, is_enabled, health_status, installed_runtimes_json,
                available_models_json, available_ram_mb, created_at, updated_at
            ) VALUES (?, ?, 'inference_node', 1, 'online', '["python-3.12", "ollama"]', ?, 16000, datetime('now'), datetime('now'))""",
            (node_id, node_id, json.dumps([model_name]))
        )
        conn.execute(
            "INSERT INTO worker_auth_tokens (id, node_id, token_hash, token_prefix, created_at, revoked) VALUES (?, ?, ?, 'tst', datetime('now'), 0)",
            (uuid.uuid4().hex, node_id, uuid.uuid4().hex)
        )
        conn.commit()
    finally:
        conn.close()

    mock_node = {
        "id": node_id,
        "name": node_id,
        "role": "inference_node",
        "is_worker": True,
        "enabled": True,
        "authenticated": True,
        "health": "online",
        "capabilities": ["inference"],
        "models": [model_name],
        "available_ram_mb": 16000,
        "load": 0,
    }
    task = {"action": {"type": "inference", "model": model_name}}

    # 1. Default scheduling (enforce_verified=True) must FAIL CLOSED
    res_default = eligible_nodes(task, [mock_node])
    assert len(res_default["eligible"]) == 0
    assert any("unverified model capability" in r for r in res_default["reasons"])
    assert any("probe_missing" in r for r in res_default["reasons"])

    # 2. select_node also fails closed
    n_sel, m_sel, reasons = select_node(task, [mock_node])
    assert n_sel is None
    assert any("unverified model capability" in r for r in reasons)

    # 3. Record a failed probe
    verify_node_model_capability(node_id, model_name, passed=False, details="Model OOM during probe")
    res_failed = eligible_nodes(task, [mock_node])
    assert len(res_failed["eligible"]) == 0
    assert any("failed" in r for r in res_failed["reasons"])

    # 4. Now execute verified capability probe -> scheduling SUCCEEDS
    verify_node_model_capability(node_id, model_name, passed=True, details="Probe passed at 28.5 tps")
    res_passed = eligible_nodes(task, [mock_node])
    assert len(res_passed["eligible"]) == 1
    assert res_passed["eligible"][0][0]["id"] == node_id
    assert res_passed["eligible"][0][1] == model_name

    # select_node also succeeds
    n_ok, m_ok, _ = select_node(task, [mock_node])
    assert n_ok["id"] == node_id
    assert m_ok == model_name


# ---------------------------------------------------------------------------
# Defect 5: Strengthened Sensitive Data Governance & Access Classification
# ---------------------------------------------------------------------------

def test_defect5_sensitive_data_governance_and_access_classification(test_project):
    """Defect 5: Classification controls and multi-vector scanning protect private data."""
    admin_token = create_session_token("admin")

    # 1. Private project classification blocks collective promotion
    rec_private = create_knowledge_record(
        subject="Project Architecture Secret",
        statement="This is internal project notes with no external exposure permitted.",
        project_id=test_project["id"],
        source_ids=["src-1", "src-2"],
        verification_status="supported",
        access_policy="private_project",
    )
    transition_trust_tier(rec_private["id"], "candidate")
    transition_trust_tier(rec_private["id"], "corroborated")
    transition_trust_tier(rec_private["id"], "reviewed", session_token=admin_token, notes="Reviewed for project use")

    with pytest.raises(ValueError, match="private project knowledge cannot cross into collective 'reusable'"):
        transition_trust_tier(rec_private["id"], "reusable")

    # 2. Database Connection URI with credentials blocked
    rec_uri = create_knowledge_record(
        subject="Database Target URI",
        statement="Connect to PostgreSQL at postgresql://app_user:SuperSecretP@ss123@db.prod.internal:5432/main_db",
        project_id=test_project["id"],
        source_ids=["src-1", "src-2"],
        verification_status="supported",
        access_policy="collective_shareable",
    )
    transition_trust_tier(rec_uri["id"], "candidate")
    transition_trust_tier(rec_uri["id"], "corroborated")
    transition_trust_tier(rec_uri["id"], "reviewed", session_token=admin_token, notes="Checked config")
    with pytest.raises(ValueError, match="Contains indicators of private, credential, or confidential data"):
        transition_trust_tier(rec_uri["id"], "reusable")

    # 3. Bearer Token / JWT blocked
    rec_jwt = create_knowledge_record(
        subject="Service Token",
        statement="Use Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotShareThisSignature",
        project_id=test_project["id"],
        source_ids=["src-1", "src-2"],
        verification_status="supported",
        access_policy="collective_shareable",
    )
    transition_trust_tier(rec_jwt["id"], "candidate")
    transition_trust_tier(rec_jwt["id"], "corroborated")
    transition_trust_tier(rec_jwt["id"], "reviewed", session_token=admin_token, notes="Checked auth")
    with pytest.raises(ValueError, match="Contains indicators of private, credential, or confidential data"):
        transition_trust_tier(rec_jwt["id"], "reusable")

    # 4. Cryptographic Private Key blocked
    rec_key = create_knowledge_record(
        subject="SSH Host Key",
        statement="Cluster key block:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0Y...\n-----END RSA PRIVATE KEY-----",
        project_id=test_project["id"],
        source_ids=["src-1", "src-2"],
        verification_status="supported",
        access_policy="collective_shareable",
    )
    transition_trust_tier(rec_key["id"], "candidate")
    transition_trust_tier(rec_key["id"], "corroborated")
    transition_trust_tier(rec_key["id"], "reviewed", session_token=admin_token, notes="Checked key")
    with pytest.raises(ValueError, match="Contains indicators of private, credential, or confidential data"):
        transition_trust_tier(rec_key["id"], "reusable")

    # 5. Financial / Social Security PII blocked
    rec_pii = create_knowledge_record(
        subject="Customer Identity Benchmark",
        statement="Sample test record uses SSN 000-12-3456 for synthetic identity testing.",
        project_id=test_project["id"],
        source_ids=["src-1", "src-2"],
        verification_status="supported",
        access_policy="collective_shareable",
    )
    transition_trust_tier(rec_pii["id"], "candidate")
    transition_trust_tier(rec_pii["id"], "corroborated")
    transition_trust_tier(rec_pii["id"], "reviewed", session_token=admin_token, notes="Checked PII")
    with pytest.raises(ValueError, match="Contains indicators of private, credential, or confidential data"):
        transition_trust_tier(rec_pii["id"], "reusable")

    # 6. Valid, non-sensitive, classified shareable record succeeds
    rec_clean = create_knowledge_record(
        subject="Algorithm Complexity",
        statement="Dijkstra with a Fibonacci heap achieves O(E + V log V) time complexity.",
        project_id=test_project["id"],
        source_ids=["src-1", "src-2"],
        verification_status="supported",
        access_policy="collective_shareable",
    )
    transition_trust_tier(rec_clean["id"], "candidate")
    transition_trust_tier(rec_clean["id"], "corroborated")
    transition_trust_tier(rec_clean["id"], "reviewed", session_token=admin_token, notes="Audited for public reuse")
    res_reusable = transition_trust_tier(rec_clean["id"], "reusable", session_token=admin_token)
    assert res_reusable["trust_tier"] == "reusable"

    # Original records remain intact in their permitted private scope
    orig = get_knowledge_record(rec_private["id"])
    assert orig["trust_tier"] == "reviewed"
    assert orig["access_policy"] == "private_project"
    assert orig["project_id"] == test_project["id"]


# ---------------------------------------------------------------------------
# Human Review Integrity & Environment Isolation (Corrections 1 & 2)
# ---------------------------------------------------------------------------

def test_correction1_service_cannot_elevate_unreviewed_record_into_reusable(test_project):
    """Correction 1: A service principal (even with mock review+promote permissions) cannot independently elevate an unreviewed record into Reusable."""
    from app.services.knowledge_store import TRUSTED_SERVICE_PRINCIPALS
    
    test_svc = "qc:service:mock_dual_permission_service"
    TRUSTED_SERVICE_PRINCIPALS[test_svc] = {
        "granted_operations": ("review", "promote", "force_transition"),
        "can_force": True,
        "is_operator": True,
    }
    try:
        service_auth = create_internal_service_authority(
            service_principal=test_svc,
            operation="review",
            service_key=settings.secret_key,
            audit_reason="Service automated review attempt",
        )
        assert service_auth.is_human is False

        rec = create_knowledge_record(
            subject="Unreviewed Candidate Fact",
            statement="Factual claim with no human review.",
            project_id=test_project["id"],
            source_ids=["src-1", "src-2"],
            verification_status="supported",
            access_policy="collective_shareable",
        )
        kid = rec["id"]
        transition_trust_tier(kid, "candidate")
        transition_trust_tier(kid, "corroborated")

        # 1. Service principal CANNOT transition to 'reviewed' (human review requirement)
        with pytest.raises(PermissionError, match="Only an authenticated, authorized human operator session can transition knowledge into the 'reviewed' tier"):
            transition_trust_tier(kid, target_tier="reviewed", authority=service_auth, notes="Automated service review")

        # 2. Service principal CANNOT elevate unreviewed record into 'reusable'
        promote_auth = create_internal_service_authority(
            service_principal=test_svc,
            operation="promote",
            service_key=settings.secret_key,
            audit_reason="Service promotion attempt",
        )
        with pytest.raises(PermissionError, match="Mandatory human review requirement"):
            transition_trust_tier(kid, target_tier="reusable", authority=promote_auth)

        # 3. force_transition CANNOT bypass mandatory human-review requirement into 'reusable'
        force_auth = create_internal_service_authority(
            service_principal=test_svc,
            operation="force_transition",
            service_key=settings.secret_key,
            audit_reason="Service forced promotion attempt",
        )
        with pytest.raises(PermissionError, match="Forced transition to 'reusable' is strictly prohibited without prior authenticated human operator review"):
            transition_trust_tier(kid, target_tier="reusable", force=True, authority=force_auth, audit_reason="Forced service bypass")

        # 4. Proposing and approving promotion on unreviewed record is also denied
        prm = propose_promotion(kid, source_project_id=test_project["id"], policy="public_technical", rationale="Proposal")
        with pytest.raises(PermissionError, match="must have verified prior human operator review before collective promotion can be approved"):
            approve_promotion(prm["id"], authority=promote_auth, rationale="Service approval attempt")

        # Record in DB remains unreviewed in corroborated tier
        db_rec = get_knowledge_record(kid)
        assert db_rec["trust_tier"] == "corroborated"
        assert db_rec["reviewed_by"] is None
    finally:
        TRUSTED_SERVICE_PRINCIPALS.pop(test_svc, None)


def test_correction1_subsequent_content_modification_invalidates_human_review(test_project):
    """Correction 1: Subsequent content modifications or contradictions invalidate human review before promotion to Reusable."""
    admin_tok = create_session_token("admin")
    rec = create_knowledge_record(
        subject="Modifiable Specification",
        statement="Original statement verified by human auditor.",
        project_id=test_project["id"],
        source_ids=["src-1", "src-2"],
        verification_status="supported",
        access_policy="collective_shareable",
    )
    kid = rec["id"]
    transition_trust_tier(kid, "candidate")
    transition_trust_tier(kid, "corroborated")
    transition_trust_tier(kid, "reviewed", session_token=admin_tok, notes="Verified original statement.")
    
    rev_rec = get_knowledge_record(kid)
    assert rev_rec["trust_tier"] == "reviewed"
    assert rev_rec["reviewed_by"] == "admin"

    # Sync to vault note
    rel_path = sync_record_to_vault(kid, project_id=test_project["id"])
    vault_file = settings.knowledge_dir / rel_path
    assert vault_file.is_file()

    # Modify content of note on disk
    vault_file.write_text(vault_file.read_text(encoding="utf-8").replace("Original statement", "Tampered statement after review"), encoding="utf-8")

    # Sync vault back to record
    synced = sync_vault_to_record(rel_path, project_id=test_project["id"])

    # Human review MUST BE invalidated!
    assert synced["trust_tier"] == "candidate"  # demoted from reviewed
    assert synced["reviewed_by"] is None
    assert synced["verification_status"] == "unverified"

    # Attempting to promote without fresh human review fails
    with pytest.raises(ValueError, match="Invalid trust tier transition from 'candidate' to 'reusable'"):
        transition_trust_tier(kid, target_tier="reusable", session_token=admin_tok)

    # Even forced transition to reusable fails because human review was invalidated
    with pytest.raises(PermissionError, match="Mandatory human review requirement"):
        transition_trust_tier(
            kid,
            target_tier="reusable",
            force=True,
            session_token=admin_tok,
            audit_reason="Attempting to force promote invalidated record",
        )


def test_correction2_test_harness_runtime_environment_isolation():
    """Correction 2: qc:service:test_harness cannot obtain authority in production mode, but is permitted in test/development mode."""
    orig_env = settings.environment
    try:
        # Negative test: In production mode, test_harness is strictly denied
        settings.environment = "production"
        with pytest.raises(PermissionError, match="Production security restriction: Test harness principal 'qc:service:test_harness' cannot obtain authority in production"):
            create_internal_service_authority(
                service_principal="qc:service:test_harness",
                operation="promote",
                service_key=settings.secret_key,
                audit_reason="Production attempt",
            )

        # Positive test: In test/development mode, test_harness can obtain authority
        settings.environment = "development"
        auth = create_internal_service_authority(
            service_principal="qc:service:test_harness",
            operation="promote",
            service_key=settings.secret_key,
            audit_reason="Test development run",
        )
        assert auth.principal == "qc:service:test_harness"
        assert auth.is_human is False
        assert auth.signature != ""
    finally:
        settings.environment = orig_env
