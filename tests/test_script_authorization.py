import copy,json,secrets,time,sqlite3
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.services import projects as P,tasks as T,worker as W
from app.services.orchestration import script_authorization as server,script_library
from vps_agent import executor
from vps_agent.agent import VPSWorkerAgent
from vps_agent.buffer import OfflineBuffer
from vps_agent.config import AgentConfig
from vps_agent.script_authorization import binding,ScriptPermit,permit_from_response,secure_controller_url
from certification_fixtures import recorded_native_outputs
from test_worker_authorization_enforcement import _setup_node


@pytest.fixture
def authorized_script(db_conn,monkeypatch):
    monkeypatch.setenv('QC_SCRIPT_AUTHORIZATION_ENABLED','1')
    pid,run,_=recorded_native_outputs({'deliverables':[]},{'objective':'Generate a reviewed synthetic web fixture'})
    P.change_project_status(pid,'active','tester','Synthetic authorization test')
    path=P.create_path(pid,'tester',name='Reviewed fixture',rationale='Exercise native authorization')
    P.change_path_status(pid,path['id'],'approved','tester','Test authorization')
    P.select_path(pid,path['id'],'tester','Test authorization')
    stage=P.create_stage(pid,path['id'],'tester',name='Generate fixture')
    worker='script-worker-'+uuid4().hex
    restrictions={'operational_hold':False,'allow_operational_execution':True,
                  'allow_script_execution':True,'allowed_task_types':['python_execution']}
    token,token_id=_setup_node(db_conn,worker,role='general_worker',restrictions=restrictions)
    action={'type':'library_script','script_id':'web_status_pilot'}
    script=script_library.get_script('web_status_pilot')['script']
    task=T.create_task('Reviewed web fixture',task_type='python_execution',assigned_worker=worker,
        project_id=pid,path_id=path['id'],stage_id=stage['id'],purpose='Synthetic verification',
        expected_output='Exact reviewed index.html',completion_criteria='No software certification',input_data={'script':script})
    assert T.approve_task(task['id'])
    revision='script-plan-'+uuid4().hex
    plan={'deliverables':[],'tasks':[{'key':'fixture','action':action,'allowed_nodes':[worker]}]}
    db_conn.execute('''INSERT INTO orchestration_plan_revisions
        (id,run_id,revision_number,status,source,plan_json,created_by,created_at)
        VALUES (?,?,2,'approved','template',?,'tester','2026-10-27')''',(revision,run['id'],json.dumps(plan)))
    db_conn.execute("UPDATE orchestration_runs SET current_revision_id=?,state='RUNNING' WHERE id=?",(revision,run['id']))
    db_conn.execute('''INSERT INTO orchestration_tasks
        (id,run_id,revision_id,plan_key,title,agent_role,state,current_task_id,dispatched_node,action_json,created_at,updated_at)
        VALUES (?,?,?,'fixture','Reviewed fixture','software','dispatched',?,?,?,'2026-10-27','2026-10-27')''',
        ('script-ot-'+uuid4().hex,run['id'],revision,task['id'],worker,json.dumps(action)))
    server.install_script_authorization_schema(db_conn); db_conn.commit()
    client=TestClient(app); headers={'Authorization':'Bearer '+token}
    def lease():
        response=client.post('/api/v1/worker/tasks/lease',headers=headers,json={
            'worker_id':worker,'supported_categories':['python_execution'],'lease_duration_seconds':60})
        assert response.status_code==200,response.text
        assert response.json()['status']=='leased',response.text
        return response.json()['task']
    def authorize(leased,manifest=None,nonce=None,custom_headers=None):
        return client.post(f'/api/v1/worker/tasks/{leased["task_id"]}/authorize-script',headers=custom_headers or headers,
            json={'idempotency_key':leased['idempotency_key'],'manifest':manifest or leased['execution_manifest'],
                  'nonce':nonce or secrets.token_hex(32)})
    return {'conn':db_conn,'client':client,'headers':headers,'worker':worker,'token':token,'token_id':token_id,
            'lease':lease,'authorize':authorize,'task':task,'revision':revision,'run_id':run['id'],'plan':plan}


def test_authenticated_start_is_one_shot_and_runtime_bounded(authorized_script):
    f=authorized_script; task=f['lease'](); nonce=secrets.token_hex(32); started=time.monotonic()
    response=f['authorize'](task,nonce=nonce)
    assert response.status_code==200,response.text
    permit=permit_from_response(task,response.json(),nonce,started)
    assert permit.consume(task)==15
    with pytest.raises(ValueError): permit.consume(task)
    assert f['authorize'](task).status_code==403
    with pytest.raises(sqlite3.IntegrityError): f['conn'].execute('DELETE FROM worker_script_authorizations')


@pytest.mark.parametrize('field,value',[
    ('signature','0'*64),('worker_id','another-worker'),('task_id','another-task'),
    ('lease_id','unknown-lease'),('script_checksum','0'*64),('network_access',True),
    ('max_runtime_seconds',999999),('allowed_capabilities',['network']),
])
def test_manifest_tampering_never_authorizes(authorized_script,field,value):
    f=authorized_script; task=f['lease'](); manifest=copy.deepcopy(task['execution_manifest']); manifest[field]=value
    assert f['authorize'](task,manifest=manifest).status_code==403


