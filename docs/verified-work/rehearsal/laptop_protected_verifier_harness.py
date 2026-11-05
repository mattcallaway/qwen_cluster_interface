"""Reviewed one-shot root verifier harness; no production configuration changes."""
import hashlib,json,sys,types,subprocess,os
from pathlib import Path
from datetime import datetime,timedelta,timezone
context=json.load(sys.stdin)
def cmd(args):
    return subprocess.run(args,capture_output=True,text=True,timeout=8,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C'})
def services():
    result={}
    for name in ('qwen-worker.service','ollama.service','qc-ollama-ts.service'):
        p=cmd(['/usr/bin/systemctl','show',name,'--property=MainPID,ActiveState,SubState'])
        if p.returncode: raise RuntimeError('Cannot inspect existing service')
        result[name]=p.stdout.strip()
    return result
package=types.ModuleType('vps_agent');package.__path__=[];sys.modules['vps_agent']=package
for name in ('linux_sandbox','independent_verifier'):
    module=types.ModuleType('vps_agent.'+name);sys.modules[module.__name__]=module
    exec(compile(context[name],'<reviewed-'+name+'>','exec'),module.__dict__)
verifier=sys.modules['vps_agent.independent_verifier']
grant=context['grant']; key=bytes.fromhex(context['key_hex'])
verifier.validate_grant(grant)
policy=Path('/etc/qwen-sandbox/test-host.json')
before=services(); units=[]; policy_created=False; receipt=None
original=verifier.run_linux_candidate
def observed(spec):
    units.append(spec.unit)
    return original(spec)
verifier.run_linux_candidate=observed
report={'schema':'qc.protected_verifier_live_test.v1','services_before':before,
    'batch_id':context['batch_id'],'started_at':datetime.now(timezone.utc).isoformat(),
    'native_binding':grant['binding'],'nonce':grant['nonce'],'units':[],
    'evidence_class':'reviewed synthetic source, native database fixture, actual protected Linux execution',
    'backend_sha256':hashlib.sha256(context['linux_sandbox'].encode()).hexdigest(),
    'verifier_sha256':hashlib.sha256(context['independent_verifier'].encode()).hexdigest(),
    'production_approved':False,'accepted':False,'certification_eligible':False}
try:
    assert os.geteuid()==0 and not policy.exists()
    verifier.protected_directory(policy.parent)
    policy_doc={'schema':'qc.linux_test_host.v1','environment':'non-production',
        'machine_id_sha256':hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest(),
        'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(),
        'allowed_image_sha256':[grant['binding']['image_sha256']]}
    with policy.open('x') as stream: json.dump(policy_doc,stream)
    policy.chmod(0o600); policy_created=True; policy_hash=hashlib.sha256(policy.read_bytes()).hexdigest()
    receipt=verifier.run_protected(grant,key)
    report['cases']=verifier.check_receipt(grant,receipt,key)
    try: verifier.run_protected(grant,key)
    except FileExistsError: report['root_nonce_replay_denied']=True
    else: raise AssertionError('Root verifier executed a consumed nonce')
finally:
    for unit in units:
        p=cmd(['/usr/bin/systemctl','show',unit,'--property=Id,MainPID,ControlGroup,Description,LoadState'])
        props=dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
        group=Path('/sys/fs/cgroup/system.slice')/unit
        empty=not group.exists() or 'populated 0' in (group/'cgroup.events').read_text()
        owned=props.get('Description','').startswith('QwenSandbox ') and props.get('Id')==unit
        safe=empty and props.get('MainPID','0')=='0'
        report['units'].append({'unit':unit,'empty':empty,'main_pid':props.get('MainPID'),
            'owned':owned,'safe':safe})
        if owned and safe: cmd(['/usr/bin/systemctl','reset-failed',unit])
    if policy_created:
        if hashlib.sha256(policy.read_bytes()).hexdigest()!=policy_hash: raise RuntimeError('Test policy changed unexpectedly')
        policy.unlink()
    report['policy_absent']=not policy.exists(); report['services_after']=services()
    report['existing_services_unchanged']=before==report['services_after']
if not report['policy_absent'] or not report['existing_services_unchanged'] or not all(u['safe'] for u in report['units']):
    raise RuntimeError('Post-test safety verification failed')
print(json.dumps({'receipt':receipt,'evidence':report}))
