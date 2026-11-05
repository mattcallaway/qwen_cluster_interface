import copy
from datetime import datetime,timedelta,timezone
import json
from uuid import uuid4
import pytest
from app.database import db_transaction
from app.services import tasks
from app.services.artifacts import artifact_service
from app.services.orchestration import evaluator,engine
from app.services.research.claim_contracts import ClaimDocumentReview
from app.services.research import claim_contracts as contracts_module
from app.services.research.manager import ResearchManager
from app.services.research.planner import ResearchPlanner
from test_native_retrieval import native_fetch,URL,TEXT

CONTRACT={'schema':'qc.claim_contract.v1','claim_mode':'source_assertion','program':'pilot',
    'jurisdiction':'test-county','as_of':'2026-10-27','required_requirement_ids':['match'],
    'allowed_source_urls':[URL],'allowed_headings':['Scope','Evidence'],'max_snapshot_age_seconds':86400}


def card(statement=TEXT,**changes):
    scope={'schema':'qc.source_scope.v1','program':'pilot','jurisdiction':'test-county',
        'valid_from':'2026-01-01','valid_until':'2026-12-31'}
    scope.update(changes)
    return 'QC-Source-Scope: '+json.dumps(scope,sort_keys=True)+'\n'+statement


pytestmark=pytest.mark.parametrize('native_fetch',[{'source_text':card(),'claim_contract':CONTRACT}],indirect=True)


def document(text=TEXT):
    return {'schema':'qc.claim_document.v1','blocks':[{'kind':'heading','text':'Scope'},
        {'kind':'claim','claim_id':'claim','text':text}]}


def prepare(f,doc=None,source=None):
    if source is not None: f['state']['body']=source.encode()
    eid=f['complete']()
    ledger=f['ledger']; ledger.capture_native_retrieval(eid); ledger.conn.commit()
    run_id=ledger.conn.execute('SELECT orchestration_run_id FROM research_runs WHERE id=?',(ledger.run_id,)).fetchone()[0]
    data=(json.dumps(document() if doc is None else doc).encode())
    artifact=artifact_service.register_artifact(name='budget.claim.json',data=data,
        project_id=ledger.project_id,run_id=run_id)
    review=ClaimDocumentReview(ledger)
    binding=review.bind('budget',artifact['id'])
    return review,binding,artifact,run_id


def refresh(f,text):
    ledger=f['ledger']; ledger.conn.commit()
    base=f['task']
    task=tasks.create_task('Refresh source',task_type='research_fetch',assigned_worker=f['worker'],
        project_id=ledger.project_id,path_id=base['path_id'],stage_id=base['stage_id'],
        purpose='Investigate updated source evidence',expected_output='Raw/extracted hashes',
        completion_criteria='Native observation required',input_data=f['inputs'])
    assert tasks.approve_task(task['id'])
    with db_transaction() as c:
        original=c.execute('SELECT * FROM orchestration_tasks WHERE current_task_id=?',(base['id'],)).fetchone()
        c.execute('''INSERT INTO orchestration_tasks
            (id,run_id,revision_id,plan_key,title,agent_role,state,current_task_id,dispatched_node,action_json,created_at,updated_at)
            VALUES (?,?,?,'refresh','Refresh','research','dispatched',?,?,?,'2026-10-27','2026-10-27')''',
            ('ot-refresh-'+uuid4().hex,original['run_id'],original['revision_id'],task['id'],f['worker'],original['action_json']))
    f['state']['body']=text.encode()
    eid=f['complete'](task_override=task)
    ledger.capture_native_retrieval(eid)


