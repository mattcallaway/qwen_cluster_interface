"""Unit and regression tests for continuous research and persistent knowledge integration."""
import pytest
from datetime import datetime, timezone, timedelta
from app.database import get_db_connection
from app.services.projects import create_project
from app.services.knowledge_store import (
    create_knowledge_record,
    get_knowledge_record,
    list_knowledge_records,
    search_knowledge_records,
    record_knowledge_impact,
    flag_affected_impacts,
    list_affected_impacts,
    evaluate_freshness,
    propose_promotion,
    evaluate_promotion_eligibility,
    approve_promotion,
    reject_promotion,
    list_promotions,
    record_contradiction,
    supersede_knowledge_record,
)
from app.services.orchestration.research_checkpoint import (
    evaluate_research_need,
    execute_research_checkpoint,
    list_project_checkpoints,
)
from app.services.orchestration.context_manager import build_inference_prompt
from app.services.observatory.analytics import get_research_and_knowledge_metrics


def test_checkpoint_lifecycle_and_creation():
    """Verify research checkpoints are recorded with complete metadata and lifecycle events."""
    proj = create_project(name="Lifecycle Test Proj", owner="tester", objective="Analyze modern ASGI web framework performance")
    project_id = proj["id"]
    
    # 1. Execute checkpoint with no prior knowledge (should search or evaluate need)
    cp = execute_research_checkpoint(
        project_id=project_id,
        stage_phase="intake",
        run_id="exec-001",
        objective="Analyze modern ASGI web framework performance",
    )
    
    assert cp["project_id"] == project_id
    assert cp["stage_phase"] == "intake"
    assert cp["run_id"] == "exec-001"
    assert cp["search_decision"] in ("search_executed", "search_skipped", "degraded_mode")
    assert isinstance(cp["knowledge_queries"], list)
    
    # Verify persisted in database
    checkpoints = list_project_checkpoints(project_id)
    assert len(checkpoints) >= 1
    saved = checkpoints[0]
    assert saved["id"] == cp["id"]
    assert saved["stage_phase"] == "intake"


def test_knowledge_search_before_web_search():
    """Verify existing knowledge is searched first; if fresh and verified, external search is skipped."""
    proj = create_project(name="Reuse Test Proj", owner="tester", objective="Use PEP 621 standard packaging")
    project_id = proj["id"]
    
    # Seed verified knowledge record into this project
    rec = create_knowledge_record(
        subject="Python Packaging Standards",
        statement="PEP 621 specifies how to store project metadata in pyproject.toml.",
        project_id=project_id,
        scope="project",
        knowledge_type="fact",
        verification_status="supported",
        source_ids=["src-pep621"],
        topics=["packaging", "standards"],
    )
    assert rec["id"]
    
    # Evaluate research need for the same topic
    decision = evaluate_research_need(
        project_id=project_id,
        stage_phase="planning",
        objective="Use PEP 621 standard packaging",
    )
    
    # Should skip web search because fresh verified knowledge exists
    assert decision["research_needed"] is False
    assert "existing" in decision["decision_reason"].lower() or "verified" in decision["decision_reason"].lower()
    assert len(decision["existing_records"]) >= 1


