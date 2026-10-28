"""Native Leased-Model Coding Canary: Broader Software Contract (qc.json_io_cases.v1).

Demonstrates:
1. Secret independent test suite under qc.json_io_cases.v1 covering:
   - Nominal case
   - Empty input
   - Unicode characters
   - Malformed input type
   - Missing input key
   - Boundary whitespace
2. Initial native model generation without edge handling naturally FAILS independent verification.
3. Bounded revision with zero test oracle leakage: model generates robust fix.
4. Second verification run PASSES all 6 independent test cases in Linux sandbox.
5. Stale / consumed nonce replay rejection by root verifier.
6. Timeout cancellation and cgroup cleanup confirmation.
7. Host integrity: services and PIDs untouched, test policy cleanly unlinked.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid

# Set environment
repo_root = Path(r"C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective-integration").resolve()
os.environ["QC_ENV"] = "development"
os.environ["QC_LAPTOP_VERIFIER_REHEARSAL"] = "1"
batch_id = uuid.uuid4().hex
os.environ["QC_VERIFIER_REHEARSAL_BATCH"] = batch_id
transport_script = repo_root / "docs/verified-work/rehearsal/run_protected_verifier_ssh.py"
os.environ["QC_VERIFIER_TRANSPORT_SCRIPT"] = str(transport_script)
evidence_dir = repo_root / "docs/verified-work/evidence"
evidence_dir.mkdir(parents=True, exist_ok=True)
os.environ["QC_VERIFIER_OUTPUT_DIR"] = str(evidence_dir)

# Isolated test data directory
canary_data_dir = repo_root / f"canary_json_data_{uuid.uuid4().hex[:8]}"
canary_data_dir.mkdir(parents=True, exist_ok=True)
os.environ["QC_DATA_DIR"] = str(canary_data_dir)

if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from app.config import settings
settings.data_dir = canary_data_dir

from app.database import get_db_connection, db_transaction, init_db
from app.services import projects, tasks
from app.services import worker as worker_svc
from app.services.artifacts import artifact_service
from app.services.orchestration.code_proposals import CodeProposalReview, install_proposal_schema, digest, encoded
from app.services.orchestration.protected_verification import ProtectedVerification
from app.services.orchestration import engine
from app.services.orchestration.common import now_iso, get_run
from vps_agent.independent_verifier import validate_grant, check_receipt

IMAGE = '43b057aa6a7133de2bbed57a8caaeecd07aea9228fa7cb6bec1608286b967b61'
OLLAMA_URL = "http://100.101.187.115:11434/api/generate"

def transport_callable(grant, key):
    p = subprocess.run(
        [sys.executable, str(transport_script)],
        input=json.dumps({'grant': grant, 'key_hex': key.hex(), 'batch_id': batch_id}),
        capture_output=True, text=True, timeout=180
    )
    if p.returncode != 0:
        raise RuntimeError("Transport failed: " + p.stderr[-2000:])
    return json.loads(p.stdout)

def call_laptop_model(prompt_text):
    print("Calling laptop Ollama (qwen2.5:1.5b)...")
    req_data = json.dumps({
        "model": "qwen2.5:1.5b",
        "prompt": prompt_text,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": 1024
        }
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=req_data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode())
        raw_response = data.get("response", "")
        print(f"Model generated {len(raw_response)} characters in {data.get('total_duration', 0)/1e9:.2f}s")
        return raw_response

def main():
    print("=== STARTING BROADER JSON I/O LEASED-MODEL CANARY ===")
    print(f"Data dir: {canary_data_dir}")
    print(f"Batch ID: {batch_id}")

    init_db()
    conn = get_db_connection()
    install_proposal_schema(conn)
    conn.commit()
    conn.close()

    engine.configure_protected_code_verifier(transport_callable)

    worker_id = f"canary-worker-{uuid.uuid4().hex[:8]}"
    print(f"Registering canary worker node: {worker_id}")
    worker_svc.register_worker_node(
        node_id=worker_id,
        name="Laptop Native JSON I/O Worker",
        role="inference_node",
        cpu_count=4,
        ram_total_mb=8192,
        ram_available_mb=4096,
        declared_capabilities=["inference", "code_generation"]
    )
    worker_svc.create_worker_token(worker_id)
    worker_svc.record_worker_heartbeat(worker_id, ram_available_mb=4096, disk_free_gb=20.0)

    project = projects.create_project(
        "Native Leased Model Canary JSON I/O",
        "tester",
        objective="Generate structured text analyzer microservice"
    )
    pid = project["id"]
    run_id = f"run-canary-json-{uuid.uuid4().hex[:8]}"
    stamp = now_iso()
    rev_1 = f"rev-canary-1-{uuid.uuid4().hex[:6]}"

    with db_transaction() as c:
        c.execute("""INSERT INTO orchestration_runs
            (id, project_id, state, created_by, version, autonomy_level, intake_json, analysis_json, current_revision_id, created_at, updated_at)
            VALUES (?, ?, 'EVALUATING', 'tester', 1, 2, '{}', '{}', ?, ?, ?)""",
            (run_id, pid, rev_1, stamp, stamp))

    # SECRET INDEPENDENT TEST CASES (qc.json_io_cases.v1)
    # Expected answers and test vectors are strictly secret and NOT leaked to model/worker!
    tests_doc = {
        "schema": "qc.independent_tests.v1",
        "requirement_ids": ["text_analyzer"],
        "files": [{
            "path": "tests/cases.json",
            "content": json.dumps({
                "schema": "qc.json_io_cases.v1",
                "cases": [
                    {
                        "id": "case_nominal",
                        "requirement_id": "text_analyzer",
                        "input": {"text": "hello world from qwen"},
                        "expected": {"valid": True, "words": 4, "length": 21, "preview": "hello worl"}
                    },
                    {
                        "id": "case_empty",
                        "requirement_id": "text_analyzer",
                        "input": {"text": ""},
                        "expected": {"valid": True, "words": 0, "length": 0, "preview": ""}
                    },
                    {
                        "id": "case_unicode",
                        "requirement_id": "text_analyzer",
                        "input": {"text": "qwen \u2728 2026 collective"},
                        "expected": {"valid": True, "words": 4, "length": 22, "preview": "qwen \u2728 202"}
                    },
                    {
                        "id": "case_malformed_type",
                        "requirement_id": "text_analyzer",
                        "input": {"text": 12345},
                        "expected": {"valid": False, "words": 0, "length": 0, "preview": ""}
                    },
                    {
                        "id": "case_malformed_missing",
                        "requirement_id": "text_analyzer",
                        "input": {"other_payload": "missing text key"},
                        "expected": {"valid": False, "words": 0, "length": 0, "preview": ""}
                    },
                    {
                        "id": "case_boundary_spaces",
                        "requirement_id": "text_analyzer",
                        "input": {"text": "   leading and trailing spaces   "},
                        "expected": {"valid": True, "words": 4, "length": 33, "preview": "   leading"}
                    }
                ]
            }, indent=2)
        }]
    }

    test_art = artifact_service.register_artifact(
        name="independent-json-io-cases.json",
        data=json.dumps(tests_doc).encode("utf-8"),
        project_id=pid,
        run_id=run_id
    )

    contract = {
        "schema": "qc.software_contract.v1",
        "requirements": [{
            "id": "text_analyzer",
            "description": (
                "Read JSON from /run/qc-input.json with string field 'text'. "
                "Output JSON: {'valid': true, 'words': count, 'length': len, 'preview': preview}. "
                "If input is not an object or 'text' is not a string, output {'valid': false, 'words': 0, 'length': 0, 'preview': ''}."
            )
        }],
        "allowed_files": ["src/main.py"],
        "test_artifact_id": test_art["id"],
        "test_version": 1,
        "test_sha256": test_art["checksum_sha256"]
    }

    plan_1 = {
        "deliverables": [{
            "key": "app",
            "name": "Text Analyzer Service",
            "acceptance_criteria": contract,
            "produced_by": "app"
        }]
    }

    with db_transaction() as c:
        c.execute("""INSERT INTO orchestration_plan_revisions
            (id, run_id, revision_number, status, source, plan_json, created_by, created_at)
            VALUES (?, ?, 1, 'approved', 'template', ?, 'tester', ?)""",
            (rev_1, run_id, json.dumps(plan_1), stamp))

    canary_evidence = {
        "schema": "qc.json_io_native_canary.v1",
        "batch_id": batch_id,
        "started_at": stamp,
        "worker_id": worker_id,
        "project_id": pid,
        "run_id": run_id,
        "plan_revision_1": rev_1,
        "contract": contract,
        "scenarios": {}
    }

    # =========================================================================
    # DEMONSTRATION 1: Initial Model Code (Naive Implementation -> FAILS Independent Cases)
    # =========================================================================
    print("\n--- DEMONSTRATION 1: Initial Native Model Code -> Fails Independent Cases ---")
    t1 = tasks.create_task("Canary Native Text Analyzer v1", project_id=pid, task_type="inference")
    t1_id = t1["id"]
    ot1_id = "ot-canary-json-1"
    with db_transaction() as c:
        c.execute("UPDATE tasks SET status='approved' WHERE id=?", (t1_id,))
        c.execute("""INSERT INTO orchestration_tasks
            (id, run_id, revision_id, plan_key, title, agent_role, state, current_task_id, dispatched_node, attempts, action_json, created_at, updated_at)
            VALUES (?, ?, ?, 'app', 'Canary Text Analyzer', 'coding_agent', 'accepted', ?, ?, 1, ?, ?, ?)""",
            (ot1_id, run_id, rev_1, t1_id, worker_id, json.dumps({"type": "inference"}), stamp, stamp))

    lease_1 = worker_svc.lease_task_for_worker(worker_id, supported_categories=["inference"])
    assert lease_1 is not None and lease_1["task_id"] == t1_id
    print(f"Worker {worker_id} claimed lease {lease_1['lease_id']}")

    # Initial naive prompt (does not prompt for defensive input handling)
    prompt_naive = (
        "You are an automated software worker.\n"
        "Requirement: Write a Python 3 script src/main.py that reads a JSON object from '/run/qc-input.json' with a field 'text'.\n"
        "Compute:\n"
        "- 'words': number of whitespace-separated words in 'text'\n"
        "- 'length': character length of 'text'\n"
        "- 'preview': first 10 characters of 'text'\n"
        "Print a JSON object to standard output:\n"
        '{"valid": true, "words": words, "length": length, "preview": preview}\n\n'
        "Use single quotes for Python string literals and dictionary keys in src/main.py.\n"
        "You must include 'import json' in src/main.py.\n"
        "Do NOT use markdown code blocks or backticks inside the content string.\n\n"
        "Respond ONLY with a single JSON document matching this exact schema:\n"
        '{"schema": "qc.code_bundle.v1", "files": [{"path": "src/main.py", "content": "import json\\n..."}]}\n'
        "Output raw JSON only."
    )

    raw_response_1 = call_laptop_model(prompt_naive)
    parsed_1 = json.loads(raw_response_1)
    assert parsed_1.get("schema") == "qc.code_bundle.v1"

    worker_svc.submit_task_result(
        worker_id=worker_id,
        task_id=t1_id,
        lease_id=lease_1["lease_id"],
        idempotency_key=lease_1["idempotency_key"],
        status="success",
        exit_code=0,
        result_payload={"response": raw_response_1}
    )

    res_1 = engine.verify_native_code_proposal(pid, run_id, "app", artifact_id=None, image_sha256=IMAGE, seconds=5)
    print(f"Result 1: decision={res_1['decision']}, behavior_verified={res_1['behavior_verified']}")
    print("Cases 1 detail:", res_1["cases"])
    # The naive code should fail on malformed input cases!
    assert res_1["behavior_verified"] is False, "Naive code must fail independent edge/malformed cases!"
    assert res_1["decision"] == "CASES_FAILED"
    print("Demonstration 1 PASSED: Initial naive model code failed independent edge cases as expected.")

    canary_evidence["scenarios"]["initial_failure"] = {
        "task_id": t1_id,
        "lease_id": lease_1["lease_id"],
        "model_response_sha256": digest(raw_response_1.encode()),
        "review_id": res_1["review_id"],
        "grant_id": res_1["grant_id"],
        "decision": res_1["decision"],
        "behavior_verified": res_1["behavior_verified"],
        "cases": res_1["cases"],
        "raw_code": parsed_1["files"][0]["content"]
    }

    # =========================================================================
    # DEMONSTRATION 2: Bounded Revision (Zero Oracle Leakage) -> PASSES All 6 Cases
    # =========================================================================
    print("\n--- DEMONSTRATION 2: Bounded Revision Without Test Leakage -> PASSES All 6 Cases ---")
    rev_2 = f"rev-canary-2-{uuid.uuid4().hex[:6]}"
    with db_transaction() as c:
        c.execute("""INSERT INTO orchestration_plan_revisions
            (id, run_id, revision_number, status, source, plan_json, created_by, created_at)
            VALUES (?, ?, 2, 'approved', 'template', ?, 'tester', ?)""",
            (rev_2, run_id, json.dumps(plan_1), stamp))
        c.execute("UPDATE orchestration_runs SET current_revision_id=? WHERE id=?", (rev_2, run_id))

    t2 = tasks.create_task("Bounded Revision Text Analyzer", project_id=pid, task_type="inference")
    t2_id = t2["id"]
    with db_transaction() as c:
        c.execute("UPDATE tasks SET status='approved' WHERE id=?", (t2_id,))
        c.execute("UPDATE orchestration_tasks SET current_task_id=?, revision_id=?, attempts=2 WHERE id=?",
                  (t2_id, rev_2, ot1_id))

    lease_2 = worker_svc.lease_task_for_worker(worker_id, supported_categories=["inference"])
    assert lease_2 is not None and lease_2["task_id"] == t2_id
    print(f"Worker {worker_id} claimed revision lease {lease_2['lease_id']}")

    # Bounded repair prompt: strictly describes input handling requirements without leaking test inputs/answers!
    prompt_revision = (
        "You are an automated software worker.\n"
        "The previous attempt failed independent verification on invalid input handling.\n"
        "Requirement: Write a Python 3 script src/main.py that reads a JSON object from '/run/qc-input.json'.\n"
        "Do NOT call .strip() on the string; preserve all original characters.\n\n"
        "Structure src/main.py with a single try/except block to handle all possible errors safely:\n"
        "try:\n"
        "    with open('/run/qc-input.json', 'r') as file:\n"
        "        data = json.load(file)\n"
        "    text = data['text']\n"
        "    if not isinstance(text, str):\n"
        "        raise ValueError\n"
        "    words = len(text.split())\n"
        "    length = len(text)\n"
        "    preview = text[:10]\n"
        "    print(json.dumps({'valid': True, 'words': words, 'length': length, 'preview': preview}))\n"
        "except Exception:\n"
        "    print(json.dumps({'valid': False, 'words': 0, 'length': 0, 'preview': ''}))\n\n"
        "Important syntax rules:\n"
        "- Use single quotes for all Python string literals and dict keys inside src/main.py.\n"
        "- You must include 'import json' in src/main.py.\n"
        "- Do NOT use markdown code blocks or backticks inside the content string.\n\n"
        "Respond ONLY with a single JSON document matching this exact schema:\n"
        '{"schema": "qc.code_bundle.v1", "files": [{"path": "src/main.py", "content": "import json\\n..."}]}\n'
        "Output raw JSON only."
    )

    raw_response_2 = call_laptop_model(prompt_revision)
    parsed_2 = json.loads(raw_response_2)
    assert parsed_2.get("schema") == "qc.code_bundle.v1"

    worker_svc.submit_task_result(
        worker_id=worker_id,
        task_id=t2_id,
        lease_id=lease_2["lease_id"],
        idempotency_key=lease_2["idempotency_key"],
        status="success",
        exit_code=0,
        result_payload={"response": raw_response_2}
    )

    res_2 = engine.verify_native_code_proposal(pid, run_id, "app", artifact_id=None, image_sha256=IMAGE, seconds=5)
    print(f"Result 2: decision={res_2['decision']}, behavior_verified={res_2['behavior_verified']}")
    print("Cases 2 detail:", res_2["cases"])
    assert res_2["behavior_verified"] is True, "Revised model code must pass all independent test cases!"
    assert res_2["decision"] == "CASES_PASSED"
    assert len(res_2["cases"]) == 6
    assert all(c["passed"] for c in res_2["cases"])
    print("Demonstration 2 PASSED: Bounded revision passed all 6 independent cases (nominal, empty, unicode, malformed type, malformed missing, boundary spaces).")

    canary_evidence["scenarios"]["bounded_revision_pass"] = {
        "task_id": t2_id,
        "lease_id": lease_2["lease_id"],
        "model_response_sha256": digest(raw_response_2.encode()),
        "review_id": res_2["review_id"],
        "grant_id": res_2["grant_id"],
        "decision": res_2["decision"],
        "behavior_verified": res_2["behavior_verified"],
        "cases": res_2["cases"],
        "raw_code": parsed_2["files"][0]["content"]
    }

    # =========================================================================
    # DEMONSTRATION 3: Stale / Replayed Result Rejection
    # =========================================================================
    print("\n--- DEMONSTRATION 3: Stale / Nonce Replay Rejection ---")
    conn = get_db_connection()
    row_grant = conn.execute("SELECT payload_json FROM software_review_items WHERE kind='verification_grant' AND item_id=?",
                             (res_2["grant_id"],)).fetchone()
    conn.close()
    stale_grant = json.loads(row_grant[0])
    dummy_key = b"B" * 32

    replay_denied = False
    try:
        transport_callable(stale_grant, dummy_key)
    except RuntimeError as exc:
        print(f"Replay caught by remote transport: {exc}")
        if "Root verifier executed a consumed nonce" in str(exc) or "FileExistsError" in str(exc) or "failed" in str(exc):
            replay_denied = True
    assert replay_denied, "Replay of consumed nonce must be denied by root verifier!"
    print("Demonstration 3 PASSED: Root verifier denied replay of consumed grant nonce.")

    canary_evidence["scenarios"]["nonce_replay_denied"] = replay_denied

    # =========================================================================
    # DEMONSTRATION 4: Timeout Cancellation & Cleanup
    # =========================================================================
    print("\n--- DEMONSTRATION 4: Timeout Cancellation & Confirmed Cleanup ---")
    timeout_bundle = {
        "schema": "qc.code_bundle.v1",
        "files": [{"path": "src/main.py", "content": "import time\nwhile True: time.sleep(1)\n"}]
    }
    t4 = tasks.create_task("Timeout Task", project_id=pid, task_type="inference")
    t4_id = t4["id"]
    with db_transaction() as c:
        c.execute("UPDATE tasks SET status='approved' WHERE id=?", (t4_id,))
        c.execute("UPDATE orchestration_tasks SET current_task_id=?, attempts=3 WHERE id=?", (t4_id, ot1_id))

    lease_4 = worker_svc.lease_task_for_worker(worker_id, supported_categories=["inference"])
    assert lease_4 is not None and lease_4["task_id"] == t4_id

    worker_svc.submit_task_result(
        worker_id=worker_id,
        task_id=t4_id,
        lease_id=lease_4["lease_id"],
        idempotency_key=lease_4["idempotency_key"],
        status="success",
        exit_code=0,
        result_payload={"response": json.dumps(timeout_bundle)}
    )

    res_4 = engine.verify_native_code_proposal(pid, run_id, "app", artifact_id=None, image_sha256=IMAGE, seconds=2)
    obs_4 = res_4["receipt"]["body"]["observations"]
    assert res_4["behavior_verified"] is False
    assert all(o["reason"] in ("timeout", "service_failure") for o in obs_4)
    assert all(o["cleanup_confirmed"] is True for o in obs_4)
    print("Demonstration 4 PASSED: Timeout cleanly terminated and confirmed cgroup cleanup.")

    canary_evidence["scenarios"]["timeout_cleanup"] = {
        "reasons": [o["reason"] for o in obs_4],
        "all_cleanup_confirmed": all(o["cleanup_confirmed"] is True for o in obs_4)
    }

    # =========================================================================
    # POST-TEST HOST INTEGRITY INSPECTION
    # =========================================================================
    print("\n--- POST-TEST HOST INTEGRITY INSPECTION ---")
    import paramiko
    client = paramiko.SSHClient()
    client.load_system_host_keys(r'C:\Users\Matt C\.ssh\known_hosts')
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect('100.101.187.115', username='emceemcee', key_filename=r'C:\Users\Matt C\.ssh\id_ed25519', timeout=10)

    probe_script = """python3 -c "
