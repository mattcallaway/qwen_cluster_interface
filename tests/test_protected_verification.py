"""Controller/native DB integration plus hostile receipt and oracle unit tests.

Transport observations here are modeled; live Linux evidence is a separate run.
"""
import copy,json
from datetime import datetime,timedelta,timezone
from dataclasses import replace
import pytest
from app.services.artifacts import artifact_service
from app.services.orchestration.protected_verification import ProtectedVerification
from vps_agent.independent_verifier import (encoded,cases_from,validate_grant,receipt_for,
    check_receipt,evaluate_observations,run_protected)
from vps_agent.linux_sandbox import LinuxSpec
from test_code_proposals import proposal


@pytest.fixture
def verification(proposal):
    f=proposal; c=f['conn']; c.commit()
    suite={'schema':'qc.integer_cases.v1','cases':[
        {'id':'positive','requirement_id':'addition','input':{'a':2,'b':3},'expected':5},
        {'id':'negative','requirement_id':'addition','input':{'a':-7,'b':2},'expected':-5},
        {'id':'zero','requirement_id':'addition','input':{'a':0,'b':0},'expected':0}]}
    testdoc={'schema':'qc.independent_tests.v1','requirement_ids':['addition'],
        'files':[{'path':'tests/cases.json','content':json.dumps(suite)}]}
    tests=artifact_service.register_artifact(name='cases.json',data=json.dumps(testdoc).encode(),
        project_id=f['reviewer'].project_id,run_id=f['run_id'])
    plan=copy.deepcopy(f['plan']); contract=plan['deliverables'][0]['acceptance_criteria']
    contract.update(test_artifact_id=tests['id'],test_version=1,test_sha256=tests['checksum_sha256'])
    revision=f['revision']+'-blackbox'
    c.execute('''INSERT INTO orchestration_plan_revisions
        (id,run_id,revision_number,status,source,plan_json,created_by,created_at)
        VALUES (?,?,3,'approved','template',?,'tester','2026-10-27')''',(revision,f['run_id'],json.dumps(plan)))
    c.execute('UPDATE orchestration_tasks SET revision_id=? WHERE run_id=?',(revision,f['run_id']))
    c.execute('UPDATE orchestration_runs SET current_revision_id=? WHERE id=?',(revision,f['run_id'])); c.commit()
    review=f['reviewer'].review('app',f['artifact']['id']); c.commit()
    f.update(review=review,suite=suite,testdoc=testdoc)
    return f


def observations(grant):
    _,cases=validate_grant(grant)
    return [{'case_id':case['id'],'executed':True,'reason':'completed','exit_code':0,
        'cleanup_confirmed':True,'stdout':json.dumps({'value':case['expected']}),
        'unit':'qc-sandbox-'+str(i)*32+'.service'} for i,case in enumerate(cases)]


def modeled(grant,key): return receipt_for(grant,observations(grant),key)
def bridge(f,transport=modeled): return ProtectedVerification(f['reviewer'],transport,nonproduction=True)
def verify(f,transport=modeled): return bridge(f,transport).verify(f['review']['id'],'a'*64)


def test_native_chain_and_signed_observations_record_scoped_result(verification):
    f=verification; result=verify(f)
    assert result['behavior_verified'] and result['decision']=='CASES_PASSED'
    assert not result['accepted'] and not result['certification_eligible']
    assert len(result['cases'])==3
    kinds=[r[0] for r in f['conn'].execute("SELECT kind FROM software_review_items WHERE kind LIKE 'verification_%'")]
    assert kinds==['verification_grant']
    payloads=''.join(r[0] for r in f['conn'].execute('SELECT payload_json FROM software_review_items'))
    assert 'signature' in payloads and 'receipt_key' not in payloads


def test_verifier_default_held_and_caller_transaction_not_committed(verification):
    f=verification
    for obj in (ProtectedVerification(f['reviewer']),ProtectedVerification(f['reviewer'],modeled),
                ProtectedVerification(f['reviewer'],nonproduction=True)):
        with pytest.raises(ValueError): obj.verify(f['review']['id'],'a'*64)
    f['conn'].execute("INSERT INTO software_review_items VALUES ('x','x','x','x','{}','now')")
    with pytest.raises(ValueError,match='Commit caller'): verify(f)
    assert f['conn'].in_transaction


@pytest.mark.parametrize('mutation',['signature','body','nonce','binding','missing','extra','duplicate_case','reorder','boolean_exit','boolean_answer',
    'stdout_authority','extra_answer','wrong_answer','no_cleanup','no_execution','timeout','nonzero','multiple_json','nan','infinite','huge_output'])