def test_project_isolation():
    """Ensure Project A's private knowledge is invisible to Project B, while collective knowledge is shared."""
    proj_a = create_project(name="Project Alpha", owner="tester")["id"]
    proj_b = create_project(name="Project Beta", owner="tester")["id"]
    
    # Project A private record
    rec_a = create_knowledge_record(
        subject="Project Alpha Internal Architecture",
        statement="Alpha internal secret token auth system v2.",
        project_id=proj_a,
        scope="project",
        knowledge_type="fact",
        verification_status="supported",
    )
    
    # Project B private record
    rec_b = create_knowledge_record(
        subject="Project Beta Confidential Design",
        statement="Beta proprietary caching algorithm details.",
        project_id=proj_b,
        scope="project",
        knowledge_type="fact",
        verification_status="supported",
    )
    
    # Collective record
    rec_c = create_knowledge_record(
        subject="Universal HTTP Status Codes",
        statement="HTTP 404 indicates resource not found.",
        scope="collective",
        knowledge_type="fact",
        verification_status="supported",
    )
    
    # Query knowledge for Project A
    results_a = list_knowledge_records(project_id=proj_a, include_collective=True)
    ids_a = {r["id"] for r in results_a}
    assert rec_a["id"] in ids_a
    assert rec_c["id"] in ids_a
    assert rec_b["id"] not in ids_a  # Project B's private record MUST NOT be visible
    
    # Query knowledge for Project B
    results_b = list_knowledge_records(project_id=proj_b, include_collective=True)
    ids_b = {r["id"] for r in results_b}
    assert rec_b["id"] in ids_b
    assert rec_c["id"] in ids_b
    assert rec_a["id"] not in ids_b  # Project A's private record MUST NOT be visible


def test_promotion_governance_and_workflow():
    """Verify promotion proposal, eligibility check, approval elevation, and rejection."""
    proj = create_project(name="Promotion Test Proj", owner="tester")
    project_id = proj["id"]
    
    # Create project record
    rec = create_knowledge_record(
        subject="PostgreSQL JSONB Indexing",
        statement="GIN indexes provide optimal performance for PostgreSQL jsonb containment queries.",
        project_id=project_id,
        scope="project",
        knowledge_type="fact",
        verification_status="supported",
        confidence_assessment="high",
        source_ids=["src-pg-jsonb"],
    )
    
    # 1. Automatic eligibility evaluation
    eligibility = evaluate_promotion_eligibility(rec)
    assert eligibility["eligible"] is True
    
    # 2. Propose promotion
    promo = propose_promotion(
        knowledge_id=rec["id"],
        source_project_id=project_id,
        policy="public_technical",
        rationale="Standard technical finding reusable across projects",
    )
    assert promo["status"] == "proposed"
    
    # Verify listed in promotions
    pending = list_promotions(status="proposed")
    assert any(p["id"] == promo["id"] for p in pending)
    
    # 3. Security check: Plain unauthenticated string reviewer is strictly rejected
    with pytest.raises(PermissionError, match="cannot provide operator authority"):
        approve_promotion(promo["id"], reviewer="admin_architect")

    # 4. Authenticated operator authority approval
    from app.auth import create_session_token, ensure_default_admin
    from datetime import datetime, timezone
    ensure_default_admin()
    admin_token = create_session_token("admin")

    with get_db_connection() as conn:
        conn.execute(
            "UPDATE knowledge_records SET reviewed_by = 'admin', reviewed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), rec["id"])
        )
        conn.commit()

    approved = approve_promotion(promo["id"], session_token=admin_token, rationale="Admin verified architectural finding")
    assert approved["status"] == "approved"
    
    # Record scope must now be 'collective'
    updated_rec = get_knowledge_record(rec["id"])
    assert updated_rec["scope"] == "collective"
    assert updated_rec["promoted_by"] == "admin"


