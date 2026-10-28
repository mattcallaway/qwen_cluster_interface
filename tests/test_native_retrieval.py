import copy
import hashlib
import json
import secrets
import socket
from uuid import uuid4
import httpx
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.database import get_db_connection, db_transaction
from app.services import projects as P, tasks as T
from app.services.worker import TaskAuthorizationError
from app.services.research import retrieval
from app.services.research import native_retrieval as native_module
from vps_agent import research_job
from vps_agent.research_runtime import retrieval as worker_retrieval
from app.services.research.feedback import ProjectEvidenceFeedback, install_feedback_schema
from app.services.research.native_retrieval import NativeRetrievalVerifier
from app.services.research.extraction import DocumentExtractor
from app.services.research.manager import ResearchManager
from app.services.research.planner import ResearchPlanner, ResearchBudget
from certification_fixtures import recorded_native_outputs
from test_worker_authorization_enforcement import _setup_node

URL='https://terms.example/grant'
TEXT='The pilot grant requires a cash match.'


@pytest.fixture
def native_fetch(monkeypatch,request):
    options=getattr(request,'param',{}) or {}
    pid,run,_=recorded_native_outputs({'deliverables':[]},{'objective':'Verify grant terms'})
    P.change_project_status(pid,'active','tester','Synthetic native retrieval test')
    path=P.create_path(pid,'tester',name='Evidence retrieval',rationale='Resolve a documented evidence gap')
    P.change_path_status(pid,path['id'],'approved','tester','Test authorization')
    P.select_path(pid,path['id'],'tester','Test authorization')
    stage=P.create_stage(pid,path['id'],'tester',name='Retrieve official terms')
    suffix=uuid4().hex; rid='rr-'+suffix; worker='fetch-worker-'+suffix; observer='observer-'+suffix
    conn=get_db_connection()
    install_feedback_schema(conn)
    conn.execute('''INSERT INTO research_runs (id,project_id,orchestration_run_id,status,created_at,updated_at)
        VALUES (?,?,?,'active','2026-10-26T00:00:00Z','2026-10-26T00:00:00Z')''',(rid,pid,run['id']))
    ledger=ProjectEvidenceFeedback(conn,pid,rid)
    ledger.add_requirement('match','What cash match is required?','budget')
    ledger.add_claim('claim',TEXT,['match'],'The pilot grant does not require a cash match.')
    proposal=ledger.propose_paths()['paths'][0]
    conn.commit()
    budget_manager=object.__new__(ResearchManager)
    budget_manager.create_or_get_budget(pid)
    inputs={'url':URL,'feedback_path_id':proposal['id']}
    task=T.create_task('Retrieve grant terms',task_type='research_fetch',assigned_worker=worker,
        project_id=pid,path_id=path['id'],stage_id=stage['id'],purpose='Resolve match requirement',
        expected_output='Raw and extracted retrieval hashes',completion_criteria='Native retrieval observed independently',
        input_data=inputs)
    assert task['status']=='awaiting_approval'
    assert T.approve_task(task['id'])
    token,token_id=_setup_node(conn,worker,role='general_worker',restrictions={
        'allowed_task_types':['research_fetch'],'allow_web_fetch':True,'allow_script_execution':False})
    with db_transaction() as c:
        c.execute('''INSERT INTO infrastructure_nodes
            (id,name,role,is_enabled,capability_restrictions_json,created_at,updated_at)
            VALUES (?,?,'controller',1,'{"allow_web_fetch":true}','2026-10-26','2026-10-26')''',(observer,observer))
        revision='rev-fetch-'+suffix
        c.execute('''INSERT INTO orchestration_plan_revisions
            (id,run_id,revision_number,status,source,plan_json,created_by,created_at)
            VALUES (?,?,2,'approved','template',?,'tester','2026-10-26T00:00:00Z')''',
            (revision,run['id'],json.dumps({'deliverables':([{'key':'budget','acceptance_criteria':json.dumps(options['claim_contract'])}]
                if options.get('claim_contract') else []),
                'tasks':[{'key':'fetch','action':{'type':'research_fetch',**inputs}},
                         {'key':'refresh','action':{'type':'research_fetch',**inputs}}]})))
        c.execute('UPDATE orchestration_runs SET current_revision_id=? WHERE id=?',(revision,run['id']))
        c.execute('''INSERT INTO orchestration_tasks
            (id,run_id,revision_id,plan_key,title,agent_role,state,current_task_id,dispatched_node,action_json,created_at,updated_at)
            VALUES (?,?,?,'fetch','Fetch terms','research','dispatched',?,?,?,'2026-10-26','2026-10-26')''',
            ('ot-fetch-'+suffix,run['id'],revision,task['id'],worker,json.dumps({'type':'research_fetch',**inputs})))
    # Actual fetcher/policy/extractor code runs against an in-memory HTTP transport.
    # Only DNS for synthetic .example names is stubbed; no public network is used.
    original_dns=socket.getaddrinfo
    monkeypatch.setattr(socket,'getaddrinfo',lambda host,port,*a,**k:
        [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',port))]
        if str(host).endswith('.example') else original_dns(host,port,*a,**k))
    state={'body':options.get('source_text',TEXT).encode(),'status':200,'content_type':'text/plain','location':None,'calls':0}
    def respond(request):
        state['calls']+=1
        if state.get('on_request'): state['on_request'](state['calls'])
        headers={'content-type':state['content_type']}
        if state['location']: headers['location']=state['location']
        return httpx.Response(state['status'],headers=headers,content=state['body'])
    monkeypatch.setattr(retrieval,'PinningHTTPTransport',lambda **kwargs:httpx.MockTransport(respond))
    monkeypatch.setattr(worker_retrieval,'PinningHTTPTransport',lambda **kwargs:httpx.MockTransport(respond))
    # Fast protocol tests exercise the shared operation in-process. Dedicated
    # deadline tests below exercise the actual killable process boundary.
    monkeypatch.setattr(native_module,'run_bounded_retrieval',research_job.perform_retrieval)
    verifier=NativeRetrievalVerifier(secrets.token_bytes(32),observer)
    ledger.receipt_verifier=verifier
    client=TestClient(app); auth={'Authorization':'Bearer '+token}
    def complete(payload_override=None,task_override=None):
        target=task_override or task
        response=client.post('/api/v1/worker/tasks/lease',json={'worker_id':worker,
            'supported_categories':['research_fetch']},headers=auth)
        assert response.status_code==200,response.text
        lease=response.json()['task']
        assert lease['task_id']==target['id']
        fetched=retrieval.SecureFetcher().fetch(URL)
        assert fetched.is_success
        document=DocumentExtractor().extract(fetched.content_bytes,fetched.content_type,fetched.final_url)
        payload={'schema':'qc.retrieval_result.v1','requested_url':URL,'final_url':fetched.final_url,
            'raw_sha256':hashlib.sha256(fetched.content_bytes).hexdigest(),
            'extracted_sha256':hashlib.sha256(document.clean_text.encode()).hexdigest()}
        payload.update(payload_override or {})
        submission=client.post('/api/v1/worker/tasks/'+target['id']+'/submit',headers=auth,json={
            'task_id':target['id'],'lease_id':lease['lease_id'],'idempotency_key':lease['idempotency_key'],
            'status':'success','exit_code':0,'result_payload':payload})
        assert submission.status_code==200,submission.text
        return submission.json()['execution_id']
    yield {'ledger':ledger,'verifier':verifier,'complete':complete,'state':state,'worker':worker,
        'observer':observer,'token_id':token_id,'client':client,'auth':auth,'task':task,'inputs':inputs}
    conn.rollback(); conn.close()


def test_native_http_lifecycle_and_feedback(native_fetch):
    f=native_fetch; eid=f['complete']()
    snapshot=f['ledger'].capture_native_retrieval(eid)
    result=f['ledger'].propose_paths()
    claim=result['assessment']['claims'][0]
    assert snapshot['provenance']=='CONTROLLER_OBSERVED_NATIVE_RETRIEVAL'
    assert claim['authenticated_content_count']==1
    assert claim['confidence']=='AUTHENTICATED_SOURCE_QUOTATION'
    assert claim['certification_eligible'] is False
    assert result['paths'][0]['reason']=='CLAIM_APPLICABILITY_REQUIRED'
    assert f['state']['calls']==2  # Worker fetch and independent controller observation.


def test_missing_native_execution_rejected_before_fetch(native_fetch):
    f=native_fetch
    with pytest.raises(ValueError,match='native retrieval chain'):
        f['verifier'].observe(f['ledger'],'invented-execution')
    assert f['state']['calls']==0


def test_forged_worker_hash_cannot_be_attested(native_fetch):
    f=native_fetch; eid=f['complete']({'raw_sha256':'0'*64})
    with pytest.raises(ValueError,match='disagree'):
        f['verifier'].observe(f['ledger'],eid)
    assert not f['ledger'].items('retrieval_receipt')


def test_changed_source_remains_unverified(native_fetch):
    f=native_fetch; eid=f['complete']()
    f['state']['body']=b'Terms changed between observations.'
    with pytest.raises(ValueError,match='disagree'):
        f['verifier'].observe(f['ledger'],eid)


@pytest.mark.parametrize('field',['raw_base64','extracted_text','requested_url','lease_id','worker_id','research_run_id'])
def test_signed_receipt_tampering_rejected(native_fetch,field):
    f=native_fetch; eid=f['complete'](); receipt=f['verifier'].observe(f['ledger'],eid)
    forged=copy.deepcopy(receipt); forged['body'][field]='tampered'
    with pytest.raises(ValueError): f['verifier'].verify(f['ledger'],forged)


def test_replay_does_not_refetch_or_duplicate(native_fetch):
    f=native_fetch; eid=f['complete']()
    first=f['ledger'].capture_native_retrieval(eid)
    second=f['ledger'].capture_native_retrieval(eid)
    assert first==second and f['state']['calls']==2
    assert len(f['ledger'].items('retrieval_receipt'))==1
    assert f['ledger'].conn.execute('SELECT docs_used FROM research_budgets WHERE project_id=?',
        (f['ledger'].project_id,)).fetchone()[0]==2


@pytest.mark.parametrize('policy_change',["is_enabled=0", "capability_restrictions_json='{\"allow_web_fetch\":false}'"])
def test_worker_hold_or_web_permission_revocation_blocks_observation(native_fetch,policy_change):
    f=native_fetch; eid=f['complete']()
    f['ledger'].conn.execute('UPDATE infrastructure_nodes SET '+policy_change+' WHERE id=?',(f['worker'],))
    with pytest.raises(TaskAuthorizationError): f['verifier'].observe(f['ledger'],eid)
    assert f['state']['calls']==1


def test_revoked_credentials_block_observation(native_fetch):
    f=native_fetch; eid=f['complete']()
    f['ledger'].conn.execute('UPDATE worker_auth_tokens SET revoked=1 WHERE id=?',(f['token_id'],))
    with pytest.raises(TaskAuthorizationError): f['verifier'].observe(f['ledger'],eid)
    assert f['state']['calls']==1


def test_observer_hold_blocks_network(native_fetch):
    f=native_fetch; eid=f['complete']()
    f['ledger'].conn.execute("UPDATE infrastructure_nodes SET capability_restrictions_json='{}' WHERE id=?",(f['observer'],))
    with pytest.raises(ValueError,match='hold'): f['verifier'].observe(f['ledger'],eid)
    assert f['state']['calls']==1


def test_private_redirect_is_rejected(native_fetch):
    f=native_fetch; eid=f['complete']()
    f['state']['status']=302; f['state']['location']='https://169.254.169.254/latest/meta-data/'
    with pytest.raises(ValueError,match='retrieval failed'):
        f['verifier'].observe(f['ledger'],eid)


def test_plain_source_snapshot_cannot_gain_authenticated_status(native_fetch):
    f=native_fetch; ledger=f['ledger']
    assert ledger.assess()['claims'][0]['authenticated_content_count']==0
    assert ledger.receipt_verifier is not None
    with pytest.raises(ValueError): NativeRetrievalVerifier(b'short',f['observer'])


def test_manager_bridge_persists_verified_feedback(native_fetch):
    f=native_fetch; eid=f['complete']()
    f['ledger'].conn.commit()
    manager=object.__new__(ResearchManager); manager.planner=ResearchPlanner()
    result=manager.prepare_native_retrieval_feedback(f['ledger'].project_id,f['ledger'].run_id,eid,f['verifier'])
    assert result['snapshot_id'] and result['assessment']['claims'][0]['authenticated_content_count']==1
    assert result['assessment']['certification_eligible'] is False


def test_restart_without_signing_authority_cannot_promote_provenance(native_fetch):
    f=native_fetch; eid=f['complete'](); f['ledger'].capture_native_retrieval(eid)
    fresh=ProjectEvidenceFeedback(f['ledger'].conn,f['ledger'].project_id,f['ledger'].run_id)
    assert fresh.assess()['claims'][0]['authenticated_content_count']==0


def test_hold_set_during_observation_is_rechecked(native_fetch):
    f=native_fetch; eid=f['complete']()
    def disable_observer(call):
        if call==2:
            f['ledger'].conn.execute('UPDATE infrastructure_nodes SET is_enabled=0 WHERE id=?',(f['observer'],))
            f['ledger'].conn.commit()
    f['state']['on_request']=disable_observer
    with pytest.raises(ValueError,match='not enabled'):
        f['verifier'].observe(f['ledger'],eid)
    assert not f['ledger'].items('retrieval_receipt')


def test_superseded_native_task_is_not_attested(native_fetch):
    f=native_fetch; eid=f['complete']()
    f['ledger'].conn.execute("UPDATE orchestration_tasks SET state='superseded' WHERE current_task_id=?",(f['task']['id'],))
    with pytest.raises(ValueError,match='native retrieval chain'):
        f['verifier'].observe(f['ledger'],eid)
    assert f['state']['calls']==1


def test_drift_from_approved_action_is_rejected(native_fetch):
    f=native_fetch; eid=f['complete']()
    f['ledger'].conn.execute("UPDATE orchestration_tasks SET action_json='{}' WHERE current_task_id=?",(f['task']['id'],))
    with pytest.raises(ValueError,match='approved plan'):
        f['verifier'].observe(f['ledger'],eid)
    assert f['state']['calls']==1


def test_wrong_signing_key_rejected(native_fetch):
    f=native_fetch; eid=f['complete'](); receipt=f['verifier'].observe(f['ledger'],eid)
    other=NativeRetrievalVerifier(secrets.token_bytes(32),f['observer'])
    with pytest.raises(ValueError,match='signature'): other.verify(f['ledger'],receipt)


def test_manager_exhausted_retrieval_budget_prevents_controller_fetch(native_fetch):
    f=native_fetch; eid=f['complete'](); f['ledger'].conn.commit()
    manager=object.__new__(ResearchManager); manager.planner=ResearchPlanner()
    result=manager.prepare_native_retrieval_feedback(f['ledger'].project_id,f['ledger'].run_id,eid,
        f['verifier'],budget=ResearchBudget(max_retrieved_documents=0))
    assert result['stop_reason']=='RESEARCH_BUDGET_EXHAUSTED'
    assert f['state']['calls']==1


def test_late_execution_cannot_be_attested(native_fetch):
    f=native_fetch; eid=f['complete']()
    f['ledger'].conn.execute("UPDATE worker_task_leases SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",(f['task']['id'],))
    with pytest.raises(ValueError,match='within its native lease'):
        f['verifier'].observe(f['ledger'],eid)
    assert f['state']['calls']==1


def test_producer_attempt_change_invalidates_receipt(native_fetch):
    f=native_fetch; eid=f['complete'](); receipt=f['verifier'].observe(f['ledger'],eid)
    f['ledger'].conn.execute('UPDATE orchestration_tasks SET attempts=attempts+1 WHERE current_task_id=?',(f['task']['id'],))
    with pytest.raises(ValueError,match='current native execution'):
        f['verifier'].verify(f['ledger'],receipt)


def test_verifier_enforces_persisted_budget_before_network(native_fetch):
    f=native_fetch; eid=f['complete']()
    f['ledger'].conn.execute('UPDATE research_budgets SET max_docs=0 WHERE project_id=?',(f['ledger'].project_id,))
    f['ledger'].conn.commit()
    with pytest.raises(ValueError,match='budget'):
        f['verifier'].observe(f['ledger'],eid)
    assert f['state']['calls']==1


def test_https_downgrade_is_rejected_before_following(native_fetch):
    f=native_fetch; eid=f['complete']()
    f['state']['status']=302; f['state']['location']='http://terms.example/insecure'
    with pytest.raises(ValueError,match='retrieval failed'):
        f['verifier'].observe(f['ledger'],eid)
    assert f['state']['calls']==2  # Initial worker fetch, then blocked controller redirect.


def test_followup_manager_revalidates_receipts_without_refetching(native_fetch):
    f=native_fetch; eid=f['complete'](); f['ledger'].capture_native_retrieval(eid)
    f['ledger'].conn.commit()
    manager=object.__new__(ResearchManager); manager.planner=ResearchPlanner()
    result=manager.prepare_evidence_feedback(f['ledger'].project_id,f['ledger'].run_id,receipt_verifier=f['verifier'])
    assert result['assessment']['claims'][0]['authenticated_content_count']==1
    assert f['state']['calls']==2
