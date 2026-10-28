"""Recorded native provenance fixtures in the real migrated test schema.

These simulate completed records; they do not execute a model or test leasing.
The separate worker-authorization suite tests that gateway.
"""
import json
from uuid import uuid4
from pathlib import Path
from app.config import settings
from app.database import db_transaction
from app.services import projects, tasks
from app.services.artifacts import artifact_service
from app.services.orchestration.common import now_iso, get_run


def recorded_native_outputs(plan, intake, inference=False, inference_response=None):
    assert '.qwen-collective' not in str(Path(settings.data_dir).resolve()).lower()
    suffix=uuid4().hex
    project=projects.create_project('Recorded native fixture '+suffix,'tester',objective=intake.get('objective',''))
    pid=project['id']; rid='run-fixture-'+suffix; rev='rev-fixture-'+suffix; worker='worker-fixture-'+suffix
    stamp=now_iso()
    with db_transaction() as c:
        c.execute('''INSERT INTO infrastructure_nodes
            (id,name,role,is_enabled,health_status,last_contact_at,created_at,updated_at)
            VALUES (?,?,'inference_node',1,'online',?,?,?)''',(worker,worker,stamp,stamp,stamp))
        c.execute('''INSERT INTO orchestration_runs
            (id,project_id,state,created_by,version,autonomy_level,intake_json,analysis_json,created_at,updated_at)
            VALUES (?,?,'EVALUATING','tester',1,2,?,'{}',?,?)''',(rid,pid,json.dumps(intake),stamp,stamp))
        c.execute('''INSERT INTO orchestration_plan_revisions
            (id,run_id,revision_number,status,source,plan_json,created_by,created_at)
            VALUES (?,?,1,'approved','template',?,'tester',?)''',(rev,rid,json.dumps(plan),stamp))
    tested=[]
    for index,d in enumerate(plan['deliverables']):
        task=tasks.create_task('Recorded output',project_id=pid,task_type='inference' if inference else 'general')
        tid=task['id']; oid=f'ot-{suffix}-{index}'; lid=f'lease-{suffix}-{index}'; key=f'key-{suffix}-{index}'
        with db_transaction() as c:
            c.execute("UPDATE tasks SET status='completed' WHERE id=?",(tid,))
            c.execute('''INSERT INTO orchestration_tasks
                (id,run_id,revision_id,plan_key,title,agent_role,state,current_task_id,dispatched_node,action_json,created_at,updated_at)
                VALUES (?,?,?,?,'Recorded output','integration','accepted',?,?,?,?,?)''',
                (oid,rid,rev,d.get('produced_by',d['key']),tid,worker,json.dumps({'type':'inference' if inference else 'controller_step'}),stamp,stamp))
            c.execute('''INSERT INTO worker_task_leases
                (id,task_id,worker_id,idempotency_key,status,leased_at,lease_expires_at,heartbeat_at,created_at,updated_at)
                VALUES (?,?,?,?,'completed',?,?,?,?,?)''',(lid,tid,worker,key,stamp,stamp,stamp,stamp,stamp))
            c.execute('''INSERT INTO worker_executions
                (id,task_id,worker_id,lease_id,idempotency_key,status,exit_code,started_at,completed_at,created_at,result_payload_json)
                VALUES (?,?,?,?,?,'success',0,?,?,?,?)''',(f'exec-{suffix}-{index}',tid,worker,lid,key,stamp,stamp,stamp,
                    json.dumps({'response':inference_response}) if inference_response is not None else '{}'))
        artifact=artifact_service.register_artifact(name=d['key']+'.md',data=('Output '+d['key']).encode(),
            project_id=pid,run_id=rid,task_id=tid,worker_id=worker)
        tested.append({'key':d['key'],'name':d['name'],'status':'accepted','criteria_tested':{},
            'evidence_satisfied':[{'id':artifact['id'],'sha256':artifact['checksum_sha256']}]})
    return pid,get_run(rid),tested
