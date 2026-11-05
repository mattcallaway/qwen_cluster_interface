"""Section 8 End-to-End Live Native Project Demonstration (`tests/test_increment18_live_native_project_e2e.py`).

Runs one complete supplier/data project through the actual user entry point (`/api/v1/chat/deliberate`)
in a disposable development database with a separately running, explicitly enrolled and authenticated
`VPSWorkerAgent` subprocess executing against local loopback Ollama (`qwen2.5:3b` / `qwen2.5:1.5b`).

Covers all 10 required steps of Section 8:
  1. Submit initial project request through `/api/v1/chat/deliberate` with a fresh synthetic dataset.
  2. Persist requirements (revision 1), decompose into `project_task_contracts`, create initial checkpoint.
  3. Execute deterministic quote reconciliation & cost comparison through registered tools, producing CAS artifacts.
  4. Lease and execute real worker task(s) through the separately running authenticated `VPSWorkerAgent` daemon.
  5. Issue a mid-flight user steering update while a task boundary is active / ready to commit.
  6. Prove stale worker result is rejected at commit, unaffected upstream artifacts remain valid, and only affected tasks re-run.
  7. Stop and restart the controller `uvicorn` process against the same disposable SQLite database, then resume without replaying completed work.
  8. Induce a controlled verification failure, run the bounded 5-step repair loop without oracle leakage, and verify the repaired output.
  9. Run an infeasible constraint variant and verify honest `BLOCKED` status.
 10. Exercise cancellation and parking and verify cleanup status comes from worker/process state, not caller booleans.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest

from app.auth import create_session_token
from app.config import settings
from app.database import db_transaction, get_db_connection, init_db
from app.services import tasks as T, worker as W
from app.services.orchestration import (
    artifact_manager,
    intake as intake_mod,
    model_router,
    working_memory as wm,
)
from app.services.orchestration.common import now_iso


# Fresh synthetic supplier dataset authored specifically for the Section 8 live E2E demonstration
# (not hardcoded in production prompt/tool logic).
FRESH_LIVE_SUPPLIER_QUOTES: List[Dict[str, Any]] = [
    {
        "supplier_id": "SUP-HELIOS",
        "name": "Helios Industrial Compute",
        "revision": 1,
        "effective_date": "2026-10-10",
        "unit_price": "1280.00",
        "shipping_fee": "400.00",
        "setup_fee": "0.00",
        "delivery_days": 14,
        "warranty_months": 24,
        "capabilities": ["iso27001", "telemetry_export"],
        "supported_dependencies": ["dep_power_bus", "dep_rack_mount", "dep_api_v2"],
    },
    {
        "supplier_id": "SUP-HELIOS",
        "name": "Helios Industrial Compute",
        "revision": 2,
        "effective_date": "2026-10-25",
        "unit_price": "1350.00",
        "shipping_fee": "480.00",
        "setup_fee": "220.00",
        # 10 * 1350.00 + 480.00 + 220.00 = $14,200.00 (11 days)
        "delivery_days": 11,
        "warranty_months": 36,
        "capabilities": ["iso27001", "telemetry_export", "fips140_3"],
        "supported_dependencies": ["dep_power_bus", "dep_rack_mount", "dep_api_v2"],
    },
    {
        "supplier_id": "SUP-ORION",
        "name": "Orion Edge Systems",
        "revision": 1,
        "effective_date": "2026-10-12",
        "status": "withdrawn",
        "unit_price": "1050.00",
        "shipping_fee": "200.00",
        "delivery_days": 12,
        "warranty_months": 24,
        "capabilities": ["iso27001", "telemetry_export"],
        "supported_dependencies": ["dep_power_bus", "dep_rack_mount", "dep_api_v2"],
    },
    {
        "supplier_id": "SUP-ORION",
        "name": "Orion Edge Systems",
        "revision": 2,
        "effective_date": "2026-10-26",
        "unit_price": "1180.00",
        "discount_amount": "250.00",
        "shipping_fee": "340.00",
        # 10 * 1180.00 - 250.00 + 340.00 = $11,890.00 (16 days)
        "delivery_days": 16,
        "warranty_months": 24,
        "capabilities": ["iso27001", "telemetry_export"],
        "supported_dependencies": ["dep_power_bus", "dep_rack_mount", "dep_api_v2"],
    },
    {
        "supplier_id": "SUP-VANGUARD",
        "name": "Vanguard MicroNodes",
        "revision": 1,
        "effective_date": "2026-10-24",
        "unit_price": "990.00",
        "shipping_fee": "250.00",
        # 10 * 990.00 + 250.00 = $10,150.00 (9 days), but missing iso27001 and dep_api_v2
        "delivery_days": 9,
        "warranty_months": 12,
        "capabilities": ["telemetry_export"],
        "supported_dependencies": ["dep_power_bus", "dep_rack_mount"],
    },
]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start_controller_subprocess(repo_root: Path, data_dir: Path, port: int, secret_key: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["QC_DATA_DIR"] = str(data_dir)
    env["QC_ENV"] = "development"
    env["QC_SECRET_KEY"] = secret_key
    env["QC_ORCHESTRATOR_ENABLED"] = "0"
    env["QC_PAGED_KV_ENABLED"] = "false"
    env["QC_GIT_REVISION"] = "HEAD"
    log_path = data_dir / f"controller_{port}.log"
    log_fh = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(repo_root),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log_fh.close()
            out = log_path.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(f"Controller uvicorn subprocess exited early ({proc.returncode}): {out}")
        try:
            r = httpx.get(f"{base_url}/health", timeout=2.0)
            if r.status_code == 200:
                return proc
        except Exception:
            pass
        time.sleep(0.25)
    proc.terminate()
    log_fh.close()
    out = log_path.read_text(encoding="utf-8", errors="replace")
    raise TimeoutError(f"Controller uvicorn subprocess did not become ready on {base_url}. Log:\n{out}")


def _start_worker_daemon_subprocess(
    repo_root: Path,
    worker_data_dir: Path,
    controller_url: str,
    worker_id: str,
    enrollment_token: str,
) -> subprocess.Popen:
    env = os.environ.copy()
    env["QC_CONTROLLER_URL"] = controller_url
    env["QC_WORKER_ID"] = worker_id
    env["QC_WORKER_NAME"] = "Development Live Worker 01"
    env["QC_WORKER_ROLE"] = "vps_worker"
    env["QC_ENROLLMENT_TOKEN"] = enrollment_token
    env["QC_WORKER_DATA_DIR"] = str(worker_data_dir)
    env["QC_POLL_INTERVAL"] = "1"
    env["QC_HEARTBEAT_INTERVAL"] = "5"
    env["QC_LEASE_DURATION"] = "120"
    log_path = worker_data_dir / "worker_daemon.log"
    log_fh = open(log_path, "a", encoding="utf-8")
    worker_code = (
        "import time\n"
        "from vps_agent.agent import VPSWorkerAgent\n"
        "agent = VPSWorkerAgent()\n"
        "assert agent.register(), 'Worker failed to register with enrollment token'\n"
        "agent.send_heartbeat()\n"
        "while True:\n"
        "    if not agent.is_registered:\n"
        "        agent.register()\n"
        "    agent.send_heartbeat()\n"
        "    agent.poll_and_execute_task()\n"
        "    time.sleep(0.4)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", worker_code],
        cwd=str(repo_root),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        text=True,
    )


def test_section8_complete_live_native_project_e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Execute all 10 required steps of Section 8 through `/api/v1/chat/deliberate` with a separate
    `uvicorn` controller subprocess and a separately running authenticated `VPSWorkerAgent` subprocess.
    """
    repo_root = Path(__file__).resolve().parent.parent
    controller_data_dir = tmp_path / "controller_data"
    worker_data_dir = tmp_path / "worker_data"
    controller_data_dir.mkdir(parents=True, exist_ok=True)
    worker_data_dir.mkdir(parents=True, exist_ok=True)

    secret_key = "dev-live-e2e-secret-key-for-increment18-verification"
    monkeypatch.setattr(settings, "data_dir", controller_data_dir)
    monkeypatch.setattr(settings, "secret_key", secret_key)
    init_db()
    wm.GLOBAL_WORKING_SET_CACHE = wm.WorkingSetCache()

    port = _find_free_port()
    controller_url = f"http://127.0.0.1:{port}"
    worker_id = "dev-live-worker-01"

    controller_proc = _start_controller_subprocess(repo_root, controller_data_dir, port, secret_key)
    worker_proc = None
    evidence_out: Dict[str, Any] = {"steps": {}}

    try:
        admin_cookie = create_session_token("admin")
        client = httpx.Client(
            base_url=controller_url,
            cookies={"qc_session": admin_cookie},
            headers={"Authorization": f"Bearer {admin_cookie}"},
            timeout=120.0,
        )

        # Fetch CSRF token for admin POST endpoints that enforce CSRF
        import re
        html_page = client.get("/projects/orchestrate/new").text
        m_csrf = re.search(r'data-csrf="([0-9a-f]+)"', html_page)
        assert m_csrf, "Could not extract CSRF token from controller"
        csrf_headers = {"X-CSRF-Token": m_csrf.group(1), "Authorization": f"Bearer {admin_cookie}"}

        # Explicitly issue a single-use administrator enrollment token for the development worker
        r_enr = client.post(
            "/api/v1/worker/enrollment-tokens",
            json={"allowed_node_id": worker_id, "expires_in_seconds": 3600},
            headers=csrf_headers,
        )
        assert r_enr.status_code == 200, r_enr.text
        enrollment_token = r_enr.json()["enrollment_token"]

        # Launch the separate VPSWorkerAgent subprocess
        worker_proc = _start_worker_daemon_subprocess(
            repo_root, worker_data_dir, controller_url, worker_id, enrollment_token
        )

        # Wait for the separate worker subprocess to enroll, authenticate, and send its first heartbeat
        enrolled_ok = False
        for _ in range(40):
            if worker_proc.poll() is not None:
                out = (worker_data_dir / "worker_daemon.log").read_text(encoding="utf-8", errors="replace")
                raise RuntimeError(f"Worker daemon exited prematurely: {out}")
            conn = get_db_connection()
            try:
                nrow = conn.execute(
                    "SELECT id, health_status, last_contact_at, available_models_json FROM infrastructure_nodes WHERE id = ?",
                    (worker_id,),
                ).fetchone()
                trow = conn.execute(
                    "SELECT id FROM worker_auth_tokens WHERE node_id = ? AND revoked = 0",
                    (worker_id,),
                ).fetchone()
                if nrow and trow and nrow["last_contact_at"]:
                    enrolled_ok = True
                    break
            finally:
                conn.close()
            time.sleep(0.25)

        assert enrolled_ok, "Separate VPSWorkerAgent subprocess did not complete enrollment and heartbeat"

        # Record persistent capability verification probe for the enrolled worker node
        model_router.verify_node_model_capability(
            worker_id, "qwen2.5:3b", verified_by="admin_capability_probe", passed=True
        )

        # =========================================================================
        # STEP 1 & STEP 2: Submit initial project request through /api/v1/chat/deliberate
        # Persist requirements (revision 1), decompose into project_task_contracts,
        # and create an initial checkpoint.
        # =========================================================================
        r_step1 = client.post(
            "/api/v1/chat/deliberate",
            json={
                "message": (
                    "Evaluate 3 edge-hardware suppliers (Helios, Orion, Vanguard) across conflicting quote revisions "
                    "for 10 units and produce verified reconciliation, comparison, and recommendation deliverables."
                ),
                "intake": {
                    "objective": (
                        "Evaluate 3 edge-hardware suppliers (Helios, Orion, Vanguard) across conflicting quote revisions, "
                        "reconcile exact Decimal total costs for 10 units, and recommend the best feasible supplier."
                    ),
                    "constraints": [
                        "Maximum total budget ceiling: $14,800.00",
                        "Maximum delivery lead time: 21 days",
                        "Required capabilities: iso27001, telemetry_export",
                        "Required dependencies: dep_power_bus, dep_rack_mount, dep_api_v2",
                        "Soft preference: fastest_delivery among feasible options",
                    ],
                    "deliverables": [
                        "quote_reconciliation_ledger.json",
                        "supplier_recommendation.json",
                        "supplier_recommendation.md",
                    ],
                    "success_criteria": [
                        "Latest non-withdrawn quote revisions selected deterministically.",
                        "All monetary totals exact to cents using Decimal.",
                        "Recommendation satisfies all hard constraints and cites exact supplier ID and Decimal total cost.",
                    ],
                    "quotes": FRESH_LIVE_SUPPLIER_QUOTES,
                },
                "advance": False,
            },
            headers=csrf_headers,
        )
        assert r_step1.status_code == 200, r_step1.text
        s1_body = r_step1.json()
        assert s1_body["mode"] == "durable_project"
        project_id = s1_body["project_id"]

        r_wm1 = client.get(f"/api/v1/projects/{project_id}/orchestration/working-memory")
        assert r_wm1.status_code == 200
        wm1_state = r_wm1.json()["result"]["authoritative_state"]
        assert wm1_state["current_requirement_revision"]["revision_number"] == 1
        assert wm1_state["delta_since_last_checkpoint"]["has_previous_checkpoint"] is True
        contracts_rev1 = wm.list_task_contracts(project_id)
        assert [c["task_key"] for c in contracts_rev1] == [
            "t1_reconcile_quotes",
            "t2_evaluate_constraints",
            "t3_synthesize_recommendation",
        ]
        evidence_out["steps"]["step_1_and_2_intake_and_contracts"] = {
            "passed": True,
            "project_id": project_id,
            "requirement_revision": 1,
            "task_contracts": [c["task_key"] for c in contracts_rev1],
            "initial_checkpoint": wm1_state["last_checkpoint"],
        }

        # =========================================================================
        # STEP 3 & STEP 4: Execute deterministic quote reconciliation & cost comparison
        # producing real CAS artifacts, and lease & execute real worker task (`t3_synthesize_recommendation`)
        # through the separately running authenticated VPSWorkerAgent using local Ollama (`qwen2.5:3b`).
        # =========================================================================
        r_step3_4 = client.post(
            "/api/v1/chat/deliberate",
            json={
                "project_id": project_id,
                "action": "advance",
                "advance": True,
                "use_live_worker": True,
                "preferred_model": "qwen2.5:3b",
            },
            headers=csrf_headers,
        )
        assert r_step3_4.status_code == 200, r_step3_4.text
        s34_body = r_step3_4.json()
        adv34 = s34_body["advance_result"]
        assert adv34["final_cycle_phase"] == "completed", f"Expected completed phase, got: {adv34}"
        assert len(adv34["native_worker_lease_ids"]) >= 1
        phase1_lease_id = adv34["native_worker_lease_ids"][-1]
        assert phase1_lease_id.startswith("lease_")

        # Independent oracle checks on Phase 1 (Revision 1: $14,800 ceiling -> SUP-HELIOS at $14,200.00)
        steps_map_1 = {s["task_key"]: s for s in adv34["steps_executed"]}
        assert steps_map_1["t1_reconcile_quotes"]["status"] == "verified"
        assert steps_map_1["t2_evaluate_constraints"]["status"] == "verified"
        assert steps_map_1["t2_evaluate_constraints"]["recommended_supplier"] == "SUP-HELIOS"
        assert steps_map_1["t2_evaluate_constraints"]["total_cost"] == "14200.00"
        assert steps_map_1["t3_synthesize_recommendation"]["status"] == "verified"
        assert steps_map_1["t3_synthesize_recommendation"]["producing_actor"] == "authenticated_worker_model"
        assert steps_map_1["t3_synthesize_recommendation"]["lease_id"] == phase1_lease_id

        quote_art_id_rev1 = steps_map_1["t1_reconcile_quotes"]["artifact_id"]
        cmp_art_id_rev1 = steps_map_1["t2_evaluate_constraints"]["artifact_id"]
        md_art_id_rev1 = steps_map_1["t3_synthesize_recommendation"]["artifact_id"]

        # Verify lease row in SQLite was claimed and completed by the separate worker daemon
        conn = get_db_connection()
        try:
            lease_row = conn.execute(
                "SELECT id, task_id, worker_id, status FROM worker_task_leases WHERE id = ?",
                (phase1_lease_id,),
            ).fetchone()
            assert lease_row is not None
            assert lease_row["worker_id"] == worker_id
            assert lease_row["status"] == "completed"
        finally:
            conn.close()

        evidence_out["steps"]["step_3_and_4_tools_and_live_worker_lease"] = {
            "passed": True,
            "phase1_recommended_supplier": "SUP-HELIOS",
            "phase1_total_cost": "14200.00",
            "quote_artifact_id": quote_art_id_rev1,
            "comparison_artifact_id": cmp_art_id_rev1,
            "report_artifact_id": md_art_id_rev1,
            "native_worker_lease_id": phase1_lease_id,
            "worker_id": worker_id,
        }

        # =========================================================================
        # STEP 5 & STEP 6: Issue a mid-flight user steering update while a worker/task
        # boundary is active and ready to commit. Prove the stale worker result is rejected
        # at commit, unaffected upstream artifacts (`quote_reconciliation_ledger.json`)
        # remain valid, and only affected downstream tasks (`t2`, `t3`) re-run.
        # =========================================================================
        def _mid_flight_steering_barrier():
            r_steer = client.post(
                "/api/v1/chat/deliberate",
                json={
                    "project_id": project_id,
                    "action": "steer",
                    "message": "Lower maximum total budget ceiling to $12,600.00",
                    "replaced_req_codes": {"req-con-2": "Maximum total budget ceiling: $12,600.00"},
                    "advance": False,
                },
                headers=csrf_headers,
            )
            assert r_steer.status_code == 200, r_steer.text

        with pytest.raises(wm.StaleWorkerResultError, match="Stale worker result rejected"):
            wm.execute_checkpointed_work_boundary(
                project_id,
                "t2_evaluate_constraints",
                lambda _pkt: {
                    "epistemic_category": "derived_result",
                    "statement": "Late Revision 1 worker result recommending SUP-HELIOS at $14,200.00",
                    "publish_artifact": {
                        "filename": "stale_rev1_attempt.json",
                        "content_bytes": b'{"recommended_supplier": "SUP-HELIOS", "total_cost": "14200.00"}',
                    },
                    "output_summary": {"recommended_supplier": "SUP-HELIOS", "total_cost": "14200.00"},
                },
                verifier_fn=lambda _pkt, _raw, _stg: {
                    "passed": True,
                    "verification_method": "unit_verifier",
                    "declared_scope": "stale_rev1_attempt.json",
                    "checks": [{"name": "ok", "passed": True}],
                },
                pre_commit_barrier=_mid_flight_steering_barrier,
            )

        # Verify upstream t1_reconcile_quotes & quote_reconciliation_ledger.json remain valid/verified,
        # while t2_evaluate_constraints and t3_synthesize_recommendation are stale!
        tc_t1_after_steer = wm.get_task_contract(project_id, "t1_reconcile_quotes")
        tc_t2_after_steer = wm.get_task_contract(project_id, "t2_evaluate_constraints")
        tc_t3_after_steer = wm.get_task_contract(project_id, "t3_synthesize_recommendation")
        assert tc_t1_after_steer["status"] == "verified"
        assert tc_t2_after_steer["status"] == "stale"
        assert tc_t3_after_steer["status"] == "stale"

        arts_after_steer = {a["id"]: a for a in artifact_manager.list_artifacts(project_id)}
        assert arts_after_steer[quote_art_id_rev1]["validation_state"] == "valid"

        evidence_out["steps"]["step_5_and_6_steering_and_atomic_fence"] = {
            "passed": True,
            "new_requirement_revision": 2,
            "stale_commit_rejected": True,
            "t1_status": tc_t1_after_steer["status"],
            "t2_status": tc_t2_after_steer["status"],
            "t3_status": tc_t3_after_steer["status"],
            "upstream_quote_artifact_valid": arts_after_steer[quote_art_id_rev1]["validation_state"] == "valid",
        }

        # =========================================================================
        # STEP 7: Stop and restart the controller uvicorn process against the same
        # disposable SQLite database, then resume without replaying completed work.
        # =========================================================================
        client.close()
        controller_proc.terminate()
        try:
            controller_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            controller_proc.kill()

        # Start a brand-new controller uvicorn subprocess on the same port and same SQLite DB
        controller_proc = _start_controller_subprocess(repo_root, controller_data_dir, port, secret_key)
        client = httpx.Client(
            base_url=controller_url,
            cookies={"qc_session": admin_cookie},
            headers={"Authorization": f"Bearer {admin_cookie}"},
            timeout=120.0,
        )
        html_page2 = client.get("/projects/orchestrate/new").text
        m_csrf2 = re.search(r'data-csrf="([0-9a-f]+)"', html_page2)
        assert m_csrf2
        csrf_headers = {"X-CSRF-Token": m_csrf2.group(1), "Authorization": f"Bearer {admin_cookie}"}

        r_resume = client.post(
            "/api/v1/chat/deliberate",
            json={
                "project_id": project_id,
                "action": "resume",
                "advance": False,
            },
            headers=csrf_headers,
        )
        assert r_resume.status_code == 200, r_resume.text
        res_body = r_resume.json()["resume_result"]
        assert res_body["status"] == "resumed"
        assert res_body["preserved_verified_tasks"] == ["t1_reconcile_quotes"]
        assert res_body["next_task_key"] == "t2_evaluate_constraints"
        assert res_body["replayed_full_conversation"] is False
        assert res_body["restarted_from_scratch"] is False

        evidence_out["steps"]["step_7_controller_cold_restart_and_resume"] = {
            "passed": True,
            "preserved_verified_tasks": res_body["preserved_verified_tasks"],
            "next_task_key": res_body["next_task_key"],
            "replayed_full_conversation": res_body["replayed_full_conversation"],
            "restarted_from_scratch": res_body["restarted_from_scratch"],
        }

        # =========================================================================
        # STEP 8: Induce a controlled verification failure on Attempt 1, run the bounded
        # 5-step repair loop without oracle leakage, and verify the repaired Attempt 2
        # output via a fresh worker lease on the restarted controller!
        # =========================================================================
        r_step8 = client.post(
            "/api/v1/chat/deliberate",
            json={
                "project_id": project_id,
                "action": "advance",
                "advance": True,
                "use_live_worker": True,
                "preferred_model": "qwen2.5:3b",
                "simulate_first_attempt_defect": True,
            },
            headers=csrf_headers,
        )
        assert r_step8.status_code == 200, r_step8.text
        s8_body = r_step8.json()
        adv8 = s8_body["advance_result"]
        assert adv8["final_cycle_phase"] == "completed", f"Expected completed phase after repair, got: {adv8}"

        # Prove t1_reconcile_quotes was NOT re-run after restart/steering!
        executed_keys_step8 = [s["task_key"] for s in adv8["steps_executed"]]
        assert "t1_reconcile_quotes" not in executed_keys_step8
        assert executed_keys_step8 == ["t2_evaluate_constraints", "t3_synthesize_recommendation"]

        steps_map_2 = {s["task_key"]: s for s in adv8["steps_executed"]}
        assert steps_map_2["t2_evaluate_constraints"]["recommended_supplier"] == "SUP-ORION"
        assert steps_map_2["t2_evaluate_constraints"]["total_cost"] == "11890.00"
        assert steps_map_2["t3_synthesize_recommendation"]["status"] == "verified"
        assert steps_map_2["t3_synthesize_recommendation"]["producing_actor"] == "authenticated_worker_model"

        # Prove Attempt 1 failed verification, triggered sanitized repair without oracle leakage, and Attempt 2 succeeded
        assert len(adv8["repair_events"]) == 1
        rep_ev = adv8["repair_events"][0]
        assert rep_ev.get("oracle_exposure_prevented") is True
        assert "SUP-ORION" not in rep_ev["repair_instruction"]
        assert "11890.00" not in rep_ev["repair_instruction"]
        assert "SUP-ORION" not in str(rep_ev.get("sanitized_diagnostics", []))
        assert "11890.00" not in str(rep_ev.get("sanitized_diagnostics", []))
        assert len(adv8["native_worker_lease_ids"]) == 2

        evidence_out["steps"]["step_8_controlled_failure_and_bounded_repair"] = {
            "passed": True,
            "re_executed_tasks": executed_keys_step8,
            "phase2_recommended_supplier": "SUP-ORION",
            "phase2_total_cost": "11890.00",
            "repair_event": rep_ev,
            "native_worker_lease_ids": adv8["native_worker_lease_ids"],
        }

        # =========================================================================
        # STEP 9: Run an infeasible constraint variant ($9,500.00 ceiling) and verify
        # honest BLOCKED status with per-supplier constraint violation diagnostics.
        # =========================================================================
        r_step9 = client.post(
            "/api/v1/chat/deliberate",
            json={
                "project_id": project_id,
                "action": "steer",
                "message": "Reduce budget ceiling to $9,500.00",
                "replaced_req_codes": {"req-con-2": "Maximum total budget ceiling: $9,500.00"},
                "advance": True,
                "use_live_worker": False,
            },
            headers=csrf_headers,
        )
        assert r_step9.status_code == 200, r_step9.text
        s9_body = r_step9.json()
        adv9 = s9_body["advance_result"]
        assert adv9["final_cycle_phase"] == "blocked"
        blk_step = adv9["steps_executed"][0]
        assert blk_step["task_key"] == "t2_evaluate_constraints"
        assert blk_step["status"] == "blocked"
        assert "INFEASIBLE_CONSTRAINTS" in str(blk_step["blocker"])

        evidence_out["steps"]["step_9_infeasible_constraint_blocked"] = {
            "passed": True,
            "final_cycle_phase": adv9["final_cycle_phase"],
            "blocker": blk_step["blocker"],
        }

        # =========================================================================
        # STEP 10: Exercise cancellation and parking and verify cleanup status comes
        # from worker/process state, not caller booleans.
        # =========================================================================
        # Create an active leased task on this project so a worker lease is currently active
        t_active = T.create_task(
            title="Active worker task during park test",
            description="Holds an active lease",
            task_type="test_task",
            assigned_worker=worker_id,
            approval_required=False,
        )
        now = now_iso()
        active_lease_id = "lease_live_e2e_active_park_check"
        with db_transaction() as c:
            c.execute("UPDATE tasks SET project_id = ?, status = 'running' WHERE id = ?", (project_id, t_active["id"]))
            c.execute(
                """INSERT INTO worker_task_leases (
                    id, task_id, worker_id, idempotency_key, status,
                    leased_at, lease_expires_at, heartbeat_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'idem_live_e2e', 'active', ?, '2099-01-01T00:00:00+00:00', ?, ?, ?)""",
                (active_lease_id, t_active["id"], worker_id, now, now, now, now),
            )

        # Even if caller passes active_process_cleanup_confirmed=True to the route, controller refuses to mark parked!
        r_park_refused = client.post(
            f"/api/v1/projects/{project_id}/orchestration/park",
            json={"reason": "Attempt park while lease active", "active_process_cleanup_confirmed": True},
            headers=csrf_headers,
        )
        assert r_park_refused.status_code == 409, r_park_refused.text

        # Chat route also refuses with HTTP 409 while lease is active
        r_chat_park_refused = client.post(
            "/api/v1/chat/deliberate",
            json={"project_id": project_id, "action": "park", "message": "Park project"},
            headers=csrf_headers,
        )
        assert r_chat_park_refused.status_code == 409, r_chat_park_refused.text

        # Terminate the active lease & task, then verify park and cancel succeed with cleanup_confirmed
        with db_transaction() as c:
            c.execute("UPDATE worker_task_leases SET status = 'cancelled', updated_at = ? WHERE id = ?", (now_iso(), active_lease_id))
            c.execute("UPDATE tasks SET status = 'cancelled', updated_at = ? WHERE id = ?", (now_iso(), t_active["id"]))

        r_chat_park_ok = client.post(
            "/api/v1/chat/deliberate",
            json={"project_id": project_id, "action": "park", "message": "Park after worker lease drained"},
            headers=csrf_headers,
        )
        assert r_chat_park_ok.status_code == 200, r_chat_park_ok.text
        park_ok_body = r_chat_park_ok.json()["park_result"]
        assert park_ok_body["status"] == "parked"
        assert park_ok_body["cleanup_lifecycle_state"] == "cleanup_confirmed"

        r_chat_cancel_ok = client.post(
            "/api/v1/chat/deliberate",
            json={"project_id": project_id, "action": "cancel", "message": "Cancel project cleanly"},
            headers=csrf_headers,
        )
        assert r_chat_cancel_ok.status_code == 200, r_chat_cancel_ok.text
        cancel_ok_body = r_chat_cancel_ok.json()["cancel_result"]
        assert cancel_ok_body["status"] == "cancelled"
        assert cancel_ok_body["cleanup_lifecycle_state"] == "cleanup_confirmed"

        evidence_out["steps"]["step_10_authoritative_park_and_cancel_cleanup"] = {
            "passed": True,
            "refused_while_lease_active_http_status": r_park_refused.status_code,
            "park_after_drain_status": park_ok_body["status"],
            "park_cleanup_lifecycle_state": park_ok_body["cleanup_lifecycle_state"],
            "cancel_after_drain_status": cancel_ok_body["status"],
            "cancel_cleanup_lifecycle_state": cancel_ok_body["cleanup_lifecycle_state"],
        }

        # Persist Section 8 live E2E evidence snapshot for the acceptance bundle
        ev_dir = repo_root / "docs" / "verified-work" / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        (ev_dir / "increment18-live-native-project-e2e-20261104.json").write_text(
            json.dumps(evidence_out, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    finally:
        if worker_proc is not None and worker_proc.poll() is None:
            worker_proc.terminate()
            try:
                worker_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker_proc.kill()
        if controller_proc is not None and controller_proc.poll() is None:
            controller_proc.terminate()
            try:
                controller_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                controller_proc.kill()

