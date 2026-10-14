import pytest
from app.services.tasks import (
    create_task,
    get_task_by_id,
    get_all_tasks,
    approve_task,
    cancel_task,
    update_task_progress,
    get_task_counts
)

def test_task_creation_and_approval_gate():
    # 1. Create task requiring manual approval
    task = create_task(
        title="Deploy Model Shard",
        description="Verify shard weights",
        task_type="inference",
        approval_required=True
    )
    assert task["id"].startswith("task-")
    assert task["status"] == "awaiting_approval"
    assert task["approval_required"] == 1
    
    # 2. Approve task
    approved = approve_task(task["id"])
    assert approved is True
    
    updated = get_task_by_id(task["id"])
    assert updated["status"] == "queued"

def test_task_lifecycle_and_results():
    task = create_task(
        title="Probe Network Subnet",
        task_type="probe",
        approval_required=False
    )
    assert task["status"] == "queued"
    
    # Transition to running
    update_task_progress(task["id"], "running")
    running = get_task_by_id(task["id"])
    assert running["status"] == "running"
    assert running["attempt_count"] == 1
    
    # Complete with structured result data
    res_payload = {"reachable_ips": ["192.168.1.101", "192.168.1.102"]}
    update_task_progress(task["id"], "completed", result_data=res_payload)
    
    completed = get_task_by_id(task["id"])
    assert completed["status"] == "completed"
    assert completed["result_data"]["reachable_ips"] == ["192.168.1.101", "192.168.1.102"]

def test_task_cancellation():
    task = create_task(
        title="Pending Action",
        approval_required=True
    )
    cancel_task(task["id"])
    assert get_task_by_id(task["id"])["status"] == "cancelled"

def test_task_idempotency():
    task1 = create_task(
        title="Idempotent Task",
        idempotency_key="idempotency-test-unique-key-1"
    )
    task2 = create_task(
        title="Idempotent Task Duplicate",
        idempotency_key="idempotency-test-unique-key-1"
    )
    assert task1["id"] == task2["id"]