def test_contradiction_detection_and_impact_tracking():
    """Verify that when knowledge is contradicted or superseded, all dependent planning/tasks/deliverables are flagged."""
    proj = create_project(name="Impact Test Proj", owner="tester")
    project_id = proj["id"]
    
    # Create initial knowledge record
    rec1 = create_knowledge_record(
        subject="Python Version Deprecation",
        statement="Python 3.8 is actively supported by security updates.",
        project_id=project_id,
        scope="project",
        knowledge_type="fact",
        verification_status="supported",
    )
    
    # Record knowledge impacts: a planning decision and a deliverable relied on this
    record_knowledge_impact(
        knowledge_id=rec1["id"],
        project_id=project_id,
        usage_type="planning_assumption",
        impact_summary="Target runtime environment supports Python 3.8",
    )
    record_knowledge_impact(
        knowledge_id=rec1["id"],
        project_id=project_id,
        usage_type="deliverable_claim",
        impact_summary="Wheel packaging targets Python 3.8+ compatibility",
    )

    
    # Create superseding knowledge record
    rec2 = create_knowledge_record(
        subject="Python 3.8 End of Life",
        statement="Python 3.8 reached End of Life in October 2024 and no longer receives security fixes.",
        project_id=project_id,
        scope="project",
        knowledge_type="fact",
        verification_status="supported",
    )
    
    # Supersede rec1 with rec2
    supersede_knowledge_record(
        old_knowledge_id=rec1["id"],
        new_knowledge_id=rec2["id"],
        reason="Python 3.8 reached official EOL in Oct 2024",
    )
    
    # Verify old record is marked superseded (outdated in verification_status)
    rec1_updated = get_knowledge_record(rec1["id"])
    assert rec1_updated["verification_status"] == "outdated"
    assert rec1_updated["superseded_by"] == rec2["id"]
    
    # Verify impacts are flagged as affected!
    affected = list_affected_impacts(project_id)
    assert len(affected) == 2
    for aff in affected:
        assert aff["is_affected"] == 1
        assert "superseded" in aff["affected_reason"].lower()


def test_freshness_policy_evaluation():
    """Test TTL freshness evaluation according to knowledge freshness policies."""
    proj = create_project(name="Freshness Test Proj", owner="tester")
    project_id = proj["id"]
    
    # Create a fresh record
    fresh_rec = create_knowledge_record(
        subject="Python Typing Syntax",
        statement="PEP 604 allows X | Y union syntax.",
        project_id=project_id,
        scope="project",
        knowledge_type="fact",
        topics=["software_api"],
    )
    eval_fresh = evaluate_freshness(fresh_rec)
    assert eval_fresh["is_fresh"] is True
    assert eval_fresh["needs_refresh"] is False
    
    # Simulate a stale record by modifying created_at
    conn = get_db_connection()
    old_time = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    conn.execute(
        "UPDATE knowledge_records SET created_at = ?, last_verified_at = ? WHERE id = ?",
        (old_time, old_time, fresh_rec["id"])
    )
    conn.commit()
    conn.close()
    
    stale_rec = get_knowledge_record(fresh_rec["id"])
    # With 14 days TTL for software_api, 200 days old should be stale
    eval_stale = evaluate_freshness(stale_rec)
    assert eval_stale["is_fresh"] is False
    assert eval_stale["needs_refresh"] is True


def test_context_assembly_with_knowledge():
    """Verify build_inference_prompt enriches prompt context with verified knowledge findings without leaking."""
    records = [
        {
            "id": "kn-001",
            "subject": "SQLite WAL Mode",
            "statement": "WAL mode provides higher concurrency for SQLite.",
            "verification_status": "supported",
            "confidence_assessment": "high",
            "source_url": "https://sqlite.org/wal.html",
        }
    ]
    
    prompt = build_inference_prompt(
        instruction="Configure database engine for high concurrency",
        inputs={"task_info": {"title": "Optimize SQLite Concurrency"}},
        knowledge_records=records,
    )
    
    assert "verified_knowledge_base" in prompt
    assert "SQLite WAL Mode" in prompt
    assert "WAL mode provides higher concurrency" in prompt
    assert "https://sqlite.org/wal.html" in prompt


def test_observatory_research_telemetry():
    """Verify observatory calculation of research checkpoints, reuse rate, and knowledge metrics."""
    metrics = get_research_and_knowledge_metrics()
    assert "checkpoints" in metrics
    assert "knowledge_base" in metrics
    assert "promotions" in metrics
    assert "impacts" in metrics
    
    cp = metrics["checkpoints"]
    assert "total" in cp
    assert "reuse_rate_percent" in cp
    assert 0.0 <= cp["reuse_rate_percent"] <= 100.0
    
    kb = metrics["knowledge_base"]
    assert "total_records" in kb
    assert "evidence_coverage_percent" in kb
    assert "research_efficiency" in kb