@pytest.mark.parametrize('change',['expired','lease_key','disabled','hold','revoked_token','failed_task','plan_changed',
                                    'feature_off','unsigned','old_issue','future_issue','missing_schema'])
def test_live_authority_changes_fail_closed(authorized_script,monkeypatch,change):
    f=authorized_script; task=f['lease'](); c=f['conn']; manifest=copy.deepcopy(task['execution_manifest'])
    if change=='expired': c.execute('UPDATE worker_task_leases SET lease_expires_at=? WHERE id=?',
        ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),task['lease_id']))
    if change=='lease_key': c.execute("UPDATE worker_task_leases SET idempotency_key='different' WHERE id=?",(task['lease_id'],))
    if change=='disabled': c.execute('UPDATE infrastructure_nodes SET is_enabled=0 WHERE id=?',(f['worker'],))
    if change=='hold': c.execute("UPDATE infrastructure_nodes SET capability_restrictions_json=? WHERE id=?",
        (json.dumps({'operational_hold':True}),f['worker']))
    if change=='revoked_token': c.execute('UPDATE worker_auth_tokens SET revoked=1 WHERE id=?',(f['token_id'],))
    if change=='failed_task': c.execute("UPDATE tasks SET status='failed' WHERE id=?",(task['task_id'],))
    if change=='plan_changed': c.execute('UPDATE orchestration_runs SET current_revision_id=NULL WHERE id=?',(f['run_id'],))
    if change=='feature_off': monkeypatch.delenv('QC_SCRIPT_AUTHORIZATION_ENABLED')
    if change=='unsigned': manifest.pop('signature')
    if change in ('old_issue','future_issue'):
        manifest['issued_at']=(datetime.now(timezone.utc)+timedelta(seconds=-120 if change=='old_issue' else 30)).isoformat()
        import hmac,hashlib
        from app.config import settings
        manifest['signature']=hmac.new(settings.secret_key.encode(),json.dumps({k:v for k,v in manifest.items() if k!='signature'},sort_keys=True).encode(),hashlib.sha256).hexdigest()
    if change=='missing_schema':
        # A separate missing-table database is not necessary; rename the empty
        # development table to reproduce unavailable provisioning, retaining triggers.
        c.execute('ALTER TABLE worker_script_authorizations RENAME TO worker_script_authorizations_unavailable')
    c.commit()
    assert f['authorize'](task,manifest=manifest).status_code in (401,403)
    if change=='missing_schema':
        c.execute('ALTER TABLE worker_script_authorizations_unavailable RENAME TO worker_script_authorizations'); c.commit()


def test_wrong_token_identity_cannot_use_another_workers_manifest(authorized_script):
    f=authorized_script; task=f['lease']()
    token,_=_setup_node(f['conn'],'other-script-worker-'+uuid4().hex,role='general_worker')
    assert f['authorize'](task,custom_headers={'Authorization':'Bearer '+token}).status_code==403


def test_executor_requires_object_not_json_approval(authorized_script,monkeypatch):
    f=authorized_script; task=f['lease']()
    monkeypatch.setattr(executor,'_detect_os_isolation_mechanism',lambda:'restricted_subprocess')
    monkeypatch.setattr(executor.subprocess,'run',lambda *a,**k:pytest.fail('Unauthorized code executed'))
    for permit in (None,{'accepted':True,'signature':'fake'}):
        result=executor.execute_task(task,script_permit=permit)
        assert result['error_category']=='script_authorization_required'


def test_changed_script_cannot_reuse_authenticated_response(authorized_script):
    f=authorized_script; task=f['lease'](); nonce=secrets.token_hex(32); started=time.monotonic()
    response=f['authorize'](task,nonce=nonce).json()
    changed=copy.deepcopy(task); changed['input_data']['script']='print("replaced")'
    with pytest.raises(ValueError): permit_from_response(changed,response,nonce,started)
    with pytest.raises(ValueError): permit_from_response(task,response,'wrong-nonce',started)
    with pytest.raises(ValueError): permit_from_response(task,response,nonce,started-10)


@pytest.mark.parametrize('url',['http://vps.example','https://u:p@controller.example','https://controller.example/path',
                               'https://controller.example?redirect=elsewhere'])
def test_untrusted_controller_origins_are_rejected(url):
    with pytest.raises(ValueError): secure_controller_url(url)


