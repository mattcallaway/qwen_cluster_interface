"""Local reviewed-fixture rehearsal. Not native collective execution or isolation."""
from pathlib import Path
import base64,hashlib,json,os,sys,time
sys.path.insert(0,str(Path('work/qwen-candidate').resolve()))
os.environ['QC_ENV']='development'
from app.services.orchestration.script_library import get_script
from vps_agent import executor
library=get_script('web_status_pilot')
script=library['script']
# Deliberate developer-only runner for this fixed, source-reviewed fixture.
executor._detect_os_isolation_mechanism=lambda:'restricted_subprocess'
from vps_agent.script_authorization import ScriptPermit,binding
task={'task_id':'reviewed-local-web-pilot','task_type':'python_execution',
    'worker_id':'local-reviewed-fixture','lease_id':'synthetic-local-lease','idempotency_key':'synthetic-local-key',
    'input_data':{'script':script},'execution_manifest':{'task_id':'reviewed-local-web-pilot',
        'script_checksum':hashlib.sha256(script.encode()).hexdigest(),
        'allowed_capabilities':['python_execution']}}
permit=ScriptPermit(binding(task),'SYNTHETIC_LOCAL_FIXTURE_NOT_CONTROLLER_AUTH',time.monotonic()+5,15)
result=executor.execute_task(task,timeout_seconds=15,script_permit=permit)
assert result['exit_code']==0 and library['validator'](result['result_payload'])[0]
out=Path('outputs/web-pilot'); out.mkdir(parents=True,exist_ok=True)
for artifact in result['result_payload']['artifacts']:
    assert artifact['filename'] in {'index.html','result.json'}
    data=base64.b64decode(artifact['content_base64'],validate=True)
    assert hashlib.sha256(data).hexdigest()==artifact['sha256']
    (out/artifact['filename']).write_bytes(data)
(out/'execution.json').write_text(json.dumps({'native_execution':False,
    'generated_by_collective':False,'os_containment_verified':False,'certification_eligible':False,
    'controller_start_authorization_verified':False,
    'mode':'reviewed development fixture only','script_sha256':hashlib.sha256(script.encode()).hexdigest(),
    'result':result},indent=2),encoding='utf-8')
print('Reviewed fixture generated; native execution and certification remain unverified.')
