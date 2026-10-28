"""SSH transport for reviewed laptop test harness. Credentials never serialized."""
import ast,json,shlex
from pathlib import Path
import paramiko
root=Path(r'C:\Users\Matt C')
tree=ast.parse((root/'.gemini/antigravity-ide/scratch/qwen-collective/scratch/test_laptop_ssh.py').read_text())
values={}
for node in tree.body:
    if isinstance(node,ast.Assign):
        for target in node.targets:
            if isinstance(target,ast.Name) and target.id in {'USER','KEY_PATH','ips'}:values[target.id]=ast.literal_eval(node.value)
script=Path('work/laptop_linux_canary.py').read_text()
context={'backend':Path('work/qwen-candidate/vps_agent/linux_sandbox.py').read_text()}
previous=Path('outputs/laptop-linux-canary.json')
if previous.exists():
    previous_report=json.loads(previous.read_text())
    if previous_report.get('image_sha256'):
        context.update(reuse_image=previous_report['image_sha256'],repair_rootfs=previous_report.get('rootfs_directory',previous_report['audit_directory']+'/rootfs'))
        if previous_report.get('smoke_passed') and previous_report.get('isolation_denials_passed'):
            context.update(reuse_prepared_image=True,phase='resources')
inspection=Path('outputs/laptop-canary-preflight-inspection.json')
if inspection.exists():
    observed=json.loads(inspection.read_text())
    if len(observed['audit_children'])==1 and not observed['images'] and not observed['policy_exists']:
        context['resume_audit']=Path(observed['audit_children'][0]).name
# Execute trusted harness text from the authenticated channel, not a writable remote script path.
bootstrap='import sys,json;d=json.load(sys.stdin);sys.stdin=__import__("io").StringIO(json.dumps(d["context"]));exec(compile(d["script"],"<reviewed-laptop-canary>","exec"))'
client=paramiko.SSHClient();client.load_system_host_keys(str(root/'.ssh/known_hosts'))
client.set_missing_host_key_policy(paramiko.RejectPolicy())
try:
    client.connect(values['ips'][0],username=values['USER'],key_filename=values['KEY_PATH'],
        look_for_keys=False,allow_agent=False,timeout=6,banner_timeout=6,auth_timeout=6)
    incoming,out,err=client.exec_command('sudo -n /usr/bin/python3 -c '+shlex.quote(bootstrap),timeout=150)
    incoming.write(json.dumps({'script':script,'context':context}));incoming.flush();incoming.channel.shutdown_write()
    raw=out.read(131073);errors=err.read(4097);status=out.channel.recv_exit_status()
    if len(raw)>131072:raise RuntimeError('Reviewed harness output exceeded transport budget')
    report=json.loads(raw)
    Path('outputs/laptop-linux-canary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    if status:raise SystemExit(status)
finally:client.close()
