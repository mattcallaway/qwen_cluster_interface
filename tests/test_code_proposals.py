import copy,json,sqlite3
from pathlib import Path
import pytest
from app.database import get_db_connection
from app.services.artifacts import artifact_service
from app.services.orchestration.code_proposals import CodeProposalReview,install_proposal_schema,digest,files_from
from certification_fixtures import recorded_native_outputs


@pytest.fixture
def proposal(db_conn,request):
    document={'schema':'qc.code_bundle.v1','files':[
        {'path':'src/main.py','content':'def add(a,b):\n    return a+b\n'},
        {'path':'web/index.html','content':'<!doctype html><title>Addition</title>\n'}]}
    # Live rehearsal parameters are reviewed synthetic fixtures, not model output.
    params=getattr(getattr(request.node,'callspec',None),'params',{})
    if 'source' in params: document['files'][0]['content']=params['source']
    pid,run,tested=recorded_native_outputs({'deliverables':[{'key':'app','name':'Tiny app'}]},
        {'objective':'Build an addition function'},inference=True,inference_response=json.dumps(document))
    conn=db_conn; install_proposal_schema(conn)
    old=conn.execute('SELECT * FROM artifacts WHERE id=?',(tested[0]['evidence_satisfied'][0]['id'],)).fetchone()
    tests_doc={'schema':'qc.independent_tests.v1','requirement_ids':['addition'],
        'files':[{'path':'tests/test_add.py','content':'assert add(2,3)==5\nassert add(-1,1)==0\n'}]}
    tests=artifact_service.register_artifact(name='acceptance.json',data=json.dumps(tests_doc).encode(),
        project_id=pid,run_id=run['id'])
    def register(doc):
        conn.commit()
        return artifact_service.register_artifact(name='proposal.json',data=json.dumps(doc).encode(),
            project_id=pid,run_id=run['id'],task_id=old['origin_task_id'],worker_id=old['origin_worker_id'])
    artifact=register(document)
    contract={'schema':'qc.software_contract.v1',
        'requirements':[{'id':'addition','description':'Return the exact sum of two integers.'}],
        'allowed_files':['src/main.py','web/index.html'],'test_artifact_id':tests['id'],
        'test_version':1,'test_sha256':tests['checksum_sha256']}
    row=conn.execute('SELECT id,plan_json FROM orchestration_plan_revisions WHERE run_id=?',(run['id'],)).fetchone()
    plan=json.loads(row['plan_json']); plan['deliverables'][0]['acceptance_criteria']=contract
    revision=row['id']+'-software'
    conn.execute('''INSERT INTO orchestration_plan_revisions
        (id,run_id,revision_number,status,source,plan_json,created_by,created_at)
        VALUES (?,?,2,'approved','template',?,'tester','2026-10-27')''',(revision,run['id'],json.dumps(plan)))
    conn.execute('UPDATE orchestration_tasks SET revision_id=? WHERE run_id=?',(revision,run['id']))
    conn.execute('UPDATE orchestration_runs SET current_revision_id=? WHERE id=?',(revision,run['id']))
    conn.commit()
    yield {'reviewer':CodeProposalReview(conn,pid,run['id']),'conn':conn,'document':document,
        'artifact':artifact,'tests':tests,'contract':contract,'register':register,
        'plan':plan,'revision':revision,'task_id':old['origin_task_id'],'run_id':run['id']}


def test_versioned_native_proposal_stages_without_executing(proposal,tmp_path):
    f=proposal; reviewer=f['reviewer']; review=reviewer.review('app',f['artifact']['id'])
    assert review['stageable'] and not review['accepted'] and not review['behavior_verified']
    assert review['approved_tests']['sha256']==f['contract']['test_sha256']
    staged=reviewer.stage(review['id'],tmp_path); root=Path(staged['directory'])
    assert (root/'proposal/src/main.py').read_text().startswith('def add')
    assert (root/'acceptance/tests/test_add.py').read_text().startswith('assert add')
    assert not staged['executed'] and not staged['certification_eligible']
    manifest=json.loads((root/'staging-manifest.json').read_bytes())
    assert manifest['review_id']==review['id']
    for namespace,key in [('proposal','proposal'),('acceptance','acceptance')]:
        for item in manifest[key]: assert digest((root/namespace/item['path']).read_bytes())==item['sha256']
    second=reviewer.stage(review['id'],tmp_path)
    assert second['directory']!=staged['directory']


@pytest.mark.parametrize('path',[
    '../escape.py','/src/main.py','C:/src/main.py','src/../main.py','src//main.py',
    'src\\main.py','src/file.py:stream','src/CON.py','src/com1.py','src/.git/config',
    'src/main.py.','src/package.json','src/setup.py','src/sitecustomize.py','src/conftest.py',
    'acceptance/test_add.py','tests/test_add.py','src/ｍain.py','src/main.exe',
])
def test_unsafe_paths_never_create_staging_files(proposal,tmp_path,path):
    f=proposal; document=copy.deepcopy(f['document']); document['files'][0]['path']=path
    artifact=f['register'](document)
    with pytest.raises(ValueError): f['reviewer'].review('app',artifact['id'])
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('mutation',['symlink','extra_field','duplicate','case_collision','missing','extra',
    'null_byte','oversize','nontext','authority_flag'])
