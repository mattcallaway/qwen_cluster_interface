"""Synthetic permits for reviewed executor fixtures, never native auth evidence.

The real authenticated endpoint/protocol is tested in test_script_authorization.
No runtime code imports this helper or enables a testing authorization fallback.
"""
import time
from vps_agent.script_authorization import ScriptPermit,binding


def reviewed_fixture_permit(task):
    task=dict(task); inputs=task.get('input_data') or {}
    manifest=task.get('execution_manifest') or inputs.get('execution_manifest')
    if not manifest: return task,None
    task.setdefault('worker_id',manifest.get('worker_id','reviewed-fixture-worker'))
    task.setdefault('lease_id',manifest.get('lease_id','reviewed-fixture-lease'))
    task.setdefault('idempotency_key','reviewed-fixture-idempotency')
    return task,ScriptPermit(binding(task),'SYNTHETIC_REVIEWED_FIXTURE_NOT_SERVER_AUTH',time.monotonic()+5,30)
