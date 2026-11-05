import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from uuid import uuid4
import zipfile
import httpx
import pytest
import uvicorn
from app.main import app
from app.database import db_transaction,get_db_connection
from app.services import projects as P
from app.services.orchestration import engine,planner,states,approval_manager,model_router,task_graph
from app.services.research.feedback import ProjectEvidenceFeedback,install_feedback_schema
from app.services.research.planner import ResearchPlanner
from app.services.research.manager import ResearchManager
from app.services.research.native_retrieval import NativeRetrievalVerifier
from app.services.research import retrieval
from app.services.research import native_retrieval as native_module
from vps_agent import research_job
from test_worker_authorization_enforcement import _setup_node

ROOT=Path(__file__).resolve().parents[1]
TEXT='The pilot grant requires a cash match.'


def builder():
    spec=importlib.util.spec_from_file_location('worker_bundle_builder',ROOT/'scripts/build_retrieval_worker.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_vendor_parity_and_no_controller_dependency(tmp_path):
    package=tmp_path/'worker.zip'; builder().build(ROOT,package)
    with zipfile.ZipFile(package) as archive:
        assert not any(n.startswith('app/') or n.endswith('.env') for n in archive.namelist())
        manifest=json.loads(archive.read('vps_agent/research_runtime/runtime-manifest.json'))
        for entry in manifest:
            source=(ROOT/f'app/services/research/{entry["module"]}.py').read_bytes()
            output=archive.read(f'vps_agent/research_runtime/{entry["module"]}.py')
            assert entry['source_sha256']==hashlib.sha256(source).hexdigest()
            assert output.decode()==source.decode('utf-8-sig').replace('app.services.research.','vps_agent.research_runtime.')


def test_worker_bundle_excludes_local_json_credentials(tmp_path):
    root=tmp_path/'source'
    shutil.copytree(ROOT/'vps_agent',root/'vps_agent',ignore=shutil.ignore_patterns('__pycache__'))
    (root/'app/services/research').mkdir(parents=True)
    for name in ('cache','policy','retrieval','extraction'):
        shutil.copyfile(ROOT/f'app/services/research/{name}.py',root/f'app/services/research/{name}.py')
    shutil.copyfile(ROOT/'app/version.py',root/'app/version.py')
    (root/'vps_agent/local-credentials.json').write_text('{"test_only":"sentinel"}',encoding='utf-8')
    archive=tmp_path/'worker.zip'; builder().build(root,archive)
    with zipfile.ZipFile(archive) as z:
        assert 'vps_agent/local-credentials.json' not in z.namelist()


def test_worker_default_denies_retrieval():
    from vps_agent.executor import execute_task
    result=execute_task({'task_type':'research_fetch','task_id':'t','lease_id':'l','idempotency_key':'k',
        'input_data':{'url':'https://terms.example/','feedback_path_id':'x'}})
    assert result['status']=='error' and 'disabled' in result['stderr_summary']


def test_scheduler_and_packaged_worker_process_loop(tmp_path,monkeypatch):
    monkeypatch.setattr(engine,'_NATIVE_RETRIEVAL_VERIFIER',None)
    project=P.create_project('Worker process test '+uuid4().hex,'tester')
    pid=project['id']; run=engine.create_run(pid,'tester',{'objective':'Validate a source quotation'},autonomy_level=2)
    rid='research-'+uuid4().hex; worker='isolated-fetch-'+uuid4().hex; observer='observer-'+uuid4().hex
    with get_db_connection() as c:
        token,_=_setup_node(c,worker,role='general_worker',restrictions={
            'allowed_task_types':['research_fetch'],'allow_web_fetch':True,'allow_script_execution':False})
    with db_transaction() as c:
        c.execute('''INSERT INTO research_runs (id,project_id,orchestration_run_id,status,created_at,updated_at)
            VALUES (?,?,?,'active','2026-10-26T00:00:00Z','2026-10-26T00:00:00Z')''',(rid,pid,run['id']))
        c.execute('''INSERT INTO infrastructure_nodes
            (id,name,role,is_enabled,capability_restrictions_json,created_at,updated_at)
            VALUES (?,?,'controller',1,'{"allow_web_fetch":true}','2026-10-26','2026-10-26')''',(observer,observer))
        install_feedback_schema(c)
        ledger=ProjectEvidenceFeedback(c,pid,rid)
        ledger.add_requirement('match','What cash match is required?','source_record')
        ledger.add_claim('claim',TEXT,['match'])
        proposal=ledger.propose_paths()['paths'][0]
        task=ResearchPlanner().retrieval_task_from_feedback(ledger,proposal['id'],'https://terms.example/grant',worker,'retrieve')
    manager=ResearchManager(feedback_only=True); manager.create_or_get_budget(pid)
    plan={'schema':'qc.plan.v1','overview':{'objective':'Validate a source quotation'},
        'paths':[{'key':'evidence','name':'Evidence collection','rationale':'Resolve the registered evidence gap',
            'stages':[{'key':'retrieve','name':'Retrieve terms','objective':'Observe source bytes'}]}],
        'tasks':[task],'deliverables':[{'key':'source_record','name':'Source record','type':'report','stage':'retrieve',
            'produced_by':task['key'],'terminal':True,'acceptance_criteria':'Native source provenance; factual claims remain unverified'}]}
    states.transition(run['id'],'PLANNING','test_setup','tester')
    rev=planner.store_revision(run['id'],plan,'template','tester')
    assert rev['validation']['valid'],rev['validation']['errors']
    states.transition(run['id'],'AWAITING_PLAN_APPROVAL','test_setup','tester')
    approval_manager.approve_plan(run['id'],'tester','Approve the isolated test plan')
    engine.configure_native_retrieval_verifier(NativeRetrievalVerifier(secrets.token_bytes(32),observer))
    engine.start_run(run['id'],'tester')
    engine.tick(run['id'])
    with get_db_connection() as c:
        assert c.execute('SELECT count(*) FROM tasks WHERE project_id=?',(pid,)).fetchone()[0]==0
    approval=next(a for a in approval_manager.list_pending(run['id']) if a['kind']=='task')
    approval_manager.decide(run['id'],approval['id'],True,'tester','Authorize this isolated retrieval')
    engine.tick(run['id'])
    ot=engine._orch_tasks(run['id'])[0]
    assert ot['state']=='dispatched' and ot['current_task_id']

    # The packaged worker runs in a separate Python process, without app/ on its path.
    package=tmp_path/'worker.zip'; builder().build(ROOT,package)
    bundle=tmp_path/'bundle'
    with zipfile.ZipFile(package) as z: z.extractall(bundle)
    sock=socket.socket(); sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,lifespan='off',log_level='error',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True); thread.start()
    config=tmp_path/'child.json'; result=tmp_path/'result.json'
    config.write_text(json.dumps({'bundle_root':str(bundle),'controller_url':f'http://127.0.0.1:{port}',
        'worker_id':worker,'token':token,'data_dir':str(tmp_path/'worker-data'),
        'source_text':TEXT,'result_file':str(result)}),encoding='utf-8')
    child_config=json.loads(config.read_text())
    child_config['retrieval_driver']=str(ROOT/'tests/retrieval_process_driver.py')
    config.write_text(json.dumps(child_config),encoding='utf-8')
    try:
        deadline=time.monotonic()+5
        while not server.started and time.monotonic()<deadline: time.sleep(.02)
        assert server.started
        child=subprocess.run([sys.executable,'-I',str(ROOT/'tests/worker_process_driver.py'),str(config)],
            cwd=bundle,capture_output=True,text=True,timeout=25)
        assert child.returncode==0,child.stderr
        assert json.loads(result.read_text())['controller_imported'] is False
    finally:
        server.should_exit=True; thread.join(timeout=5); sock.close()
        config.unlink(missing_ok=True)
        assert not thread.is_alive(), 'Isolated controller did not shut down'
    original_dns=socket.getaddrinfo
    monkeypatch.setattr(socket,'getaddrinfo',lambda host,port,*a,**k:
        [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',port))]
        if str(host).endswith('.example') else original_dns(host,port,*a,**k))
    monkeypatch.setattr(retrieval,'PinningHTTPTransport',lambda **kwargs:httpx.MockTransport(lambda request:
        httpx.Response(200,headers={'content-type':'text/plain'},content=TEXT.encode())))
    monkeypatch.setattr(research_job,'_process_args',lambda:[sys.executable,'-I',
        str(ROOT/'tests/retrieval_process_driver.py'),str(ROOT),'good',TEXT])
    try:
        engine.tick(run['id'])
        fresh=engine._orch_tasks(run['id'])[0]
        assert fresh['state']=='accepted',fresh['last_error']
        accepted=json.loads(fresh['accepted_result_json'])
        assert accepted['assessment']['claims'][0]['authenticated_content_count']==1
        assert accepted['assessment']['certification_eligible'] is False
        assert accepted['paths'][0]['reason']=='CLAIM_APPLICABILITY_REQUIRED'
        assert engine.get_run(run['id'])['state']!='COMPLETED'
    finally:
        engine.configure_native_retrieval_verifier(None)


@pytest.mark.parametrize('local_permission',[False,True])
def test_worker_rejects_unleased_or_script_fields(local_permission):
    from vps_agent.executor import execute_task
    result=execute_task({'task_type':'research_fetch','task_id':'t','lease_id':'l','idempotency_key':'k',
        'input_data':{'url':'https://terms.example/','feedback_path_id':'x','script':'print(1)'}},
        allow_web_fetch=local_permission)
    assert result['status']=='error'


def test_graph_rejects_unapproved_or_unpinned_retrieval():
    task={'key':'fetch','action':{'type':'research_fetch','url':'https://terms.example/',
        'feedback_path_id':'a'*64},'allowed_nodes':[],'approval':'auto'}
    errors=[]; task_graph._validate_action(task,errors)
    assert any('approval' in error for error in errors)


def test_graph_disallows_automatic_retrieval_retries():
    task={'key':'fetch','action':{'type':'research_fetch','url':'https://terms.example/',
        'feedback_path_id':'a'*64},'allowed_nodes':['worker'],'approval':'required','max_attempts':3}
    errors=[]; task_graph._validate_action(task,errors)
    assert any('one attempt' in error for error in errors)


def test_router_denies_web_hold_even_with_task_allowlist():
    node={'id':'n','is_worker':True,'enabled':True,'authenticated':True,'health':'online',
        'restrictions':{'allow_web_fetch':True,'allowed_task_types':['research_fetch'],'operational_hold':True}}
    assert not model_router.eligible_nodes({'action':{'type':'research_fetch'}},[node])['eligible']
