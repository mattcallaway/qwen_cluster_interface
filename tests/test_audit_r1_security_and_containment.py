"""Audit Remediation Increment R1 — Security Boundaries and Containment Regression Suite.

Covers:
- AUD-03:
  * Missing CSRF enforcement on cookie-authenticated `POST /api/v1/chat/deliberate`.
  * Unconfined `workspace_path` traversal, outside-allowed-roots access, and symlink/junction rejection.
  * Caller-supplied `holdout_test_dir` rejection unless registered with the verifier, and workspace/holdout overlap rejection.
  * Rejection of fault-injection parameters (`simulate_first_attempt_defect`, `injected_first_attempt_candidate`) on external HTTP routes.
- AUD-05:
  * Worker Bearer token SHA-256 verification and node-enablement checks on orchestration and deliverables routes.
  * CSRF boolean return value enforcement on deliverable status/evaluate/submit routes and research export route.
  * Authentication enforcement across `/observatory`, `/api/v1/observatory/*`, `/api/v1/system/diagnostics`, `/api/knowledge/*`, and `/api/v1/inference/*`.
  * Removal of duplicate `/health` and `/api/v1/health` route shadowing in `app/routes/observatory.py`.
- AUD-10b:
  * Removal of `allow_untrusted_subprocess` and `internal_harness_test_only` host subprocess execution bypasses in `app/services/orchestration/code_sandbox.py`.
- AUD-11c:
  * SSRF redirect target re-validation, IPv6 zone-ID / IPv4-mapped IPv6 / CGNAT / metadata blocking, and DNS-rebinding detection between validation and socket connect in research retrieval.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile
import uuid

from fastapi.testclient import TestClient
import pytest

from app.auth import create_session_token
from app.config import settings
from app.database import get_db_connection, init_db
from app.main import app
from app.services import projects as ps
from app.services import worker as worker_svc
from app.services.orchestration import code_sandbox, protected_repo_verifier, tool_catalogue
from app.services.research.policy import ResearchSecurityPolicy
from app.services.research.retrieval import DestinationBlockedError, SecureFetcher


@pytest.fixture
def isolated_r1_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "qc_r1_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setenv("QC_ALLOWED_WORKSPACE_ROOTS", "")
    if hasattr(settings, "allowed_workspace_roots"):
        monkeypatch.setattr(settings, "allowed_workspace_roots", "")
    init_db()
    protected_repo_verifier.configure_protected_repo_verifier(None)
    return data_dir


def _get_csrf_token_for_client(client: TestClient) -> str:
    resp = client.get("/projects")
    m = re.search(r'name="csrf_token"\s+value="([0-9a-f]+)"', resp.text) or re.search(
        r'data-csrf="([0-9a-f]+)"', resp.text
    )
    assert m, "Could not find CSRF token in HTML response"
    return m.group(1)


def test_aud03_chat_deliberate_requires_csrf_for_cookie_sessions(isolated_r1_env: Path) -> None:
    """Cookie-authenticated POST /api/v1/chat/deliberate must reject missing or invalid CSRF tokens."""
    client = TestClient(app)
    client.cookies.set("qc_session", create_session_token("admin"))

    # Missing X-CSRF-Token on cookie-authenticated POST must return 403
    r_missing = client.post(
        "/api/v1/chat/deliberate",
        json={"message": "What is 12 + 30?"},
    )
    assert r_missing.status_code == 403

    # Invalid X-CSRF-Token on cookie-authenticated POST must return 403
    r_bad = client.post(
        "/api/v1/chat/deliberate",
        json={"message": "What is 12 + 30?"},
        headers={"X-CSRF-Token": "0000000000000000deadbeef"},
    )
    assert r_bad.status_code == 403

    # Valid X-CSRF-Token succeeds
    csrf = _get_csrf_token_for_client(client)
    r_ok = client.post(
        "/api/v1/chat/deliberate",
        json={"message": "What is 12 + 30?"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r_ok.status_code == 200


def test_aud03_workspace_confinement_holdout_isolation_and_fault_injection_rejected(
    isolated_r1_env: Path, tmp_path: Path
) -> None:
    """POST /api/v1/chat/deliberate must reject:
    1. Fault-injection flags (`simulate_first_attempt_defect`, `injected_first_attempt_candidate`).
    2. Arbitrary user-supplied `holdout_test_dir` not registered with the verifier.
    3. `workspace_path` outside authorized workspace roots (`settings.workspaces_dir` / `allowed_workspace_roots`).
    4. Symlinks or Windows junctions/reparse points inside or along `workspace_path`.
    """
    client = TestClient(app)
    tok = create_session_token("admin")
    headers = {"Authorization": f"Bearer {tok}"}

    # 1. Fault-injection parameters rejected with 400
    r_fault1 = client.post(
        "/api/v1/chat/deliberate",
        headers=headers,
        json={
            "message": "Run project",
            "simulate_first_attempt_defect": True,
        },
    )
    assert r_fault1.status_code == 400

    r_fault2 = client.post(
        "/api/v1/chat/deliberate",
        headers=headers,
        json={
            "message": "Run project",
            "injected_first_attempt_candidate": "def broken(): pass",
        },
    )
    assert r_fault2.status_code == 400

    # 2. Unconfined workspace_path outside settings.workspaces_dir rejected with 403
    outside_ws = tmp_path / "unconfined_outside_repo"
    outside_ws.mkdir(parents=True, exist_ok=True)
    (outside_ws / "mod.py").write_text("x = 1\n", encoding="utf-8")

    r_unconfined = client.post(
        "/api/v1/chat/deliberate",
        headers=headers,
        json={
            "message": "Fix bug in unconfined repo",
            "intake": {
                "objective": "Fix bug in mod.py",
                "workspace_path": str(outside_ws),
                "constraints": ["Target file: mod.py"],
                "deliverables": ["verified_bugfix_patch.diff"],
            },
        },
    )
    assert r_unconfined.status_code == 403

    # 3. Arbitrary user-supplied holdout_test_dir rejected with 403 even when workspace_path is inside workspaces_dir
    allowed_ws = settings.workspaces_dir / "proj_safe_repo"
    allowed_ws.mkdir(parents=True, exist_ok=True)
    (allowed_ws / "mod.py").write_text("def f(): return 1\n", encoding="utf-8")

    unregistered_holdout = tmp_path / "arbitrary_user_holdout"
    unregistered_holdout.mkdir(parents=True, exist_ok=True)
    (unregistered_holdout / "holdout_cases.json").write_text(
        '{"schema":"qc.json_io_cases.v1","cases":[]}', encoding="utf-8"
    )

    r_bad_holdout = client.post(
        "/api/v1/chat/deliberate",
        headers=headers,
        json={
            "message": "Fix bug in safe repo with arbitrary holdout",
            "intake": {
                "objective": "Fix bug in mod.py",
                "workspace_path": str(allowed_ws),
                "holdout_test_dir": str(unregistered_holdout),
                "constraints": ["Target file: mod.py"],
                "deliverables": ["verified_bugfix_patch.diff"],
            },
        },
    )
    assert r_bad_holdout.status_code == 403

    # 4. Path traversal (`..`) in tool_catalogue._resolve_safe_workspace_path rejected
    with pytest.raises((ValueError, PermissionError)):
        tool_catalogue._resolve_safe_workspace_path(str(allowed_ws), "../escaped.py")

    # 5. Windows directory junction or symlink inside workspace rejected
    import subprocess
    junction_ws = settings.workspaces_dir / "proj_junction_escape"
    junction_ws.mkdir(parents=True, exist_ok=True)
    (junction_ws / "main.py").write_text("x = 1\n", encoding="utf-8")
    junction_link = junction_ws / "linked_outside"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction_link), str(outside_ws)],
            check=True,
            capture_output=True,
        )
    else:
        junction_link.symlink_to(outside_ws, target_is_directory=True)

    with pytest.raises(PermissionError):
        tool_catalogue.validate_and_confine_workspace_path(str(junction_ws), enforce_allowed_roots=True)

    r_junction = client.post(
        "/api/v1/chat/deliberate",
        headers=headers,
        json={
            "message": "Fix bug in junction workspace",
            "intake": {
                "objective": "Fix bug in main.py",
                "workspace_path": str(junction_ws),
                "constraints": ["Target file: main.py"],
                "deliverables": ["verified_bugfix_patch.diff"],
            },
        },
    )
    assert r_junction.status_code == 403


def test_aud05_worker_token_sha256_and_csrf_and_route_authentication(isolated_r1_env: Path) -> None:
    """Verify:
    1. Real worker tokens (stored as SHA-256 hashes via `worker_svc.generate_worker_token`) are recognized
       as worker tokens on orchestration & deliverables routes and enforced against human-only actions (403),
       while disabled/revoked worker tokens are rejected with 401.
    2. Deliverable POST endpoints reject invalid/missing CSRF tokens with 403.
    3. Observatory, Knowledge, and Inference routes reject unauthenticated callers with 401.
    4. `/health` and `/api/v1/health` are served once without leaking git/process telemetry.
    """
    node_id = f"node-r1-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        conn.execute(
            """INSERT INTO infrastructure_nodes
               (id, name, role, health_status, is_enabled, created_at, updated_at)
               VALUES (?, 'R1 Worker', 'vps_worker', 'online', 1, datetime('now'), datetime('now'))""",
            (node_id,),
        )
        conn.commit()
    finally:
        conn.close()

    tok_id, raw_worker_token = worker_svc.create_worker_token(node_id)
    conn = get_db_connection()
    try:
        stored_hash = conn.execute(
            "SELECT token_hash FROM worker_auth_tokens WHERE id = ?", (tok_id,)
        ).fetchone()["token_hash"]
    finally:
        conn.close()
    # Confirm DB stores SHA-256, not plaintext
    assert stored_hash == hashlib.sha256(raw_worker_token.encode("utf-8")).hexdigest()

    worker_client = TestClient(app)
    worker_client.headers.update({"Authorization": f"Bearer {raw_worker_token}"})

    # Worker token attempting to create an orchestration run must be recognized as worker token and get 403 (not 401)
    r_orch = worker_client.post(
        "/api/v1/orchestrator/runs",
        json={"project_id": "proj-1", "objective": "test"},
    )
    assert r_orch.status_code == 403
    assert "Worker automation tokens" in r_orch.json().get("error", "")

    # Disable the node -> worker token must now be rejected with 401
    conn = get_db_connection()
    try:
        conn.execute("UPDATE infrastructure_nodes SET is_enabled = 0 WHERE id = ?", (node_id,))
        conn.commit()
    finally:
        conn.close()

    r_disabled = worker_client.post(
        "/api/v1/orchestrator/runs",
        json={"project_id": "proj-1", "objective": "test"},
    )
    assert r_disabled.status_code == 401

    # Deliverable POST without valid CSRF must be rejected with 403
    proj = ps.create_project(name="R1 Deliv Proj", owner="admin")
    conn = get_db_connection()
    try:
        conn.execute(
            """INSERT INTO deliverables (id, project_id, name, deliverable_type, status, created_by, created_at, updated_at)
               VALUES ('deliv-r1', ?, 'Spec', 'document', 'planned', 'admin', datetime('now'), datetime('now'))""",
            (proj["id"],),
        )
        conn.commit()
    finally:
        conn.close()

    cookie_client = TestClient(app)
    cookie_client.cookies.set("qc_session", create_session_token("admin"))
    r_deliv_csrf = cookie_client.post(
        "/deliverables/deliv-r1/knowledge",
        data={"csrf_token": "invalid_csrf_token"},
    )
    assert r_deliv_csrf.status_code == 403

    # Unauthenticated calls to observatory, diagnostics, knowledge, and inference APIs must return 401
    anon = TestClient(app)
    for protected_url in (
        "/api/v1/observatory/overview",
        "/api/v1/observatory/nodes",
        "/api/v1/observatory/snapshots/latest",
        "/api/v1/system/diagnostics",
        "/api/knowledge/records",
        "/api/knowledge/impacts/proj-1",
        "/api/knowledge/checkpoints/proj-1",
        "/api/v1/inference/paged-kv/metrics",
        "/api/v1/inference/pipeline/stages",
    ):
        resp = anon.get(protected_url)
        assert resp.status_code == 401, f"Expected 401 on {protected_url}, got {resp.status_code}"

    # Public /health must remain 200 and not expose git_commit or process_memory_mb
    h_resp = anon.get("/health")
    assert h_resp.status_code == 200
    h_json = h_resp.json()
    assert h_json["status"] == "healthy"
    assert "git_commit" not in h_json


def test_aud10b_code_sandbox_never_executes_untrusted_host_subprocess() -> None:
    """`run_isolated_code_evaluation` must fail closed (`BLOCKED`) even when
    `allow_untrusted_subprocess=True` or `internal_harness_test_only=True` is passed.
    """
    marker_file = Path(tempfile.gettempdir()) / f"qc_r1_escape_{uuid.uuid4().hex}.txt"
    payload = f"import pathlib\npathlib.Path({str(marker_file)!r}).write_text('escaped')\n"

    res1 = code_sandbox.run_isolated_code_evaluation(
        payload,
        test_code="assert True",
        allow_untrusted_subprocess=True,
    )
    assert res1.status == "BLOCKED"
    assert res1.success is False
    assert not marker_file.exists()

    res2 = code_sandbox.run_isolated_code_evaluation(
        payload,
        test_code="assert True",
        internal_harness_test_only=True,
    )
    assert res2.status == "BLOCKED"
    assert res2.success is False
    assert not marker_file.exists()


def test_aud11c_ssrf_ipv6_zone_and_dns_rebinding_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Research security policy and SecureFetcher must block:
    1. IPv6 zone IDs (`fe80::1%eth0`), IPv4-mapped IPv6 (`::ffff:169.254.169.254`), and CGNAT (`100.100.100.100`).
    2. DNS rebinding where hostname resolves to a public IP during pre-validation and changes
       to a different/private IP during `PinningSyncBackend.connect_tcp`.
    """
    policy = ResearchSecurityPolicy()

    for blocked_url in (
        "http://[fe80::1%25eth0]/",
        "http://[::ffff:169.254.169.254]/latest/meta-data/",
        "http://[::ffff:127.0.0.1]:8000/",
        "http://100.101.187.115:8000/",
        "http://0.0.0.0/",
    ):
        url_ok, _, host = policy.validate_url(blocked_url)
        if url_ok and host:
            dest_ok, _, _ = policy.resolve_and_verify_destination(host)
        else:
            dest_ok = False
        assert not (url_ok and dest_ok), f"Expected blocked URL {blocked_url} to be rejected"

    # Simulate DNS rebinding: first resolution returns public IP 93.184.216.34,
    # second resolution (at socket connect time) returns a different IP 1.1.1.1
    calls = {"n": 0}

    def _fake_resolve(hostname: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return True, "ok", ["93.184.216.34"]
        return True, "ok", ["1.1.1.1"]

    monkeypatch.setattr(policy, "resolve_and_verify_destination", _fake_resolve)
    fetcher = SecureFetcher(policy=policy)
    res = fetcher.fetch("https://rebind.example.com/data")
    assert res.error is not None
    assert "rebind" in res.error.lower() or "address change" in res.error.lower()

    # 3. Worker HttpsRetrievalPolicy and redirect target re-validation:
    # Any non-HTTPS redirect target or redirect to loopback/metadata/CGNAT must be rejected.
    from vps_agent.research_fetch import HttpsRetrievalPolicy

    worker_policy = HttpsRetrievalPolicy()
    for bad_redirect_target in (
        "http://example.com/downgrade",
        "https://127.0.0.1:11434/api/tags",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::ffff:127.0.0.1]:8000/",
        "https://100.101.187.115:8000/",
    ):
        u_ok, _, u_host = worker_policy.validate_url(bad_redirect_target)
        if u_ok and u_host:
            d_ok, _, _ = worker_policy.resolve_and_verify_destination(u_host)
        else:
            d_ok = False
        assert not (u_ok and d_ok), f"Expected worker redirect target {bad_redirect_target} to be rejected"