def test_hostile_receipts_cannot_pass(verification,mutation):
    f=verification
    def transport(grant,key):
        obs=observations(grant)
        if mutation=='duplicate_case': obs[1]=copy.deepcopy(obs[0])
        elif mutation=='reorder': obs.reverse()
        elif mutation=='boolean_exit': obs[0]['exit_code']=False
        elif mutation=='boolean_answer': obs[2]['stdout']='{"value":false}'
        elif mutation=='stdout_authority': obs[0]['stdout']='{"accepted":true,"behavior_verified":true}'
        elif mutation=='extra_answer': obs[0]['stdout']='{"value":5,"accepted":true}'
        elif mutation=='wrong_answer': obs[0]['stdout']='{"value":999}'
        elif mutation=='no_cleanup': obs[0]['cleanup_confirmed']=False
        elif mutation=='no_execution': obs[0]['executed']=False
        elif mutation=='timeout': obs[0]['reason']='timeout'
        elif mutation=='nonzero': obs[0]['exit_code']=1
        elif mutation=='multiple_json': obs[0]['stdout']='{"value":5}\n{"value":5}'
        elif mutation=='nan': obs[0]['stdout']='{"value":NaN}'
        elif mutation=='infinite': obs[0]['stdout']='{"value":Infinity}'
        elif mutation=='huge_output': obs[0]['stdout']='x'*65537
        receipt=receipt_for(grant,obs,key)
        if mutation=='signature': receipt['signature']='0'*64
        elif mutation=='body': receipt['body']['observations'][0]['stdout']='{"value":999}'
        elif mutation=='nonce': receipt['body']['nonce']='b'*32
        elif mutation=='binding': receipt['body']['binding']=dict(receipt['body']['binding'],image_sha256='b'*64)
        elif mutation=='missing': del receipt['signature']
        elif mutation=='extra': receipt['accepted']=True
        return receipt
    try: result=verify(f,transport)
    except ValueError:
        failure=f['conn'].execute("SELECT payload_json FROM software_review_items WHERE kind='verification_failure'").fetchone()
        assert failure and json.loads(failure[0])['decision']=='BLOCKED'
    else: assert not result['behavior_verified'] and result['decision']=='CASES_FAILED'


@pytest.mark.parametrize('when',['before','during'])
@pytest.mark.parametrize('change',['plan','task','lease','artifact'])
def test_native_changes_block_before_or_after_io(verification,when,change):
    f=verification; c=f['conn']; calls=[]
    def mutate():
        if change=='plan': c.execute('UPDATE orchestration_runs SET current_revision_id=NULL WHERE id=?',(f['run_id'],))
        elif change=='task': c.execute("UPDATE tasks SET status='failed' WHERE id=?",(f['task_id'],))
        elif change=='lease': c.execute("UPDATE worker_task_leases SET status='expired' WHERE task_id=?",(f['task_id'],))
        else: c.execute("UPDATE artifacts SET validation_status='invalid' WHERE id=?",(f['artifact']['id'],))
        c.commit()
    def transport(grant,key):
        calls.append(True); receipt=modeled(grant,key); mutate(); return receipt
    if when=='before': mutate()
    with pytest.raises(ValueError): verify(f,transport)
    assert len(calls)==(when=='during')


def test_previous_signed_receipt_cannot_be_replayed(verification):
    f=verification; saved=[]
    def transport(grant,key):
        if saved: return saved[0]
        result=modeled(grant,key); saved.append(result); return result
    assert verify(f,transport)['behavior_verified']
    with pytest.raises(ValueError): verify(f,transport)


