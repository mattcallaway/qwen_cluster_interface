"""Reviewed root test harness, sent over existing authenticated SSH stdin.

Creates only a new sandbox test namespace and short-lived policy. No installs,
existing service restarts, worker flags, registry changes or model execution.
"""
import hashlib,json,os,re,shutil,stat,subprocess,sys,tempfile,time,types,uuid
from pathlib import Path
from datetime import datetime,timedelta,timezone

def sha(data): return hashlib.sha256(data).hexdigest()
def command(argv,timeout=30):
    p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C'})
    if p.returncode: raise RuntimeError('Command failed: '+argv[0]+': '+p.stderr[:1000])
    return p.stdout
def services():
    return {name:command(['/usr/bin/systemctl','show',name,'--property=MainPID,ActiveState,SubState']).strip()
        for name in ('qwen-worker.service','ollama.service','qc-ollama-ts.service')}

def rerun(context,image_sha):
    root=Path('/var/lib/qwen-sandbox');policy=Path('/etc/qwen-sandbox/test-host.json')
    audit=root/'audit'/uuid.uuid4().hex;audit.mkdir(mode=0o700)
    image_root=Path(context['repair_rootfs'])
    assert re.fullmatch('/var/lib/qwen-sandbox/audit/[0-9a-f]{32}/rootfs',str(image_root)) and image_root.is_dir()
    if context.get('reuse_prepared_image'):
        image=root/'images'/(image_sha+'.raw');assert sha(image.read_bytes())==image_sha
    else:
        (image_root/'var/tmp').mkdir(parents=True,exist_ok=True)
        raw=audit/'candidate.raw'
        command(['/usr/bin/mksquashfs',str(image_root),str(raw),'-noappend','-all-root','-no-xattrs','-processors','1','-quiet'],timeout=90)
        image_sha=sha(raw.read_bytes());image=root/'images'/(image_sha+'.raw')
        if image.exists():
            assert sha(image.read_bytes())==image_sha;raw.unlink()
        else:raw.rename(image);image.chmod(0o444)
    report={'schema':'qc.linux_canary.v1','started_at':datetime.now(timezone.utc).isoformat(),
        'mode':'reviewed laptop fixtures only','generated_code_executed':False,'production_approved':False,
        'services_before':services(),'tests':[],'image_sha256':image_sha,'image_size_bytes':image.stat().st_size,
        'rootfs_directory':str(image_root),'backend_source_sha256':sha(context['backend'].encode()),'audit_directory':str(audit)}
    policy_doc={'schema':'qc.linux_test_host.v1','environment':'non-production',
        'machine_id_sha256':sha(Path('/etc/machine-id').read_bytes().strip()),
        'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(),'allowed_image_sha256':[image_sha]}
    with policy.open('x') as stream:json.dump(policy_doc,stream)
    policy.chmod(0o600);policy_hash=sha(policy.read_bytes())
    module=types.ModuleType('qc_linux_canary_backend');sys.modules[module.__name__]=module
    exec(compile(context['backend'],'<reviewed-linux-backend>','exec'),module.__dict__)
    class ObservedHost(module.SystemdHost):
        def __init__(self,spec):super().__init__(spec);self.observations=[]
        def observe(self):
            value=super().observe()
            sample={'manager':value}
            try:
                sample['effective_properties']=self._control(['show','--no-pager','--property=MemoryMax,MemorySwapMax,TasksMax,CPUQuotaPerSecUSec,RuntimeMaxUSec,KillMode,PrivateNetwork,RootImage,NoNewPrivileges'])
                group=Path('/sys/fs/cgroup'+self.spec.control_group)
                sample['kernel_limits']={name:(group/name).read_text().strip() for name in ('memory.max','memory.swap.max','pids.max','cpu.max')}
            except OSError:pass
            self.observations.append(sample);return value
    def run_case(name,source,seconds=8,memory=128,processes=16):
        data=json.dumps({'schema':'qc.code_bundle.v1','files':[{'path':'src/main.py','content':source}]},sort_keys=True).encode()
        source_sha=sha(data);snapshot=root/'inputs'/source_sha;(snapshot/'src').mkdir(parents=True,exist_ok=True)
        target=snapshot/'src/main.py'
        if target.exists():assert target.read_bytes()==source.encode()
        else:target.write_bytes(source.encode());target.chmod(0o444)
        spec=module.LinuxSpec(uuid.uuid4().hex,source_sha,image_sha,(('src/main.py',sha(source.encode())),),
            seconds=seconds,memory_mb=memory,processes=processes)
        host=ObservedHost(spec);result=module.run_linux_candidate(spec,host=host)
        result['observations']=(host.observations[:1]+host.observations[-1:]) if host.observations else []
        result['stdout_sha256']=sha(result.get('stdout','').encode());result['stdout']=result.get('stdout','')[:4096]
        result['stderr_sha256']=sha(result.get('stderr','').encode());result['stderr']=result.get('stderr','')[:4096]
        report['tests'].append({'name':name,'result':result});(audit/'report.json').write_text(json.dumps(report,indent=2))
        return result
    try:
        result=run_case('smoke','import json,os,time\nprint(json.dumps({"fixture":"linux-smoke","uid":os.getuid(),"pid":os.getpid()}),flush=True)\ntime.sleep(.5)\n')
        report['smoke_passed']=result['reason']=='completed' and result['exit_code']==0 and result['cleanup_confirmed']
        if report['smoke_passed'] and context.get('phase')=='resources':
            source='import time\nprint("memory fixture",flush=True)\ntime.sleep(.5)\ndata=bytearray(256*1024*1024)\nprint("allocation unexpectedly succeeded",flush=True)\n'
            result=run_case('memory_limit',source,memory=64)
            report['memory_limit_passed']=result['cleanup_confirmed'] and result['reason']!='completed'
            source='''import subprocess,sys,json,time
children=[];denied=False
time.sleep(.5)
try:
 for i in range(20):
  try:children.append(subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(2)']))
  except OSError:denied=True;break
finally:
 for child in children:
  child.terminate()
 for child in children:
  child.wait()
print(json.dumps({'pid_limit_denied':denied,'spawned':len(children)}),flush=True);time.sleep(.5)
'''
            result=run_case('pid_limit',source,processes=8)
            try:report['pid_limit_passed']=result['cleanup_confirmed'] and result['reason']=='completed' and json.loads(result['stdout'])['pid_limit_denied']
            except Exception:report['pid_limit_passed']=False
            source='import time\ntime.sleep(60)\n'
            data=json.dumps({'schema':'qc.code_bundle.v1','files':[{'path':'src/main.py','content':source}]},sort_keys=True).encode()
            source_sha=sha(data);snapshot=root/'inputs'/source_sha;(snapshot/'src').mkdir(parents=True,exist_ok=True)
            target=snapshot/'src/main.py'
            if target.exists():assert target.read_bytes()==source.encode()
            else:target.write_bytes(source.encode());target.chmod(0o444)
            spec=module.LinuxSpec(uuid.uuid4().hex,source_sha,image_sha,(('src/main.py',sha(source.encode())),),seconds=2)
            host=ObservedHost(spec);process=host.start();began=time.monotonic();ready=False
            until=began+1
            while time.monotonic()<until:
                try:
                    observed=host.observe()
                    if not observed['main_done']:ready=True;break
                except module.LinuxSandboxUnavailable:pass
                time.sleep(.02)
            process.kill();process.wait(timeout=3)
            ended=False;last=None
            until=began+6
            while time.monotonic()<until:
                try:
                    last=host.observe()
                    if last['main_done']:ended=True;break
                except module.LinuxSandboxUnavailable:pass
                time.sleep(.05)
            cleanup=False
            try:host.kill();host.stop();cleanup=host.empty()
            except Exception:pass
            process.stdout.close();process.stderr.close()
            report['tests'].append({'name':'client_loss','unit':spec.unit,'observed_running_before_client_loss':ready,
                'manager_after_client_loss':last,'service_ended_within_seconds':time.monotonic()-began,'cleanup_confirmed':cleanup,
                'observations':host.observations[:1]+host.observations[-1:]})
            report['client_loss_passed']=ready and ended and cleanup and last['service_result']=='timeout'
        elif report['smoke_passed']:
            sentinel=root/'host-only-sentinel';sentinel.write_text('synthetic-host-sentinel');sentinel.chmod(0o600)
            source='''import json,os,socket,time
from pathlib import Path
checks={'non_root':os.getuid()!=0,'host_sentinel_hidden':not Path('/var/lib/qwen-sandbox/host-only-sentinel').exists(),
 'credential_env_absent':not any(k in os.environ for k in ('QC_WORKER_TOKEN','QC_SECRET_KEY','SSH_AUTH_SOCK'))}
try:
 socket.socket(socket.AF_INET,socket.SOCK_STREAM);checks['network_socket_denied']=False
except OSError:checks['network_socket_denied']=True
try:
 open('/src/src/main.py','a');checks['source_read_only']=False
except OSError:checks['source_read_only']=True
try:
 open('/sys/fs/cgroup/cgroup.procs','w');checks['cgroup_write_denied']=False
except OSError:checks['cgroup_write_denied']=True
try:
 with open('/work/space-test','wb') as f:
  for i in range(40):f.write(b'x'*1048576)
 checks['scratch_limit']=False
except OSError:checks['scratch_limit']=True
print(json.dumps(checks),flush=True);time.sleep(.5)
'''
            result=run_case('isolation_denials',source)
            try:report['isolation_denials_passed']=all(json.loads(result['stdout']).values()) and result['cleanup_confirmed'] and result['exit_code']==0
            except Exception:report['isolation_denials_passed']=False
            if report['isolation_denials_passed']:
                run_case('deadline','import time\nprint("deadline fixture",flush=True)\ntime.sleep(60)\n',seconds=2)
                run_case('output_flood','import os\nwhile True:os.write(1,b"x"*4096)\n',seconds=4)
                run_case('orphan','import os,time\npid=os.fork()\nif pid==0:\n os.setsid();time.sleep(60)\nelse:\n time.sleep(.5)\n',seconds=4)
    finally:
        if policy.exists() and sha(policy.read_bytes())==policy_hash:policy.unlink()
        report['host_policy_removed']=not policy.exists();report['services_after']=services()
        report['existing_service_pids_unchanged']=report['services_before']==report['services_after']
        report['backend_verified']=False;report['finished_at']=datetime.now(timezone.utc).isoformat()
        (audit/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))

