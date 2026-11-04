"""Reconcile only known test-unit metadata; preserve evidence and existing services."""
import ast,json,shlex,re
from pathlib import Path
import paramiko
root=Path(r'C:\Users\Matt C');v={}
tree=ast.parse((root/'.gemini/antigravity-ide/scratch/qwen-collective/scratch/test_laptop_ssh.py').read_text())
for n in tree.body:
    if isinstance(n,ast.Assign):
        for t in n.targets:
            if isinstance(t,ast.Name) and t.id in {'USER','KEY_PATH','ips'}:v[t.id]=ast.literal_eval(n.value)
reports=[]
for name in ('laptop-linux-canary-namespace-failure.json','laptop-linux-isolation-tests.json','laptop-linux-canary.json'):
    p=json.loads((Path('outputs')/name).read_text());reports.append(p)
units=sorted({case.get('result',case)['unit'] for p in reports for case in p.get('tests',[])})
assert all(re.fullmatch(r'qc-sandbox-[0-9a-f]{32}\.service',u) for u in units)
probe='''import json,sys,subprocess
from pathlib import Path
units=json.load(sys.stdin);result={'units':[],'policy_absent':not Path('/etc/qwen-sandbox/test-host.json').exists()}
for unit in units:
 p=subprocess.run(['/usr/bin/systemctl','show',unit,'--property=Id,Description,LoadState,MainPID,ActiveState,ControlGroup'],capture_output=True,text=True,timeout=5)
 values=dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
 row={'unit':unit,'before':values}
 group=Path('/sys/fs/cgroup/system.slice')/unit
 row['cgroup_absent_or_empty']=not group.exists() or 'populated 0' in (group/'cgroup.events').read_text().splitlines()
 if values.get('LoadState')=='loaded':
  if not values.get('Description','').startswith('QwenSandbox ') or values.get('MainPID')!='0' or not row['cgroup_absent_or_empty']:
   raise RuntimeError('Unexpected active/unknown unit; refusing blind cleanup')
  for operation in ('stop','reset-failed'):
   done=subprocess.run(['/usr/bin/systemctl',operation,unit],capture_output=True,text=True,timeout=5)
   row[operation+'_exit']=done.returncode
 result['units'].append(row)
result['services_after']={}
for name in ('qwen-worker.service','ollama.service','qc-ollama-ts.service'):
 p=subprocess.run(['/usr/bin/systemctl','show',name,'--property=MainPID,ActiveState,SubState'],capture_output=True,text=True,timeout=5)
 result['services_after'][name]=p.stdout.strip()
result['retained_inactive_artifacts']=['/var/lib/qwen-sandbox/images','/var/lib/qwen-sandbox/inputs','/var/lib/qwen-sandbox/audit']
print(json.dumps(result))
'''
c=paramiko.SSHClient();c.load_system_host_keys(str(root/'.ssh/known_hosts'));c.set_missing_host_key_policy(paramiko.RejectPolicy())
try:
    c.connect(v['ips'][0],username=v['USER'],key_filename=v['KEY_PATH'],look_for_keys=False,allow_agent=False,timeout=6)
    incoming,out,err=c.exec_command('sudo -n /usr/bin/python3 -c '+shlex.quote(probe),timeout=60)
    incoming.write(json.dumps(units));incoming.flush();incoming.channel.shutdown_write()
    raw=out.read(32769);status=out.channel.recv_exit_status()
    if status or len(raw)>32768:raise RuntimeError('Scoped cleanup inspection failed')
    result=json.loads(raw)
    result['existing_service_pids_unchanged']=result['services_after']==reports[-1]['services_before']
    Path('outputs/laptop-linux-final-state.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'test_unit_count':len(result['units']),'all_cgroups_empty':all(u['cgroup_absent_or_empty'] for u in result['units']),
        'policy_absent':result['policy_absent'],'existing_service_pids_unchanged':result['existing_service_pids_unchanged']},indent=2))
finally:c.close()