def change_contract_before_evidence(f,updates):
    c=f['ledger'].conn
    row=c.execute('''SELECT rev.*,r.id AS orchestration_run_id FROM research_runs rr
        JOIN orchestration_runs r ON r.id=rr.orchestration_run_id
        JOIN orchestration_plan_revisions rev ON rev.id=r.current_revision_id WHERE rr.id=?''',
        (f['ledger'].run_id,)).fetchone()
    plan=json.loads(row['plan_json']); contract=copy.deepcopy(CONTRACT); contract.update(updates)
    plan['deliverables'][0]['acceptance_criteria']=json.dumps(contract)
    rid='revision-'+uuid4().hex
    c.execute('''INSERT INTO orchestration_plan_revisions
        (id,run_id,revision_number,status,source,plan_json,created_by,created_at)
        VALUES (?,?,3,'approved','template',?,'tester','2026-10-27')''',
        (rid,row['orchestration_run_id'],json.dumps(plan)))
    c.execute('UPDATE orchestration_runs SET current_revision_id=? WHERE id=?',(rid,row['orchestration_run_id']))
    c.execute('UPDATE orchestration_tasks SET revision_id=? WHERE current_task_id=?',(rid,f['task']['id']))
    c.commit()


def test_complete_bound_source_assertion_review(native_fetch):
    review,binding,artifact,run_id=prepare(native_fetch)
    result=review.evaluate(binding['id'])
    assert result['coverage_complete'] is True
    assert result['artifact_sha256']==artifact['checksum_sha256']
    assert result['contract_sha256']==binding['contract_sha256']
    assert result['occurrences'][0]['pointer']=='/blocks/1/text'
    assert result['occurrences'][0]['status']=='APPLICABLE_SOURCE_ASSERTION'
    assert result['requirement_entailment_verified'] is False
    assert result['certification_eligible'] is False and result['decision']=='BLOCKED'


@pytest.mark.parametrize('change,issue',[
    ({'program':'another-program'},'PROGRAM_MISMATCH'),
    ({'jurisdiction':'another-county'},'JURISDICTION_MISMATCH'),
    ({'valid_until':'2025-12-31'},'OUTSIDE_EFFECTIVE_PERIOD'),
    ({'valid_from':'2027-01-01'},'OUTSIDE_EFFECTIVE_PERIOD'),
    ({'valid_from':'yesterday'},'UNVERIFIABLE_SOURCE_SCOPE')])
def test_scoped_applicability_mismatch_is_blocked(native_fetch,change,issue):
    review,binding,_,_=prepare(native_fetch,source=card(**change))
    result=review.evaluate(binding['id'])
    assert result['coverage_complete'] is False
    assert issue in result['occurrences'][0]['applicability_issues']


def test_plain_prose_scope_is_not_inferred(native_fetch):
    review,binding,_,_=prepare(native_fetch,source=TEXT)
    result=review.evaluate(binding['id'])
    assert not result['coverage_complete']
    assert 'UNSUPPORTED_SOURCE_CARD' in result['occurrences'][0]['applicability_issues']


@pytest.mark.parametrize('mutation',['extra_prose','changed_text','unknown_claim','unapproved_heading','unknown_block','empty'])
def test_uninventoried_document_content_cannot_pass(native_fetch,mutation):
    doc=document()
    if mutation=='extra_prose': doc['notes']='An unsupported deadline is tomorrow.'
    if mutation=='changed_text': doc['blocks'][1]['text']='The pilot grant does not require a cash match.'
    if mutation=='unknown_claim': doc['blocks'][1]['claim_id']='unknown'
    if mutation=='unapproved_heading': doc['blocks'][0]['text']='All funds have been awarded.'
    if mutation=='unknown_block': doc['blocks'].append({'kind':'paragraph','text':'Unsupported claim.'})
    if mutation=='empty': doc['blocks']=[]
    review,binding,_,_=prepare(native_fetch,doc=doc)
    assert not review.evaluate(binding['id'])['coverage_complete']