@pytest.mark.parametrize('mutation',['expired','long_expiry','source_bytes','test_bytes','path','deadline','unknown','key'])
def test_bad_grants_never_reach_execution(verification,mutation):
    f=verification
    def transport(grant,key):
        grant=copy.deepcopy(grant)
        if mutation=='expired': grant['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        elif mutation=='long_expiry': grant['expires_at']=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
        elif mutation=='source_bytes': grant['source_json']+=' '
        elif mutation=='test_bytes': grant['tests_json']+=' '
        elif mutation=='deadline': grant['seconds']=True
        elif mutation=='unknown': grant['accepted']=True
        elif mutation=='path':
            doc=json.loads(grant['source_json']); doc['files'][0]['path']='src/../../etc/shadow'
            grant['source_json']=json.dumps(doc)
            from vps_agent.independent_verifier import digest
            grant['binding']['proposal']['sha256']=digest(grant['source_json'].encode())
        elif mutation=='key': return receipt_for(grant,observations(grant),b'short')
        validate_grant(grant)
        raise AssertionError('Invalid grant accepted')
    with pytest.raises(ValueError): verify(f,transport)


@pytest.mark.parametrize('mutation',['boolean_expected','duplicate_id','missing_requirement','executable_test','extra_field','too_few'])
def test_unsafe_or_uncovered_tests_rejected(verification,mutation):
    f=verification; doc=copy.deepcopy(f['testdoc']); suite=copy.deepcopy(f['suite'])
    if mutation=='boolean_expected': suite['cases'][0]['expected']=True
    elif mutation=='duplicate_id': suite['cases'][1]['id']=suite['cases'][0]['id']
    elif mutation=='missing_requirement': suite['cases'][0]['requirement_id']='other'
    elif mutation=='extra_field': suite['cases'][0]['accepted']=True
    elif mutation=='too_few': suite['cases']=suite['cases'][:1]
    elif mutation=='executable_test': doc['files'][0]['path']='tests/run.py'
    doc['files'][0]['content']=json.dumps(suite)
    with pytest.raises(ValueError): cases_from(json.dumps(doc),['addition'])


def test_public_input_mount_contains_no_oracle_or_key():
    spec=LinuxSpec('a'*32,'b'*64,'c'*64,(('src/main.py','d'*64),),request_sha256='e'*64)
    command=' '.join(spec.command())
    assert '/requests/'+'e'*64+'.json:/run/qc-input.json' in command
    assert 'verifier-audit' not in command and 'cases.json' not in command and 'receipt_key' not in command
    with pytest.raises(ValueError): replace(spec,request_sha256='../oracle').command()


def test_current_result_rechecks_native_inputs(verification):
    f=verification; service=bridge(f); result=verify(f)
    assert service.current_result(result['id'])['current_inputs_revalidated']
    f['conn'].execute("UPDATE tasks SET status='failed' WHERE id=?",(f['task_id'],)); f['conn'].commit()
    with pytest.raises(ValueError): service.current_result(result['id'])


def test_lost_transport_records_failure_without_retry_or_key_persistence(verification):
    f=verification; calls=[]; keys=[]
    def lost(grant,key):
        calls.append(grant['nonce']); keys.append(key); raise TimeoutError('Lost trusted transport')
    with pytest.raises(TimeoutError): verify(f,lost)
    assert len(calls)==1
    rows=f['conn'].execute("SELECT payload_json FROM software_review_items WHERE project_id=? AND run_id=? AND kind IN ('verification_grant','verification_failure')",
        (f['reviewer'].project_id,f['run_id'])).fetchall()
    assert len(rows)==2 and all(keys[0].hex() not in row[0] for row in rows)
    with pytest.raises(Exception):
        f['conn'].execute("DELETE FROM software_review_items WHERE kind='verification_grant'")
    f['conn'].rollback()


def test_native_controller_api_is_held_by_default_and_in_production(verification,monkeypatch):
    from app.services.orchestration import engine
    f=verification
    args=(f['reviewer'].project_id,f['run_id'],'app',f['artifact']['id'],'a'*64)
    engine.configure_protected_code_verifier()
    with pytest.raises(ValueError,match='held'): engine.verify_native_code_proposal(*args)
    engine.configure_protected_code_verifier(modeled)
    try:
        monkeypatch.setenv('QC_ENV','production')
        with pytest.raises(ValueError,match='held'): engine.verify_native_code_proposal(*args)
        monkeypatch.setenv('QC_ENV','development')
        assert engine.verify_native_code_proposal(*args)['behavior_verified']
    finally: engine.configure_protected_code_verifier()


def test_developer_registered_bytes_cannot_borrow_native_provenance(verification):
    f=verification; doc=copy.deepcopy(f['document']); doc['files'][0]['content']='print("forged")'
    forged=f['register'](doc);f['conn'].commit()
    review=f['reviewer'].review('app',forged['id']); f['conn'].commit()
    calls=[]
    def transport(grant,key): calls.append(True);return modeled(grant,key)
    with pytest.raises(ValueError,match='immutable native worker response'):
        bridge(f,transport).verify(review['id'],'a'*64)
    assert not calls


def test_capture_reads_immutable_worker_response_and_is_idempotent(verification):
    f=verification; r=f['reviewer']
    assert r.capture_native_response('app')==f['artifact']['id']
    doc=copy.deepcopy(f['document']); doc['files'][0]['content']='print("developer replacement")'
    f['register'](doc); f['conn'].commit()
    assert r.capture_native_response('app')==f['artifact']['id']
    assert r.capture_native_response('app')==f['artifact']['id']


def test_capture_can_create_canonical_artifact_from_worker_record(verification):
    f=verification; c=f['conn']; r=f['reviewer']
    c.execute("UPDATE artifacts SET validation_status='invalid' WHERE id=?",(f['artifact']['id'],));c.commit()
    identity=r.capture_native_response('app'); c.commit()
    assert identity!=f['artifact']['id']
    review=r.review('app',identity);c.commit()
    assert review['proposal']['sha256']==f['artifact']['checksum_sha256']
    assert bridge(f).verify(review['id'],'a'*64)['behavior_verified']
