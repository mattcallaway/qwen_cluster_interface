"""Stage 7 Acceptance Tests: Knowledge Vault, Review Governance, and Traceable Learning.

Validates:
- F-18: Model Router Dynamic Discovery & Persistent Capability Verification
- Invariant I-02: Feasibility before approval (real verified capabilities)
- Invariant I-07: Honest evidence (strict claim-to-source attribution)
- Invariant I-10: Full lineage and explicit reuse
- Invariant I-11: Knowledge trust tiers (strict promotion lifecycle: Captured -> Candidate -> Corroborated -> Reviewed -> Reusable)
- Contradiction & conflict ledger handling with impact notification
- Bidirectional Markdown vault synchronization and audit
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from app.auth import create_session_token
from app.config import settings
from app.database import get_db_connection
from app.services import projects as ps
from app.services.knowledge_store import (
    create_knowledge_record,
    get_knowledge_record,
    list_knowledge_records,
    transition_trust_tier,
    propose_promotion,
    approve_promotion,
    reject_promotion,
    list_promotions,
    record_knowledge_conflict,
    resolve_knowledge_conflict,
    list_knowledge_conflicts,
    record_contradiction,
    supersede_knowledge_record,
    get_governance_review_queue,
    sync_record_to_vault,
    sync_vault_to_record,
    audit_vault_synchronization,
    record_knowledge_impact,
    list_affected_impacts,
)
from app.services.orchestration.model_router import (
    eligible_nodes,
    verify_node_model_capability,
    node_inventory,
)


import uuid

@pytest.fixture
def test_project():
    """Create a temporary project for testing."""
    return ps.create_project(f"Stage 7 Project {uuid.uuid4().hex[:8]}", owner="operator")


def test_a_knowledge_record_creation_defaults_to_captured_trust_tier(test_project):
    """Test A: Knowledge records default to 'captured' trust tier and reject invalid tiers."""
    rec = create_knowledge_record(
        subject="Default Tier Record",
        statement="Initial factual observation from crawler.",
        project_id=test_project["id"],
        knowledge_type="source_observation",
    )
    assert rec["trust_tier"] == "captured"
    assert rec["sync_status"] == "synced"
    assert rec["review_notes"] == ""

    with pytest.raises(ValueError, match="Invalid trust_tier"):
        create_knowledge_record(
            subject="Invalid Tier",
            statement="Should fail",
            project_id=test_project["id"],
            trust_tier="unauthorized_tier",
        )


def test_b_trust_tier_lifecycle_strict_transitions_enforced(test_project):
    """Test B: Enforce Invariant I-11: Captured -> Candidate -> Corroborated -> Reviewed -> Reusable.
    
    Direct leaps (e.g. Captured -> Reusable) are rejected.
    """
    rec = create_knowledge_record(
        subject="Python 3.12 GIL Features",
        statement="Python 3.12 introduces per-interpreter GIL support.",
        project_id=test_project["id"],
        source_ids=["src-1", "src-2"],
        verification_status="supported",
    )
    kid = rec["id"]
    assert rec["trust_tier"] == "captured"

    # Direct leap from captured to reusable must fail
    with pytest.raises(ValueError, match="Invalid trust tier transition"):
        transition_trust_tier(kid, target_tier="reusable")

    # captured -> candidate succeeds
    c1 = transition_trust_tier(kid, target_tier="candidate")
    assert c1["trust_tier"] == "candidate"

    # candidate -> corroborated succeeds with multiple sources
    c2 = transition_trust_tier(kid, target_tier="corroborated")
    assert c2["trust_tier"] == "corroborated"

    admin_tok = create_session_token("admin")
    # Transition to reviewed without reviewer or notes must fail
    with pytest.raises(ValueError, match="Reviewer identity is required"):
        transition_trust_tier(kid, target_tier="reviewed", reviewer="", notes="looks good")
    with pytest.raises(ValueError, match="Review rationale/notes are required"):
        transition_trust_tier(kid, target_tier="reviewed", session_token=admin_tok, notes="")

    # candidate/corroborated -> reviewed with credentials succeeds
    c3 = transition_trust_tier(
        kid,
        target_tier="reviewed",
        session_token=admin_tok,
        notes="Verified against official Python 3.12 release notes.",
    )
    assert c3["trust_tier"] == "reviewed"
    assert c3["reviewed_by"] == "admin"
    assert "release notes" in c3["review_notes"]

    # reviewed -> reusable succeeds
    c4 = transition_trust_tier(kid, target_tier="reusable", session_token=admin_tok)
    assert c4["trust_tier"] == "reusable"


def test_c_corroboration_requires_multiple_sources_or_deterministic_proof(test_project):
    """Test C: Corroboration requires multiple independent sources or deterministic verification."""
    rec = create_knowledge_record(
        subject="Single Source Claim",
        statement="A solitary claim with only one source.",
        project_id=test_project["id"],
        source_ids=["src-single"],
        verification_method="heuristic",
    )
    transition_trust_tier(rec["id"], target_tier="candidate")

    # Attempting to corroborate with only 1 heuristic source fails
    with pytest.raises(ValueError, match="requires at least 2 independent sources"):
        transition_trust_tier(rec["id"], target_tier="corroborated")

    # But with deterministic verification method, corroboration passes
    rec_det = create_knowledge_record(
        subject="Deterministic Proof Claim",
        statement="Proved via deterministic test execution.",
        project_id=test_project["id"],
        source_ids=["src-single"],
        verification_method="deterministic",
    )
    transition_trust_tier(rec_det["id"], target_tier="candidate")
    c_det = transition_trust_tier(rec_det["id"], target_tier="corroborated")
    assert c_det["trust_tier"] == "corroborated"


def test_d_private_credentials_prohibit_reusable_promotion(test_project):
    """Test D: Private data and credentials cannot be promoted to Reusable."""
    rec = create_knowledge_record(
        subject="Internal Database Credential",
        statement="The production database password is secret_token_xyz123.",
        project_id=test_project["id"],
        source_ids=["src-1", "src-2"],
        verification_status="supported",
        access_policy="collective_shareable",
    )
    transition_trust_tier(rec["id"], target_tier="candidate")
    transition_trust_tier(rec["id"], target_tier="corroborated")
    transition_trust_tier(rec["id"], target_tier="reviewed", session_token=create_session_token("admin"), notes="Audit check")

    with pytest.raises(ValueError, match="indicators of private, credential, or confidential data"):
        transition_trust_tier(rec["id"], target_tier="reusable")


def test_e_contradiction_detection_demotes_trust_tier_and_logs_conflict(test_project):
    """Test E: Contradiction detection demotes trust tier, sets status contradicted, and logs conflict."""
    rec_a = create_knowledge_record(
        subject="API Rate Limit",
        statement="The external API limit is 100 requests per minute.",
        project_id=test_project["id"],
        source_ids=["src-1", "src-2"],
        verification_status="supported",
        access_policy="collective_shareable",
    )
    transition_trust_tier(rec_a["id"], "candidate")
    transition_trust_tier(rec_a["id"], "corroborated")
    admin_tok = create_session_token("admin")
    transition_trust_tier(rec_a["id"], "reviewed", session_token=admin_tok, notes="Verified in 2025")
    transition_trust_tier(rec_a["id"], "reusable", session_token=admin_tok)
    assert get_knowledge_record(rec_a["id"])["trust_tier"] == "reusable"

    # Register an impact
    record_knowledge_impact(
        knowledge_id=rec_a["id"],
        project_id=test_project["id"],
        usage_type="planning_assumption",
        impact_summary="Planned rate limiting queue around 100 rpm",
    )

    rec_b = create_knowledge_record(
        subject="API Rate Limit Updated",
        statement="The external API limit is 50 requests per minute as of October 2026.",
        project_id=test_project["id"],
        source_ids=["src-3"],
    )

    # Record contradiction between A and B
    cid = record_knowledge_conflict(
        rec_a["id"],
        rec_b["id"],
        conflict_type="direct_contradiction",
        details="Official documentation reduced rate limit from 100 to 50 rpm.",
    )
    assert cid.startswith("cnf-")

    # Record A must be demoted and marked contradicted
    updated_a = get_knowledge_record(rec_a["id"])
    assert updated_a["verification_status"] == "contradicted"
    assert updated_a["trust_tier"] == "candidate"  # demoted from reusable!
    assert updated_a["contradicts"] == rec_b["id"]

    # Affected impact must be flagged
    affected = list_affected_impacts(test_project["id"])
    assert len(affected) >= 1
    assert any(a["knowledge_id"] == rec_a["id"] for a in affected)

    # Contradicted record cannot be promoted to reusable
    with pytest.raises(ValueError, match="Cannot promote contradicted knowledge record"):
        transition_trust_tier(rec_a["id"], "reusable")


def test_f_conflict_resolution_and_supersession(test_project):
    """Test F: Conflict resolution with supersession updates losing and winning records."""
    rec_old = create_knowledge_record(
        subject="Architecture Pattern",
        statement="Use monolithic polling scheduler.",
        project_id=test_project["id"],
        source_ids=["src-old"],
        verification_status="supported",
    )
    rec_new = create_knowledge_record(
        subject="Architecture Pattern",
        statement="Use bounded ThreadPoolExecutor scheduler offload.",
        project_id=test_project["id"],
        source_ids=["src-new"],
        verification_status="supported",
    )

    cid = record_knowledge_conflict(
        rec_old["id"],
        rec_new["id"],
        conflict_type="version_supersession",
        details="Stage 3 execution offload superseded synchronous scheduler.",
    )

    # Resolve conflict in favor of rec_new
    resolved = resolve_knowledge_conflict(
        conflict_id=cid,
        resolution_status="resolved_superseded",
        resolved_by="architect-bob",
        resolution_notes="Adopted Stage 3 asynchronous execution model.",
        winning_record_id=rec_new["id"],
    )
    assert resolved["status"] == "resolved_superseded"
    assert resolved["resolved_by"] == "architect-bob"

    old_after = get_knowledge_record(rec_old["id"])
    new_after = get_knowledge_record(rec_new["id"])

    assert old_after["superseded_by"] == rec_new["id"]
    assert old_after["verification_status"] == "outdated"
    assert new_after["verification_status"] == "supported"


def test_g_governance_review_queue_aggregates_work_items(test_project):
    """Test G: Operator review queue aggregates pending promotions, unreviewed candidates, and conflicts."""
    rec = create_knowledge_record(
        subject="Review Queue Test",
        statement="Candidate item waiting for review.",
        project_id=test_project["id"],
        source_ids=["src-1"],
        verification_status="supported",
    )
    transition_trust_tier(rec["id"], "candidate")

    # Propose for promotion
    prm = propose_promotion(
        knowledge_id=rec["id"],
        source_project_id=test_project["id"],
        policy="public_technical",
        rationale="Candidate technical finding",
    )
    assert prm["status"] == "proposed"

    queue = get_governance_review_queue(project_id=test_project["id"])
    assert queue["total_pending"] >= 1
    assert any(p["id"] == prm["id"] for p in queue["pending_promotions"])
    assert any(c["id"] == rec["id"] for c in queue["unreviewed_candidates"])

    # Human operator review followed by promotion approval
    admin_tok = create_session_token("admin")
    transition_trust_tier(rec["id"], "reviewed", session_token=admin_tok, notes="Operator verified candidate finding.")
    approve_promotion(prm["id"], session_token=admin_tok, rationale="Approved for collective repository")
    rec_approved = get_knowledge_record(rec["id"])
    assert rec_approved["scope"] == "collective"
    assert rec_approved["trust_tier"] == "reusable"
    assert rec_approved["reviewed_by"] == "admin"


def test_h_bidirectional_markdown_vault_synchronization(test_project, tmp_path):
    """Test H: Bidirectional sync between SQLite knowledge records and Markdown vault notes."""
    rec = create_knowledge_record(
        subject="Synchronized Vault Finding",
        statement="Detailed technical findings to be synchronized into Markdown vault note.",
        summary="A concise summary of findings.",
        project_id=test_project["id"],
        source_ids=["https://example.org/spec"],
        verification_status="supported",
        confidence_assessment="high",
        trust_tier="corroborated",
    )

    # 1. Sync DB record -> Vault Markdown note
    rel_path = sync_record_to_vault(rec["id"], category="research", project_id=test_project["id"])
    assert rel_path.endswith(".md")
    assert rel_path.startswith("projects/")

    vault_file = settings.knowledge_dir / rel_path
    assert vault_file.is_file()
    content = vault_file.read_text(encoding="utf-8")
    assert "kn_id: " + rec["id"] in content
    assert "trust_tier: corroborated" in content
    assert "Synchronized Vault Finding" in content

    # 2. Audit vault synchronization
    audit = audit_vault_synchronization()
    assert audit["status"] == "healthy"
    assert audit["missing_file_count"] == 0

    # 3. Modify note on disk and sync back: Vault -> DB
    # Frontmatter attempts to elevate trust_tier to 'reviewed'
    modified_content = content.replace("Detailed technical findings", "Updated technical findings from human editor")
    modified_content = modified_content.replace("trust_tier: corroborated", "trust_tier: reviewed")
    vault_file.write_text(modified_content, encoding="utf-8")

    synced_rec = sync_vault_to_record(rel_path, project_id=test_project["id"])
    assert synced_rec["id"] == rec["id"]
    assert "Updated technical findings" in synced_rec["statement"]
    # Frontmatter elevation is untrusted and rejected: trust_tier remains corroborated in DB
    assert synced_rec["trust_tier"] == "corroborated"
    assert "metadata_conflict" in synced_rec["sync_status"]


def test_i_model_router_persistent_capability_verification_f18():
    """Test I (Finding F-18): Model Router verifies capability probes in database rather than trusting tags."""
    conn = get_db_connection()
    try:
        # Register a test node in infrastructure_nodes
        conn.execute(
            """INSERT OR REPLACE INTO infrastructure_nodes (
                id, name, role, is_enabled, health_status, installed_runtimes_json,
                registered_services_json, available_models_json, available_ram_mb,
                created_at, updated_at
            ) VALUES (
                'node-unverified-gpu', 'Unverified GPU Node', 'inference_node', 1, 'online',
                '["python","ollama"]', '[]', '["qwen2.5:7b"]', 8192,
                datetime('now'), datetime('now')
            )"""
        )
        conn.execute("INSERT OR REPLACE INTO worker_auth_tokens (id, node_id, token_hash, token_prefix, created_at, revoked) VALUES ('tok-test', 'node-unverified-gpu', 'hash_test', 'pfx', datetime('now'), 0)")
        conn.commit()
    finally:
        conn.close()

    nodes = node_inventory()
    target_node = next((n for n in nodes if n["id"] == "node-unverified-gpu"), None)
    assert target_node is not None

    task = {
        "action": {"type": "inference", "model": "qwen2.5:7b"},
        "allowed_nodes": ["node-unverified-gpu"],
    }

    # When enforce_verified=True without probe: must be rejected as unverified
    res_unverified = eligible_nodes(task, [target_node], enforce_verified=True)
    assert len(res_unverified["eligible"]) == 0
    assert any("unverified model capability" in r for r in res_unverified["reasons"])

    # Run verification probe
    verif = verify_node_model_capability(
        node_id="node-unverified-gpu",
        model="qwen2.5:7b",
        verified_by="test_daemon",
        passed=True,
        details="Model weights checksum matched, test inference latency 42ms",
    )
    assert verif["verified"] is True

    # Now with verified probe in database: node becomes eligible!
    res_verified = eligible_nodes(task, [target_node], enforce_verified=True)
    assert len(res_verified["eligible"]) == 1
    assert res_verified["eligible"][0][0]["id"] == "node-unverified-gpu"
    assert res_verified["eligible"][0][1] == "qwen2.5:7b"


def test_j_f18_knowledge_trust_tiers_e2e(test_project):
    """Test J: Full End-to-End Promotion Pipeline (Captured -> Candidate -> Corroborated -> Reviewed -> Reusable)."""
    # 1. Captured: Extracted from research
    rec = create_knowledge_record(
        subject="SQLite WAL Mode Concurrency",
        statement="PRAGMA journal_mode=WAL enables concurrent readers alongside a single writer.",
        summary="WAL mode overview",
        project_id=test_project["id"],
        source_ids=["https://sqlite.org/wal.html", "https://sqlite.org/pragma.html"],
        verification_status="supported",
        confidence_assessment="high",
        verification_method="deterministic",
    )
    assert rec["trust_tier"] == "captured"
    assert rec["scope"] == "project"

    # 2. Candidate: Structured with claim spans and links
    r_cand = transition_trust_tier(rec["id"], "candidate")
    assert r_cand["trust_tier"] == "candidate"

    # 3. Corroborated: Verified against multiple sources
    r_corr = transition_trust_tier(rec["id"], "corroborated")
    assert r_corr["trust_tier"] == "corroborated"

    # 4. Operator Review: Human signoff
    admin_tok = create_session_token("admin")
    r_rev = transition_trust_tier(
        rec["id"],
        "reviewed",
        session_token=admin_tok,
        notes="Empirically validated with concurrent reader test bench.",
    )
    assert r_rev["trust_tier"] == "reviewed"
    assert r_rev["reviewed_by"] == "admin"

    # 5. Promotion to Reusable in Collective Scope
    prm = propose_promotion(
        rec["id"],
        source_project_id=test_project["id"],
        policy="public_technical",
        rationale="Generalizable SQLite concurrency rule",
    )
    assert prm["status"] == "proposed"

    approved = approve_promotion(
        prm["id"],
        session_token=create_session_token("admin"),
        rationale="Promoted to collective engineering standards",
    )
    assert approved["status"] == "approved"

    final_rec = get_knowledge_record(rec["id"])
    assert final_rec["scope"] == "collective"
    assert final_rec["trust_tier"] == "reusable"
    assert final_rec["reviewed_by"] == "admin"