def test_repeated_claim_occurrences_are_each_inventoried(native_fetch):
    doc=document(); doc['blocks'].append(copy.deepcopy(doc['blocks'][1]))
    review,binding,_,_=prepare(native_fetch,doc=doc)
    result=review.evaluate(binding['id'])
    assert result['coverage_complete']
    assert [o['pointer'] for o in result['occurrences']]==['/blocks/1/text','/blocks/2/text']


def test_applicable_conflict_invalidates_previous_review(native_fetch):
    review,binding,_,_=prepare(native_fetch)
    first=review.evaluate(binding['id']); assert first['coverage_complete']
    refresh(native_fetch,card('The pilot grant does not require a cash match.'))
    changed=review.evaluate(binding['id'])
    assert not changed['coverage_complete']
    assert 'APPLICABLE_EVIDENCE_CONFLICT' in changed['errors']
    assert first['id']!=changed['id']
    assert native_fetch['ledger'].items('deliverable_invalidation')
    assert len(native_fetch['ledger'].items('deliverable_assessment'))==2


def test_wrong_jurisdiction_counterstatement_does_not_become_applicable_fact(native_fetch):
    review,binding,_,_=prepare(native_fetch)
    refresh(native_fetch,card('The pilot grant does not require a cash match.',jurisdiction='other-county'))
    result=review.evaluate(binding['id'])
    assert result['coverage_complete']
    assert not result['occurrences'][0]['contradictions']
    assert 'JURISDICTION_MISMATCH' in result['occurrences'][0]['applicability_issues']


def test_artifact_version_change_invalidates_old_binding(native_fetch):
    review,binding,artifact,run_id=prepare(native_fetch)
    assert review.evaluate(binding['id'])['coverage_complete']
    native_fetch['ledger'].conn.commit()
    doc=document(); doc['blocks'].append({'kind':'paragraph','text':'Unregistered new conclusion.'})
    artifact_service.create_artifact_version(artifact['id'],json.dumps(doc).encode(),run_id=run_id)
    result=review.evaluate(binding['id'])
    assert 'ARTIFACT_CHANGED_OR_UNAVAILABLE' in result['errors']
    fresh=review.bind('budget',artifact['id'])
    assert fresh['artifact_version']==2 and fresh['id']!=binding['id']
    assert not review.evaluate(fresh['id'])['coverage_complete']


def test_new_contract_revision_invalidates_binding(native_fetch):
    review,binding,_,run_id=prepare(native_fetch)
    assert review.evaluate(binding['id'])['coverage_complete']
    c=native_fetch['ledger'].conn
    row=c.execute('SELECT plan_json FROM orchestration_plan_revisions WHERE id=?',(binding['plan_revision_id'],)).fetchone()
    plan=json.loads(row[0]); changed=copy.deepcopy(CONTRACT); changed['as_of']='2027-01-01'
    plan['deliverables'][0]['acceptance_criteria']=json.dumps(changed)
    rid='new-revision-'+uuid4().hex
    c.execute('''INSERT INTO orchestration_plan_revisions
        (id,run_id,revision_number,status,source,plan_json,created_by,created_at)
        VALUES (?,?,3,'approved','template',?,'tester','2026-10-27')''',(rid,run_id,json.dumps(plan)))
    c.execute('UPDATE orchestration_runs SET current_revision_id=? WHERE id=?',(rid,run_id))
    assert 'CONTRACT_OR_PLAN_CHANGED' in review.evaluate(binding['id'])['errors']


def test_producer_metadata_change_invalidates_binding(native_fetch):
    review,binding,artifact,_=prepare(native_fetch)
    assert review.evaluate(binding['id'])['coverage_complete']
    native_fetch['ledger'].conn.execute('UPDATE artifact_versions SET producer_attempt=producer_attempt+1 WHERE artifact_id=?',
        (artifact['id'],))
    assert 'PRODUCER_OR_VERSION_BINDING_CHANGED' in review.evaluate(binding['id'])['errors']