def main():
    context=json.load(sys.stdin)
    assert os.geteuid()==0
    root=Path('/var/lib/qwen-sandbox'); policy=Path('/etc/qwen-sandbox/test-host.json')
    # Refuse to overwrite any earlier test/deployment setup.
    if policy.exists(): raise RuntimeError('Existing host policy requires explicit reconciliation')
    if root.exists():
        if context.get('reuse_image'):
            image_hash=context['reuse_image']
            assert re.fullmatch('[0-9a-f]{64}',image_hash)
            image=root/'images'/(image_hash+'.raw')
            assert image.is_file() and sha(image.read_bytes())==image_hash
            return rerun(context,image_hash)
        previous=context.get('resume_audit')
        if (not previous or not re.fullmatch('[0-9a-f]{32}',previous)
                or {p.name for p in root.iterdir()}!={'images','inputs','audit'}
                or list((root/'images').iterdir()) or list((root/'inputs').iterdir())
                or {p.name for p in (root/'audit').iterdir()}!={previous}):
            raise RuntimeError('Unexpected sandbox state; refusing to overwrite')
    report={'schema':'qc.linux_canary.v1','started_at':datetime.now(timezone.utc).isoformat(),
        'mode':'reviewed laptop fixtures only','generated_code_executed':False,'production_approved':False,
        'services_before':services(),'tests':[],'created_paths':[]}
    root.mkdir(mode=0o755,exist_ok=True); (root/'images').mkdir(exist_ok=True); (root/'inputs').mkdir(exist_ok=True); (root/'audit').mkdir(mode=0o700,exist_ok=True)
    report['created_paths'].append(str(root)); audit=root/'audit'/uuid.uuid4().hex; audit.mkdir(mode=0o700)
    image_root=audit/'rootfs'; image_root.mkdir()
    for relative in ('usr/bin','usr/lib','lib','lib64','etc','src','work','tmp','var/tmp','dev','proc','sys','run','root','home'):
        (image_root/relative).mkdir(parents=True,exist_ok=True)
    copied=set()
    def copy_file(source,destination=None):
        source=Path(source); destination=destination or source.as_posix()
        if destination in copied:return
        copied.add(destination); target=image_root/destination.lstrip('/'); target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source.resolve(),target); target.chmod(stat.S_IMODE(source.resolve().stat().st_mode)&0o755)
    copy_file('/usr/bin/python3.12','/usr/bin/python3')
    ignored={'__pycache__','test','tests','idlelib','tkinter','ensurepip','venv','sitecustomize.py','usercustomize.py'}
    def ignores(directory,names):return [n for n in names if n in ignored or n.startswith('config-')]
    source_stdlib=Path('/usr/lib/python3.12')
    for file in source_stdlib.rglob('*'):
        parts=file.relative_to(source_stdlib).parts
        if file.is_symlink() and not any(p in ignored or p.startswith('config-') for p in parts):
            if not file.resolve().is_relative_to(source_stdlib):
                raise RuntimeError('Unexpected external stdlib symlink; review before including')
    shutil.copytree(source_stdlib,image_root/'usr/lib/python3.12',ignore=ignores,dirs_exist_ok=True)
    binaries=[Path('/usr/bin/python3.12')]+list((source_stdlib/'lib-dynload').glob('*.so'))
    dependencies=set()
    for binary in binaries:
        output=command(['/usr/bin/ldd',str(binary)],timeout=5)
        dependencies.update(re.findall(r'(/[A-Za-z0-9_./+-]+)\s+\(',output))
    for dependency in sorted(dependencies): copy_file(dependency)
    (image_root/'etc/passwd').write_text('root:x:0:0:root:/root:/usr/sbin/nologin\nnobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n')
    (image_root/'etc/group').write_text('root:x:0:\nnogroup:x:65534:\n')
    (image_root/'etc/nsswitch.conf').write_text('passwd: files\ngroup: files\nhosts: files\n')
    raw=audit/'candidate.raw'
    command(['/usr/bin/mksquashfs',str(image_root),str(raw),'-noappend','-all-root','-no-xattrs','-processors','1','-quiet'],timeout=90)
    image_sha=sha(raw.read_bytes()); image=root/'images'/(image_sha+'.raw'); raw.rename(image); image.chmod(0o444)
    report.update(image_sha256=image_sha,image_size_bytes=image.stat().st_size,
        image_recipe='Installed /usr/bin/python3.12, stdlib, ldd-resolved shared libraries; no host credentials/configuration',
        backend_source_sha256=sha(context['backend'].encode()))
    policy.parent.mkdir(mode=0o755,exist_ok=True)
    policy_doc={'schema':'qc.linux_test_host.v1','environment':'non-production',
        'machine_id_sha256':sha(Path('/etc/machine-id').read_bytes().strip()),
        'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(),'allowed_image_sha256':[image_sha]}
    with policy.open('x') as stream:json.dump(policy_doc,stream)
    policy.chmod(0o600); report['created_paths'].append(str(policy))
    policy_hash=sha(policy.read_bytes())
    module=types.ModuleType('qc_linux_canary_backend'); sys.modules[module.__name__]=module
    exec(compile(context['backend'],'<reviewed-linux-backend>','exec'),module.__dict__)
    # Store root-owned evidence before execution so a client interruption does
    # not lose the created-path and image inventory.
    (audit/'report.json').write_text(json.dumps(report,indent=2))
    def run_case(name,source,seconds=8,memory=128,processes=16):
        bundle=json.dumps({'schema':'qc.code_bundle.v1','files':[{'path':'src/main.py','content':source}]},sort_keys=True).encode()
        source_sha=sha(bundle); snapshot=root/'inputs'/source_sha
        (snapshot/'src').mkdir(parents=True,exist_ok=True)
        target=snapshot/'src/main.py'; target.write_bytes(source.encode()); target.chmod(0o444)
        spec=module.LinuxSpec(uuid.uuid4().hex,source_sha,image_sha,(('src/main.py',sha(source.encode())),),
            seconds=seconds,memory_mb=memory,processes=processes)
        result=module.run_linux_candidate(spec)
        report['tests'].append({'name':name,'result':result})
        (audit/'report.json').write_text(json.dumps(report,indent=2))
        return result
    try:
        result=run_case('smoke','import json,os,time\nprint(json.dumps({"fixture":"linux-smoke","uid":os.getuid(),"pid":os.getpid()}),flush=True)\ntime.sleep(.5)\n')
        report['smoke_passed']=(result['reason']=='completed' and result['exit_code']==0 and result['cleanup_confirmed'])
    finally:
        if policy.exists() and sha(policy.read_bytes())==policy_hash:policy.unlink()
        report['host_policy_removed']=not policy.exists()
        report['services_after']=services()
        report['existing_service_pids_unchanged']=report['services_before']==report['services_after']
        report['audit_directory']=str(audit)
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        report['backend_verified']=False
        (audit/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'status':'harness_error','error_type':type(exc).__name__,'detail':str(exc)[:1200],
            'backend_verified':False,'production_approved':False}))
        raise SystemExit(1)