def test_native_agent_leases_authorizes_executes_reviewed_fixture_and_submits(authorized_script,monkeypatch,tmp_path):
    f=authorized_script
    class Adapter:
        verify=True
        def post(self,url,**kwargs):
            kwargs['follow_redirects']=kwargs.pop('allow_redirects',False)
            return f['client'].post(url,**kwargs)
    config=AgentConfig(controller_url='http://127.0.0.1',worker_id=f['worker'],worker_token=f['token'],
        data_dir=tmp_path,allow_script_execution=True)
    agent=VPSWorkerAgent(config,OfflineBuffer(tmp_path/'buffer.db')); agent.is_registered=True
    agent.session=Adapter()
    # Actual reviewed code subprocess, explicitly NOT OS confinement evidence.
    monkeypatch.setattr(executor,'_detect_os_isolation_mechanism',lambda:'restricted_subprocess')
    result=agent.poll_and_execute_task()
    assert result['status']=='success'
    assert {a['filename'] for a in result['result_payload']['artifacts']}=={'index.html','result.json'}
    row=f['conn'].execute('SELECT * FROM worker_executions WHERE task_id=?',(f['task']['id'],)).fetchone()
    assert row and row['status']=='success' and row['exit_code']==0
    assert f['conn'].execute('SELECT COUNT(*) FROM worker_script_authorizations WHERE task_id=?',(f['task']['id'],)).fetchone()[0]==1


def test_local_script_flag_defaults_off(monkeypatch,tmp_path):
    monkeypatch.delenv('QC_WORKER_ALLOW_SCRIPT_EXECUTION',raising=False)
    config=AgentConfig(data_dir=tmp_path)
    assert config.allow_script_execution is False


@pytest.mark.parametrize('change',[
    {'network_access':True},{'allowed_capabilities':['python_execution','network']},
    {'max_runtime_seconds':True},{'max_runtime_seconds':3601}])
def test_valid_signature_does_not_waive_capability_bounds(authorized_script,change):
    import hashlib,hmac
    from app.config import settings
    f=authorized_script; task=f['lease'](); manifest=copy.deepcopy(task['execution_manifest']); manifest.update(change)
    manifest['signature']=hmac.new(settings.secret_key.encode(),json.dumps({k:v for k,v in manifest.items() if k!='signature'},sort_keys=True).encode(),hashlib.sha256).hexdigest()
    assert f['authorize'](task,manifest=manifest).status_code==403


def test_legacy_signing_cannot_authorize_nonlibrary_code(authorized_script):
    f=authorized_script; task=f['lease'](); script='print("unreviewed generated code")'
    f['conn'].execute('UPDATE tasks SET input_data_json=? WHERE id=?',(json.dumps({'script':script,'approved':True}),task['task_id']))
    f['conn'].commit()
    manifest=W.create_execution_manifest(task['task_id'],task['lease_id'],f['worker'],'python_execution',script,timeout_seconds=30)
    assert f['authorize'](task,manifest=manifest).status_code==403


def test_issued_permit_is_not_an_os_containment_waiver(authorized_script,monkeypatch):
    f=authorized_script; task=f['lease'](); nonce=secrets.token_hex(32); started=time.monotonic()
    response=f['authorize'](task,nonce=nonce)
    permit=permit_from_response(task,response.json(),nonce,started)
    monkeypatch.setattr(executor,'_detect_os_isolation_mechanism',lambda:None)
    result=executor.execute_task(task,script_permit=permit)
    assert result['error_category']=='unsupported_isolation'
    # A fresh connection/reconstructed client cannot obtain a second start.
    assert f['authorize'](task).status_code==403


def test_authorization_response_transport_cannot_redirect(authorized_script,tmp_path):
    f=authorized_script; task=f['lease']()
    class Redirect:
        status_code=302
    class Session:
        verify=True
        def post(self,url,**kwargs):
            assert kwargs['allow_redirects'] is False
            return Redirect()
    agent=VPSWorkerAgent(AgentConfig(controller_url='https://controller.example',worker_id=f['worker'],
        worker_token=f['token'],data_dir=tmp_path,allow_script_execution=True),OfflineBuffer(tmp_path/'offline.db'))
    agent.is_registered=True; agent.session=Session()
    assert agent._authorize_script(task) is None
    for insecure in (False,None,0,''):
        agent.session.verify=insecure
        assert agent._authorize_script(task) is None


def test_local_permit_expiry_and_identity_are_checked_at_consumption(authorized_script):
    f=authorized_script; task=f['lease']()
    expired=ScriptPermit(binding(task),'synthetic-expired',time.monotonic()-1,15)
    with pytest.raises(ValueError): expired.consume(task)
    other=copy.deepcopy(task); other['lease_id']='different'
    permit=ScriptPermit(binding(task),'synthetic-binding-test',time.monotonic()+5,15)
    with pytest.raises(ValueError): permit.consume(other)


def test_local_disabled_worker_does_not_advertise_scripts(authorized_script,monkeypatch,tmp_path):
    f=authorized_script
    class Session:
        def post(self,url,**kwargs):
            assert 'python_execution' not in kwargs['json']['supported_categories']
            class Response:
                status_code=200
                def json(self): return {'status':'no_work'}
            return Response()
    agent=VPSWorkerAgent(AgentConfig(data_dir=tmp_path,worker_token=f['token'],allow_script_execution=False),
        OfflineBuffer(tmp_path/'local-off.db'))
    agent.is_registered=True; agent.session=Session()
    assert agent.poll_and_execute_task() is None
