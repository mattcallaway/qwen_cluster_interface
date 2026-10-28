"""Create, run, and monitor an Orchestrator Project testing Increment 17 capabilities and faults."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_db_connection
from app.services import projects as ps
from app.services.orchestration import (
    approval_manager,
    artifact_manager,
    engine,
    planner,
    task_graph,
)
from app.services.orchestration.common import list_events

def main():
    proj_unique_suffix = int(time.time())
    project_name = f"proj-inc17-fault-test-{proj_unique_suffix}"

    print("=" * 70)
    print(f"1. Creating Orchestrated Project: {project_name}...")
    print("=" * 70)

    intake = {
        "objective": "Verify Increment 17 Distributed Pipeline Parallelism & Searchable Paged KV Cache capabilities under adversarial fault conditions, measure fault containment ratio, and generate a certified verification report.",
        "scope": "Radix prefix matching, virtual page table COW forking, RAM quota LRU eviction, WireGuard activation frame packaging, HMAC-SHA256 bit-flip tamper detection, truncated packet handling, and multi-stage pipeline topology validation.",
        "deliverables": "Structured diagnostic artifact (pipeline_kv_diagnostics.json) and final markdown verification report.",
        "success_criteria": "All 9 capability and fault tests pass with 100% adversarial fault containment (5/5 faults trapped) and certified deliverable assembled.",
    }

    res = engine.create_project_with_run(
        name=project_name,
        actor="admin",
        intake=intake,
        autonomy_level=2,
    )
    project = res["project"]
    run = res["run"]
    run_id = run["id"]
    print(f"Created Project: {project['id']} (Name: {project['name']})")
    print(f"Created Run: {run_id} (State: {run['state']})")

    print("\n" + "=" * 70)
    print("2. Generating and Storing Plan DAG...")
    print("=" * 70)

    plan = {
        "schema": "qc.plan.v1",
        "overview": {
            "objective": intake["objective"],
            "scope": intake["scope"],
            "assumptions": [
                "Controller has Increment 17 modules installed (paged_kv, pipeline_parallel).",
                "Cluster WireGuard keys and protocol bindings are configured.",
            ],
            "constraints": [
                "Production safety holds must remain fail-closed.",
                "Non-destructive fault injection (isolated memory and mock buffers).",
            ],
            "expected_outcome": "Complete fault resilience verification with zero unhandled exceptions.",
        },
        "paths": [
            {
                "key": "main",
                "name": "Main Verification Path",
                "rationale": "Sequential execution of diagnostic test suite followed by deliverable report assembly.",
                "stages": [
                    {
                        "key": "s_diag",
                        "name": "Diagnostics & Adversarial Fault Injection",
                        "objective": "Execute 9 capability and fault tests against Paged KV and Pipeline Parallelism.",
                    },
                    {
                        "key": "s_report",
                        "name": "Report Assembly & Certification",
                        "objective": "Compile final project report with measured metrics and provenance.",
                    },
                ],
            }
        ],
        "tasks": [
            {
                "key": "run_diagnostics",
                "title": "Execute Pipeline & Paged KV Diagnostics",
                "description": "Run live algorithmic, cryptographic, and fault-injection checks.",
                "stage": "s_diag",
                "agent_role": "execution",
                "depends_on": [],
                "allowed_nodes": [],
                "priority": 1,
                "timeout_seconds": 120,
                "max_attempts": 2,
                "risk": "low",
                "approval": "auto",
                "action": {
                    "type": "controller_step",
                    "step": "pipeline_kv_diagnostics",
                    "inputs": [],
                },
                "expected_outputs": ["pipeline_kv_diagnostics.json"],
                "acceptance": {},
            },
            {
                "key": "assemble_report",
                "title": "Assemble Verification & Fault Report",
                "description": "Compile the consolidated Markdown report with verified provenance and diagnostic metrics.",
                "stage": "s_report",
                "agent_role": "integration",
                "depends_on": ["run_diagnostics"],
                "allowed_nodes": [],
                "priority": 2,
                "timeout_seconds": 120,
                "max_attempts": 2,
                "risk": "low",
                "approval": "auto",
                "action": {
                    "type": "controller_step",
                    "step": "assemble_report",
                    "inputs": ["run_diagnostics"],
                },
                "expected_outputs": ["health_report.md", "health_report.json"],
                "acceptance": {},
            },
        ],
        "deliverables": [
            {
                "key": "d1",
                "name": "Increment 17 Verification and Fault Containment Report",
                "type": "report",
                "acceptance_criteria": "Contains verified operational results, fault containment metrics, and complete provenance.",
                "produced_by": "assemble_report",
                "stage": "s_report",
                "terminal": True,
            }
        ],
        "risks": [
            {
                "description": "Adversarial fault injection could leak unhandled exceptions if error boundaries fail.",
                "mitigation": "Strict try/except trapping with cryptographic and framing assertions.",
            }
        ],
        "approval_gates": [],
    }

    # Validate plan with task_graph validator
    from app.services.orchestration.model_router import node_inventory
    val = task_graph.validate_plan(plan, node_inventory())
    print(f"Plan Validation: valid={val['valid']}, errors={val.get('errors')}, warnings={val.get('warnings')}")
    if not val["valid"]:
        raise ValueError(f"Plan validation failed: {val['errors']}")

    rev = planner.store_revision(run_id, plan, source="manual_edit", actor="admin")
    print(f"Stored Plan Revision: {rev['id']} (Rev #{rev['revision_number']}, Digest: {rev['plan_digest'][:16]}...)")

    from app.services.orchestration import states
    states.transition(run_id, "PLANNING", "planning_started", "admin", "custom plan")
    states.transition(run_id, "AWAITING_PLAN_APPROVAL", "plan_proposed", "admin", "custom plan awaiting approval")

    print("\n" + "=" * 70)
    print("3. Approving Plan and Starting Run...")
    print("=" * 70)

    appr = approval_manager.approve_plan(
        run_id,
        "admin",
        rationale="Operator sign-off for Increment 17 capability and fault verification DAG",
        revision_id=rev["id"],
    )
    print(f"Plan Approved! Run State: {appr['state']}")

    start_res = engine.start_run(run_id, "admin")
    print(f"Run Started! State: {start_res['state']}")

    print("\n" + "=" * 70)
    print("4. Executing and Monitoring Orchestration Run...")
    print("=" * 70)

    last_event_id = 0
    max_ticks = 20
    for tick_num in range(1, max_ticks + 1):
        # Allow any in-flight controller steps to execute
        engine.await_active_controller_steps(run_id, timeout=5.0)

        # Advance single scheduler tick
        active = engine.tick(run_id, manual=True)
        current_state = active["state"]
        print(f"[Tick #{tick_num}] Run State: {current_state}")

        # Stream new events
        events = list_events(project["id"], after_id=last_event_id)
        for ev in events:
            last_event_id = max(last_event_id, ev["id"])
            print(f"  --> Event [{ev['event_type']}]: {ev.get('reason')} (actor: {ev['actor']})")

        # Check tasks status
        c = get_db_connection()
        try:
            tasks = [dict(r) for r in c.execute("SELECT plan_key, state, attempts, dispatched_node FROM orchestration_tasks WHERE run_id=?", (run_id,)).fetchall()]
            for t in tasks:
                print(f"      Task '{t['plan_key']}': {t['state']} (node={t['dispatched_node']}, attempts={t['attempts']})")
        finally:
            c.close()

        if current_state in ("COMPLETED", "SUCCEEDED"):
            print(f"\nSUCCESS: Run reached terminal state '{current_state}'!")
            break
        elif current_state in ("FAILED", "BLOCKED"):
            print(f"\nALERT: Run entered state '{current_state}'!")
            break

        time.sleep(0.5)

    print("\n" + "=" * 70)
    print("5. Inspecting Run Artifacts and Deliverables...")
    print("=" * 70)

    artifacts = artifact_manager.list_artifacts(project["id"])
    print(f"Total Artifacts Produced: {len(artifacts)}")
    for a in artifacts:
        print(f" - [{a['validation_status']}] {a['filename']} ({a['size_bytes']} bytes, SHA256: {a['sha256'][:12]}...)")
        if a["filename"] == "pipeline_kv_diagnostics.json":
            content = artifact_manager.read_artifact_bytes(a["id"]).decode("utf-8")
            data = json.loads(content)
            print(f"   * Status: {data.get('status')}")
            print(f"   * Total Tests: {data.get('total_tests')} (Passed: {data.get('passed_tests')})")
            print(f"   * Faults Injected: {data.get('faults_injected')} (Trapped: {data.get('faults_trapped_and_contained')})")
            print(f"   * Containment Ratio: {data.get('fault_containment_ratio') * 100}%")
        elif a["filename"] == "health_report.md":
            content = artifact_manager.read_artifact_bytes(a["id"]).decode("utf-8")
            print(f"\n--- Preview of Final Health Report Markdown ---\n")
            print(content[:800] + ("\n...[truncated]" if len(content) > 800 else ""))

    print("\n" + "=" * 70)
    print("Orchestrator Project Execution & Monitoring Completed Successfully!")
    print("=" * 70)

if __name__ == "__main__":
    main()