def test_evaluator_cannot_waive_research_hold(native_fetch,monkeypatch):
    review,binding,artifact,run_id=prepare(native_fetch)
    assert review.evaluate(binding['id'])['coverage_complete']
    native_fetch['ledger'].conn.commit()
    monkeypatch.setattr(engine,'_NATIVE_RETRIEVAL_VERIFIER',native_fetch['verifier'])
    result=evaluator.evaluate_deliverable_acceptance({'key':'budget','name':'Budget source assertions','type':'report'},
        [{'id':artifact['id'],'filename':'budget.claim.json'}],acceptance_criteria=CONTRACT,
        project_id=native_fetch['ledger'].project_id,run_id=run_id)
    assert result['accepted'] is False and result['decision']=='BLOCKED'
    assert result['claim_review']['coverage_complete'] is True


def test_feedback_creates_internal_revalidation_path(native_fetch):
    review,binding,_,_=prepare(native_fetch); review.evaluate(binding['id'])
    refresh(native_fetch,card('The pilot grant does not require a cash match.'))
    result=native_fetch['ledger'].propose_paths(max_paths=10,max_cycles=10)
    path=next(p for p in result['paths'] if p['reason']=='REVALIDATE_DELIVERABLE')
    assert path['action']=='revalidate_deliverable' and path['artifact_version']==1
    with pytest.raises(ValueError,match='internal revalidation'):
        ResearchPlanner().retrieval_task_from_feedback(native_fetch['ledger'],path['id'],URL,native_fetch['worker'],'retrieve')


def test_missing_receipt_authority_downgrades_review(native_fetch):
    review,binding,_,_=prepare(native_fetch)
    assert review.evaluate(binding['id'])['coverage_complete']
    native_fetch['ledger'].receipt_verifier=None
    result=review.evaluate(binding['id'])
    assert not result['coverage_complete']
    assert 'AUTHENTICATED_RETRIEVAL_REQUIRED' in result['occurrences'][0]['applicability_issues']


def test_approved_source_url_is_exact(native_fetch):
    change_contract_before_evidence(native_fetch,{'allowed_source_urls':['https://other.example/grant']})
    review,binding,_,_=prepare(native_fetch)
    result=review.evaluate(binding['id'])
    assert not result['coverage_complete']
    assert 'UNAPPROVED_SOURCE_URL' in result['occurrences'][0]['applicability_issues']


def test_missing_requested_requirement_is_not_covered_by_category_words(native_fetch):
    native_fetch['ledger'].add_requirement('deadline','What is the application deadline?','budget')
    native_fetch['ledger'].conn.commit()
    change_contract_before_evidence(native_fetch,{'required_requirement_ids':['match','deadline']})
    review,binding,_,_=prepare(native_fetch)
    assert 'REQUIREMENT_COVERAGE_INCOMPLETE' in review.evaluate(binding['id'])['errors']


def test_expired_snapshot_invalidates_review(native_fetch,monkeypatch):
    review,binding,_,_=prepare(native_fetch)
    assert review.evaluate(binding['id'])['coverage_complete']
    future=datetime.now(timezone.utc)+timedelta(days=2)
    class FutureClock(datetime):
        @classmethod
        def now(cls,tz=None): return future
    monkeypatch.setattr(contracts_module,'datetime',FutureClock)
    result=review.evaluate(binding['id'])
    assert not result['coverage_complete']
    assert 'SOURCE_STALE_OR_FUTURE' in result['occurrences'][0]['applicability_issues']


def test_duplicate_json_keys_cannot_hide_unsupported_content(native_fetch):
    review,binding,artifact,run_id=prepare(native_fetch)
    native_fetch['ledger'].conn.commit()
    raw=json.dumps(document()).replace('"schema": "qc.claim_document.v1"',
        '"schema":"unsupported","schema":"qc.claim_document.v1"')
    artifact_service.create_artifact_version(artifact['id'],raw.encode(),run_id=run_id)
    changed=review.bind('budget',artifact['id'])
    assert not review.evaluate(changed['id'])['coverage_complete']


