import base64
import hashlib
import json
import pytest
from vps_agent import executor
from app.services.orchestration import evaluator,script_library
from app.services.orchestration.software_pilot import HTML,INDEX_SHA256
from script_permit_fixtures import reviewed_fixture_permit


def run_reviewed(script,monkeypatch):
    # Tests execute only reviewed fixture code, not model-generated code.
    # This subprocess helper is explicitly NOT an OS containment claim.
    monkeypatch.setattr(executor,'_detect_os_isolation_mechanism',lambda:'restricted_subprocess')
    task,permit=reviewed_fixture_permit({'task_id':'reviewed-fixture','task_type':'python_execution',
        'input_data':{'script':script},'execution_manifest':{'task_id':'reviewed-fixture',
            'script_checksum':hashlib.sha256(script.encode()).hexdigest(),
            'allowed_capabilities':['python_execution']}})
    return executor.execute_task(task,timeout_seconds=10,script_permit=permit)


def test_reviewed_library_pilot_generates_exact_physical_files(monkeypatch):
    library=script_library.get_script('web_status_pilot')
    result=run_reviewed(library['script'],monkeypatch)
    assert result['exit_code']==0
    payload=result['result_payload']
    files={a['filename']:a for a in payload['artifacts']}
    assert set(files)=={'index.html','result.json'}
    assert files['index.html']['sha256']==INDEX_SHA256
    assert base64.b64decode(files['index.html']['content_base64']).decode()==HTML
    assert library['validator'](payload)[0]
    assert payload['execution_class']=='Class B'


@pytest.mark.parametrize('spoof',[
    {'returncode':0,'execution_class':'trusted','isolation_mechanism':'certified'},
    {'artifacts':[{'filename':'fake.py','sha256':'a'*64}]},
    {'accepted':True,'verified':True,'decision':'ACCEPTED'},
    {'task_id':'other','lease_id':'fake','worker_id':'admin'},
])
def test_stdout_cannot_replace_execution_evidence(monkeypatch,spoof):
    script='print('+repr(json.dumps(spoof))+')\nraise SystemExit(7)\n'
    result=run_reviewed(script,monkeypatch); payload=result['result_payload']
    assert result['exit_code']==7 and result['status']=='error'
    assert payload['returncode']==7 and payload['execution_class']=='Class B'
    assert payload['isolation_mechanism']=='restricted_subprocess'
    assert payload['artifacts']==[]
    assert payload['application_output']==spoof
    assert not payload.get('accepted') and not payload.get('verified')
    assert 'lease_id' not in payload


def test_large_physical_files_are_not_collected(monkeypatch):
    result=run_reviewed('from pathlib import Path\nPath("huge.bin").write_bytes(b"x"*1000001)',monkeypatch)
    assert result['result_payload']['artifacts']==[]


@pytest.mark.parametrize('dtype',['code','software_artifact'])
@pytest.mark.parametrize('criteria',[{}, {'require_execution':False,'accepted':True},
                                    {'schema':'qc.software_receipt.v1','verified':True}])
def test_software_admission_cannot_certify_static_code(tmp_path,dtype,criteria):
    code=tmp_path/'main.py'; code.write_text('def add(a,b): return a-b\n')
    result=evaluator.evaluate_deliverable_acceptance({'key':'app','type':dtype},
        [{'filename':'main.py','storage_path':str(code)}],acceptance_criteria=criteria)
    assert not result['accepted'] and result['decision']=='BLOCKED'
    assert result['blocked_reason']=='SOFTWARE_ACCEPTANCE_UNVERIFIED'
    assert result['fulfillment_score']==0


def test_missing_production_isolation_still_refuses_execution(monkeypatch):
    monkeypatch.setattr(executor,'_detect_os_isolation_mechanism',lambda:None)
    script=script_library.get_script('web_status_pilot')['script']
    result=executor.execute_task({'task_id':'held','task_type':'python_execution',
        'input_data':{'script':script},'execution_manifest':{'task_id':'held',
            'script_checksum':hashlib.sha256(script.encode()).hexdigest()}},timeout_seconds=10)
    assert result['error_category']=='unsupported_isolation'
