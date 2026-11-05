"""One-shot authorization for current approved native library scripts only.

Explicit development schema/feature flag; never provisions OS containment or
authorizes generated code. Callers must authenticate the exact worker token.
"""
from datetime import datetime,timezone
import hashlib,json,os,re,sqlite3,uuid
from app.database import get_db_connection
from app.services.research.claim_contracts import strict_json
from app.services.orchestration import script_library
from app.services import worker as workers


def install_script_authorization_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS worker_script_authorizations (
        lease_id TEXT PRIMARY KEY,task_id TEXT NOT NULL UNIQUE,authorization_id TEXT NOT NULL UNIQUE,
        nonce_sha256 TEXT NOT NULL UNIQUE,payload_json TEXT NOT NULL,created_at TEXT NOT NULL)''')
    for operation in ('UPDATE','DELETE'):
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS script_auth_no_{operation.lower()}
            BEFORE {operation} ON worker_script_authorizations
            BEGIN SELECT RAISE(ABORT,'script authorization is append-only'); END''')


def _sha(raw): return hashlib.sha256(raw).hexdigest()
def _canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def _time(value):
    if not isinstance(value,str): raise ValueError('ISO timestamp required')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None: raise ValueError('Timezone required')
    return result


def authorize_start(worker_id,task_id,idempotency_key,manifest,nonce):
    deny=lambda reason: workers.TaskAuthorizationError(reason,status_code=403)
    if os.getenv('QC_SCRIPT_AUTHORIZATION_ENABLED')!='1': raise deny('Script authorization is held')
    if not isinstance(nonce,str) or not re.fullmatch('[0-9a-f]{64}',nonce): raise deny('Invalid authorization nonce')
    fields={'task_id','lease_id','worker_id','task_type','script_checksum','max_runtime_seconds',
            'allowed_capabilities','network_access','issued_at','signature'}
    try:
        shaped=isinstance(manifest,dict) and set(manifest)==fields and len(_canonical(manifest))<=8192
    except (ValueError,TypeError): shaped=False
    if not shaped:
        raise deny('Unsupported execution manifest')
    conn=get_db_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        ctx=workers.check_worker_leasing_authority(conn,worker_id,requested_categories=['python_execution'])
        restrictions=ctx['restrictions']
        # New capability never inherits permissive legacy defaults.
        if (restrictions.get('allow_script_execution') is not True
                or restrictions.get('allow_operational_execution') is not True
                or restrictions.get('operational_hold') is not False):
            raise deny('Explicit operational/script authorization required')
        task=conn.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
        lease=conn.execute('SELECT * FROM worker_task_leases WHERE id=?',(manifest['lease_id'],)).fetchone()
        now=datetime.now(timezone.utc)
        if (not task or not lease or task['task_type']!='python_execution' or task['status']!='in_progress'
                or task['assigned_worker']!=worker_id or lease['task_id']!=task_id or lease['worker_id']!=worker_id
                or lease['status']!='leased' or lease['idempotency_key']!=idempotency_key
                or _time(lease['lease_expires_at'])<=now):
            raise deny('Active task/lease/worker binding required')
        if conn.execute('SELECT 1 FROM worker_executions WHERE task_id=?',(task_id,)).fetchone():
            raise deny('Task already has execution history; use a new approved task')
        inputs=strict_json(task['input_data_json'])
        if not isinstance(inputs,dict): raise deny('Structured approved inputs required')
        script=inputs.get('script') or inputs.get('code')
        if not isinstance(script,str) or not script or len(script.encode())>250_000: raise deny('Invalid approved script')
        valid,_=workers.verify_execution_manifest(manifest,script,expected_task_id=task_id,expected_worker_id=worker_id)
        if not valid: raise deny('Controller manifest signature or content verification failed')
        issued=_time(manifest['issued_at'])
        if (manifest['task_type']!=task['task_type'] or manifest['network_access'] is not False
                or manifest['allowed_capabilities']!=['python_execution']
                or type(manifest['max_runtime_seconds']) is not int or not 1<=manifest['max_runtime_seconds']<=3600
                or not -1<=(now-issued).total_seconds()<=60
                or issued<_time(lease['leased_at'])):
            raise deny('Manifest timing/capabilities invalid')
        rows=conn.execute('''SELECT o.plan_key,o.action_json,rev.plan_json FROM orchestration_tasks o
            JOIN orchestration_runs r ON r.id=o.run_id AND r.current_revision_id=o.revision_id
            JOIN orchestration_plan_revisions rev ON rev.id=r.current_revision_id AND rev.status='approved'
            JOIN projects p ON p.id=r.project_id AND p.status='active'
            WHERE o.current_task_id=? AND o.dispatched_node=? AND o.state='dispatched'
                AND r.project_id=? AND r.state='RUNNING' ''',(task_id,worker_id,task['project_id'])).fetchall()
        if len(rows)!=1: raise deny('Current native dispatched task in an active approved project required')
        row=rows[0]; action=strict_json(row['action_json']); plan=strict_json(row['plan_json'])
        selected=[t for t in plan.get('tasks',[]) if t.get('key')==row['plan_key']]
        if (len(selected)!=1 or selected[0].get('action')!=action or worker_id not in selected[0].get('allowed_nodes',[])
                or action.get('type')!='library_script' or action.get('script_id') not in script_library.LIBRARY):
            raise deny('Pinned approved library action required')
        library=script_library.get_script(action['script_id'])
        if library['script']!=script or library['network_access'] is not False: raise deny('Reviewed library bytes required')
        max_runtime=min(30,library['timeout_seconds'],manifest['max_runtime_seconds'],
                        int(( _time(lease['lease_expires_at'])-now).total_seconds())-6)
        if max_runtime<1: raise deny('Insufficient remaining lease time')
        response={'schema':'qc.script_start.v1','authorization_id':'script-auth-'+uuid.uuid4().hex,
            'nonce':nonce,'binding':{'task_id':task_id,'lease_id':lease['id'],'worker_id':worker_id,
                'idempotency_key':idempotency_key,'task_type':task['task_type'],
                'script_sha256':_sha(script.encode()),'manifest_sha256':_sha(_canonical(manifest))},
            'start_within_seconds':5,'max_runtime_seconds':max_runtime}
        # Consumed before replying. Lost response/crash never grants another start.
        conn.execute('INSERT INTO worker_script_authorizations VALUES (?,?,?,?,?,?)',
            (lease['id'],task_id,response['authorization_id'],_sha(nonce.encode()),json.dumps(response,sort_keys=True),now.isoformat()))
        conn.commit()
        return response
    except workers.TaskAuthorizationError:
        conn.rollback(); raise
    except (ValueError,TypeError,KeyError,sqlite3.Error):
        conn.rollback(); raise deny('Script authorization unavailable or invalid; no start permitted')
    finally: conn.close()