def test_manager_records_current_review(native_fetch):
    review,binding,artifact,_=prepare(native_fetch)
    native_fetch['ledger'].conn.commit()
    result=ResearchManager(feedback_only=True).review_claim_document(native_fetch['ledger'].project_id,
        native_fetch['ledger'].run_id,'budget',artifact['id'],native_fetch['verifier'])
    assert result['coverage_complete'] and result['decision']=='BLOCKED'


def test_verified_scope_moves_feedback_to_remaining_certification_gate(native_fetch):
    review,binding,_,_=prepare(native_fetch); review.evaluate(binding['id'])
    result=native_fetch['ledger'].propose_paths(max_paths=10,max_cycles=10)
    assert any(p['reason']=='CERTIFICATION_GATE_REMAINS' for p in result['paths'])
    assert not any(p['reason']=='CLAIM_APPLICABILITY_REQUIRED' for p in result['paths'])


def test_unbound_artifact_cannot_use_caller_audit_flag(native_fetch,monkeypatch):
    review,binding,artifact,run_id=prepare(native_fetch)
    ledger=native_fetch['ledger']; ledger.conn.commit()
    other=artifact_service.register_artifact(name='unbound.claim.json',data=json.dumps(document()).encode(),
        project_id=ledger.project_id,run_id=run_id)
    monkeypatch.setattr(engine,'_NATIVE_RETRIEVAL_VERIFIER',native_fetch['verifier'])
    result=evaluator.evaluate_deliverable_acceptance({'key':'budget','type':'report'},
        [{'id':other['id'],'filename':'unbound.claim.json','validation_state':'valid'}],
        acceptance_criteria={**CONTRACT,'valid':True},project_id=ledger.project_id,run_id=run_id)
    assert result['accepted'] is False and result['claim_review'] is None


def test_contract_cannot_hide_facts_in_heading_allowlist(native_fetch):
    change_contract_before_evidence(native_fetch,{'allowed_headings':['All funds have been awarded.']})
    with pytest.raises(ValueError,match='structural labels'): prepare(native_fetch)


def test_missing_cas_bytes_invalidates_bound_review(native_fetch):
    review,binding,_,_=prepare(native_fetch)
    assert review.evaluate(binding['id'])['coverage_complete']
    # Remove only this synthetic test artifact's CAS object, never a source blob.
    artifact_service.storage.get_cas_path(binding['cas_hash']).unlink()
    result=review.evaluate(binding['id'])
    assert not result['coverage_complete'] and 'UNVERIFIABLE_DOCUMENT_OR_BINDING' in result['errors']


def test_admission_gate_rechecks_new_evidence_instead_of_cached_success(native_fetch,monkeypatch):
    review,binding,artifact,run_id=prepare(native_fetch)
    assert review.evaluate(binding['id'])['coverage_complete']
    refresh(native_fetch,card('The pilot grant does not require a cash match.'))
    native_fetch['ledger'].conn.commit()
    monkeypatch.setattr(engine,'_NATIVE_RETRIEVAL_VERIFIER',native_fetch['verifier'])
    result=evaluator.evaluate_deliverable_acceptance({'key':'budget','type':'report'},
        [{'id':artifact['id'],'filename':'budget.claim.json'}],acceptance_criteria=CONTRACT,
        project_id=native_fetch['ledger'].project_id,run_id=run_id)
    assert result['accepted'] is False
    assert not result['claim_review']['coverage_complete']
    assert 'APPLICABLE_EVIDENCE_CONFLICT' in result['claim_review']['errors']


def test_general_factual_or_financial_contract_is_not_silently_supported(native_fetch):
    change_contract_before_evidence(native_fetch,{'claim_mode':'financial_calculation'})
    with pytest.raises(ValueError,match='Unsupported claim contract'): prepare(native_fetch)