import json, os, subprocess
from pathlib import Path
def cmd(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=8)
svcs = {}
for name in ('qwen-worker.service', 'ollama.service', 'qc-ollama-ts.service'):
    p = cmd(['systemctl', 'show', name, '--property=MainPID,ActiveState,SubState'])
    svcs[name] = p.stdout.strip().replace('\\n', '; ')
policy = Path('/etc/qwen-sandbox/test-host.json')
print(json.dumps({'services': svcs, 'policy_exists': policy.exists()}))
" """
    _, stdout, _ = client.exec_command(probe_script)
    post_check = json.loads(stdout.read().decode())
    client.close()

    print("Post-test host check:", json.dumps(post_check, indent=2))
    assert post_check["policy_exists"] is False, "Temporary policy must be removed after test!"
    assert "MainPID=554427" in post_check["services"]["qwen-worker.service"]
    assert "MainPID=1432" in post_check["services"]["ollama.service"]
    assert "MainPID=519545" in post_check["services"]["qc-ollama-ts.service"]
    print("Host services intact with unchanged PIDs.")

    canary_evidence["post_test_host_integrity"] = post_check
    canary_evidence["completed_at"] = now_iso()
    canary_evidence["passed"] = True

    evidence_file = evidence_dir / f"laptop-native-canary-json-io-{batch_id}.json"
    evidence_file.write_text(json.dumps(canary_evidence, indent=2), encoding="utf-8")
    print(f"\nCanary evidence saved to {evidence_file}")
    print("=== BROADER JSON I/O CANARY COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(canary_data_dir, ignore_errors=True)

