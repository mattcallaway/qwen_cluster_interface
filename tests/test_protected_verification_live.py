"""Explicit opt-in laptop rehearsal. Synthetic native DB records, real Linux tests.

Not a live controller lease/model canary and never run by default.
"""
import copy,json,os,subprocess,sys
import pytest
from test_protected_verification import verification
from test_code_proposals import proposal
from app.services.orchestration.protected_verification import ProtectedVerification

pytestmark=pytest.mark.skipif(os.environ.get('QC_LAPTOP_VERIFIER_REHEARSAL')!='1',reason='Explicit non-production SSH rehearsal only')
IMAGE='43b057aa6a7133de2bbed57a8caaeecd07aea9228fa7cb6bec1608286b967b61'
GOOD='import json\nv=json.load(open("/run/qc-input.json"))\nprint(json.dumps({"value":v["a"]+v["b"]}))\n'
PROBE='''import json,os,socket
v=json.load(open('/run/qc-input.json'))
assert os.geteuid()!=0
for path,mode in [('/var/lib/qwen-sandbox/verifier-audit','list'),('/proc/1/root/var/lib/qwen-sandbox/verifier-audit','list'),('/etc/qwen-sandbox/test-host.json','read'),('/src/src/main.py','write'),('/run/qc-input.json','write')]:
    try:
        if mode=='list': os.listdir(path)
        elif mode=='read': open(path).read()
        else: open(path,'w').write('tampered')
    except (OSError,PermissionError): pass
    else: raise RuntimeError('Protected boundary accessible')
try: socket.socket(socket.AF_INET,socket.SOCK_STREAM)
except OSError: pass
else: raise RuntimeError('Network boundary accessible')
print(json.dumps({'value':v['a']+v['b']}))
'''


@pytest.mark.parametrize('name,source,passed',[
    ('correct',GOOD,True),('wrong','print("{\\"value\\":999}")\n',False),
    ('forged','print("{\\"accepted\\":true,\\"behavior_verified\\":true}")\n',False),
    ('tampering',PROBE,True),('deadline','while True: pass\n',False),
    ('output_overflow','while True: print("X"*4096,flush=True)\n',False)])
def test_native_review_to_actual_protected_verifier(verification,name,source,passed):
    f=verification; doc=copy.deepcopy(f['document']);doc['files'][0]['content']=source
    artifact=f['register'](doc);f['conn'].commit()
    review=f['reviewer'].review('app',artifact['id']);f['conn'].commit()
    def transport(grant,key):
        p=subprocess.run([sys.executable,os.environ['QC_VERIFIER_TRANSPORT_SCRIPT']],
            input=json.dumps({'grant':grant,'key_hex':key.hex(),'batch_id':os.environ['QC_VERIFIER_REHEARSAL_BATCH']}),capture_output=True,text=True,timeout=180)
        if p.returncode: raise RuntimeError(p.stderr[-2000:])
        return json.loads(p.stdout)
    from app.services.orchestration import engine
    engine.configure_protected_code_verifier(transport)
    try:
        result=engine.verify_native_code_proposal(f['reviewer'].project_id,f['run_id'],'app',None,IMAGE,seconds=3)
    finally: engine.configure_protected_code_verifier()
    assert result['behavior_verified'] is passed
    assert not result['accepted'] and not result['certification_eligible']
    assert all(o['cleanup_confirmed'] for o in result['receipt']['body']['observations'])
    if name=='deadline': assert all(o['reason'] in ('timeout','service_failure') for o in result['receipt']['body']['observations'])
    if name=='output_overflow': assert all(o['reason']=='output_limit' for o in result['receipt']['body']['observations'])