def test_untrusted_bundle_shapes_are_rejected(proposal,mutation):
    f=proposal; doc=copy.deepcopy(f['document'])
    if mutation=='symlink': doc['files'][0]={'path':'src/main.py','symlink':'/etc/passwd'}
    if mutation=='extra_field': doc['files'][0]['mode']='executable'
    if mutation=='duplicate': doc['files'].append(copy.deepcopy(doc['files'][0]))
    if mutation=='case_collision': doc['files'].append({'path':'src/Main.py','content':'pass'})
    if mutation=='missing': doc['files'].pop()
    if mutation=='extra': doc['files'].append({'path':'src/new.py','content':'pass'})
    if mutation=='null_byte': doc['files'][0]['content']='\x00'
    if mutation=='oversize': doc['files'][0]['content']='x'*250001
    if mutation=='nontext': doc['files'][0]['content']={'instruction':'run'}
    if mutation=='authority_flag': doc['accepted']=True
    artifact=f['register'](doc)
    with pytest.raises((ValueError,TypeError)): f['reviewer'].review('app',artifact['id'])


@pytest.mark.parametrize('change',['plan','proposal_version','test_version','lease','worker','failed_task','action','cross_project',
                                   'version_validation','producer_metadata'])
def test_changed_bindings_require_revalidation(proposal,tmp_path,change):
    f=proposal; reviewer=f['reviewer']; review=reviewer.review('app',f['artifact']['id']); c=f['conn']
    if change=='plan':
        plan=copy.deepcopy(f['plan']); plan['deliverables'][0]['acceptance_criteria']['requirements'][0]['description']='Changed requirement'
        revision=f['revision']+'-changed'
        c.execute('''INSERT INTO orchestration_plan_revisions
            (id,run_id,revision_number,status,source,plan_json,created_by,created_at)
            VALUES (?,?,3,'approved','template',?,'tester','2026-10-27')''',(revision,f['run_id'],json.dumps(plan)))
        c.execute('UPDATE orchestration_runs SET current_revision_id=? WHERE id=?',(revision,f['run_id']))
    if change=='proposal_version': c.execute('UPDATE artifacts SET current_version_number=2 WHERE id=?',(f['artifact']['id'],))
    if change=='test_version': c.execute('UPDATE artifacts SET current_version_number=2 WHERE id=?',(f['tests']['id'],))
    if change=='lease': c.execute("UPDATE worker_task_leases SET status='expired' WHERE task_id=?",(f['task_id'],))
    if change=='worker': c.execute("UPDATE orchestration_tasks SET dispatched_node='other' WHERE current_task_id=?",(f['task_id'],))
    if change=='failed_task': c.execute("UPDATE tasks SET status='failed' WHERE id=?",(f['task_id'],))
    if change=='action': c.execute("UPDATE orchestration_tasks SET action_json='{}' WHERE current_task_id=?",(f['task_id'],))
    if change=='cross_project': reviewer.project_id='other'
    if change=='version_validation': c.execute("UPDATE artifact_versions SET validation_status='invalid' WHERE artifact_id=?",(f['artifact']['id'],))
    if change=='producer_metadata': c.execute('UPDATE artifacts SET producer_attempt=2 WHERE id=?',(f['artifact']['id'],))
    with pytest.raises(ValueError): reviewer.stage(review['id'],tmp_path)
    assert list(tmp_path.iterdir())==[]


def test_review_records_are_append_only(proposal):
    f=proposal; f['reviewer'].review('app',f['artifact']['id'])
    with pytest.raises(sqlite3.IntegrityError): f['conn'].execute('DELETE FROM software_review_items')


def test_declared_native_flags_cannot_replace_missing_lease(proposal):
    f=proposal; f['conn'].execute("UPDATE worker_task_leases SET status='expired' WHERE task_id=?",(f['task_id'],))
    with pytest.raises(ValueError): f['reviewer'].review('app',f['artifact']['id'])


def test_staging_never_executes_proposal(proposal,tmp_path):
    f=proposal; marker=tmp_path/'MUST_NOT_EXIST'
    doc=copy.deepcopy(f['document']); doc['files'][0]['content']=f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")'
    artifact=f['register'](doc); review=f['reviewer'].review('app',artifact['id'])
    f['reviewer'].stage(review['id'],tmp_path)
    assert not marker.exists()


def test_model_cannot_replace_pinned_acceptance_files(proposal):
    f=proposal; doc=copy.deepcopy(f['document']); doc['files'].append({'path':'tests/test_add.py','content':'assert True'})
    artifact=f['register'](doc)
    with pytest.raises(ValueError): f['reviewer'].review('app',artifact['id'])


def test_file_directory_collision_is_rejected():
    with pytest.raises(ValueError): files_from({'files':[{'path':'src/a.py','content':''},
                                                       {'path':'src/a.py/b.py','content':''}]})


def test_directory_case_aliases_are_rejected():
    with pytest.raises(ValueError): files_from({'files':[{'path':'src/Module/a.py','content':''},
                                                       {'path':'src/module/b.py','content':''}]})


def test_software_admission_records_proposal_review_but_never_certifies(proposal):
    from app.services.orchestration.evaluator import evaluate_deliverable_acceptance
    f=proposal
    result=evaluate_deliverable_acceptance({'key':'app','type':'software_artifact'},
        [{'id':f['artifact']['id'],'filename':'proposal.json'}],
        acceptance_criteria=f['contract'],project_id=f['reviewer'].project_id,run_id=f['run_id'])
    assert result['proposal_review']['stageable']
    assert not result['accepted'] and result['decision']=='BLOCKED' and result['fulfillment_score']==0


def test_forged_software_criteria_do_not_replace_approved_plan(proposal):
    from app.services.orchestration.evaluator import evaluate_deliverable_acceptance
    f=proposal; supplied=dict(f['contract'],accepted=True)
    result=evaluate_deliverable_acceptance({'key':'wrong','type':'software_artifact'},
        [{'id':f['artifact']['id']}],acceptance_criteria=supplied,
        project_id=f['reviewer'].project_id,run_id=f['run_id'])
    assert result['proposal_review'] is None and not result['accepted']
