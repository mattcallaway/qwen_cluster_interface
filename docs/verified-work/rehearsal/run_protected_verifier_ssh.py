"""Authenticated test transport. Key/grant are stdin-only; private key is never read into reports."""
import json,os,shlex,sys
from pathlib import Path
import paramiko
script_path=Path(__file__).resolve()
repo_root=script_path.parents[3] if len(script_path.parents)>3 and (script_path.parents[3]/'vps_agent').exists() else script_path.parents[1]
vps_dir=repo_root/'vps_agent' if (repo_root/'vps_agent').exists() else (repo_root/'work/qwen-candidate/vps_agent')
harness_file=script_path.parent/'laptop_protected_verifier_harness.py' if (script_path.parent/'laptop_protected_verifier_harness.py').exists() else (repo_root/'work/laptop_protected_verifier_harness.py')
out_dir=Path(os.environ['QC_VERIFIER_OUTPUT_DIR']) if os.environ.get('QC_VERIFIER_OUTPUT_DIR') else ((repo_root/'docs/verified-work/evidence') if (repo_root/'docs/verified-work/evidence').exists() else (repo_root/'outputs'))
out_dir.mkdir(parents=True,exist_ok=True)
request=json.load(sys.stdin)
context={**request,**{name:(vps_dir/(name+'.py')).read_text() for name in ('linux_sandbox','independent_verifier')}}
harness=harness_file.read_text()
bootstrap='import sys,json,io;d=json.load(sys.stdin);sys.stdin=io.StringIO(json.dumps(d["context"]));exec(compile(d["script"],"<reviewed-protected-verifier>","exec"))'
client=paramiko.SSHClient()
client.load_system_host_keys(r'C:\Users\Matt C\.ssh\known_hosts');client.set_missing_host_key_policy(paramiko.RejectPolicy())
try:
    client.connect('100.101.187.115',username='emceemcee',key_filename=r'C:\Users\Matt C\.ssh\id_ed25519',
        look_for_keys=False,allow_agent=False,timeout=8,banner_timeout=8,auth_timeout=8)
    incoming,out,err=client.exec_command('sudo -n /usr/bin/python3 -c '+shlex.quote(bootstrap),timeout=175)
    incoming.write(json.dumps({'script':harness,'context':context}));incoming.flush();incoming.channel.shutdown_write()
    raw=out.read(600001); errors=err.read(4097); status=out.channel.recv_exit_status()
    if len(raw)>600000 or status:
        # Remote errors contain only reviewed harness messages; never print stdin/key.
        raise RuntimeError('Protected verifier transport failed: '+errors.decode(errors='replace')[-1500:])
    response=json.loads(raw)
    report=response['evidence']; report['receipt']=response['receipt']
    (out_dir/('laptop-verifier-'+request['grant']['nonce']+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(response['receipt']))
finally: client.close()
