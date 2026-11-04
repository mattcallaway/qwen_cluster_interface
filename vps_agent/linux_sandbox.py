"""Candidate Linux cgroup service backend; NOT wired to worker dispatch.

Execution requires an explicit non-production operator harness on Linux with a
prepared immutable root image and source snapshot. No flags, claimed receipts,
or import of this module enable generated code or production certification.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib,json,os,re,stat,subprocess,threading,time,uuid
from pathlib import Path,PurePosixPath


class LinuxSandboxUnavailable(RuntimeError): pass
def sha(data): return hashlib.sha256(data).hexdigest()
def hex_digest(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value): raise ValueError('Exact SHA256 required')
    return value


@dataclass(frozen=True)
class LinuxSpec:
    job_id:str
    source_sha256:str
    image_sha256:str
    source_files:tuple
    seconds:int=15
    memory_mb:int=256
    processes:int=32
    request_sha256:str=None

    def validate(self):
        if not isinstance(self.job_id,str) or not re.fullmatch('[0-9a-f]{32}',self.job_id): raise ValueError('Random job identity required')
        hex_digest(self.source_sha256); hex_digest(self.image_sha256)
        if self.request_sha256 is not None: hex_digest(self.request_sha256)
        if (type(self.seconds) is not int or not 1<=self.seconds<=30
                or type(self.memory_mb) is not int or not 64<=self.memory_mb<=512
                or type(self.processes) is not int or not 2<=self.processes<=32): raise ValueError('Bounded resource policy required')
        if not isinstance(self.source_files,tuple) or not 0<len(self.source_files)<=64: raise ValueError('Bound source inventory required')
        seen=set()
        for path,digest in self.source_files:
            if (not isinstance(path,str) or not re.fullmatch(r'(src|web|docs)/[A-Za-z0-9_./-]+',path)
                    or any(p in ('','.','..') or p.startswith('.') for p in path.split('/'))
                    or path.casefold() in seen): raise ValueError('Unsafe/duplicate source path')
            seen.add(path.casefold()); hex_digest(digest)
        if 'src/main.py' not in {p for p,_ in self.source_files}: raise ValueError('Initial Python profile requires src/main.py')
        return self

    @property
    def unit(self): self.validate(); return 'qc-sandbox-'+self.job_id+'.service'
    @property
    def source_root(self): return '/var/lib/qwen-sandbox/inputs/'+hex_digest(self.source_sha256)
    @property
    def image(self): return '/var/lib/qwen-sandbox/images/'+hex_digest(self.image_sha256)+'.raw'
    @property
    def control_group(self): return '/system.slice/'+self.unit

    def command(self,owner_token=None):
        self.validate()
        if owner_token is not None and not re.fullmatch('[0-9a-f]{32}',owner_token): raise ValueError('Invalid owner token')
        policy={
            'Description':'QwenSandbox '+(owner_token or 'unstarted-'+self.job_id),
            'Type':'exec','Slice':'system.slice','Restart':'no','RemainAfterExit':'yes','NotifyAccess':'none',
            'RuntimeMaxSec':str(self.seconds),'TimeoutStartSec':'5','TimeoutStopSec':'2',
            'KillMode':'control-group','SendSIGKILL':'yes','FinalKillSignal':'SIGKILL',
            'MemoryMax':str(self.memory_mb)+'M','MemorySwapMax':'0','TasksMax':str(self.processes),'CPUQuota':'100%',
            'RootImage':self.image,'RootImageOptions':'ro,nosuid,nodev','DynamicUser':'yes',
            'NoNewPrivileges':'yes','CapabilityBoundingSet':'','AmbientCapabilities':'',
            'ProtectSystem':'strict','ProtectHome':'yes','ProtectControlGroups':'yes',
            'ProtectProc':'invisible','ProcSubset':'pid',
            'ProtectKernelTunables':'yes','ProtectKernelModules':'yes','ProtectKernelLogs':'yes',
            'PrivateNetwork':'yes','IPAddressDeny':'any','RestrictAddressFamilies':'AF_UNIX',
            'PrivateDevices':'yes','PrivateTmp':'yes','RestrictSUIDSGID':'yes','LockPersonality':'yes',
            'RestrictNamespaces':'yes','RestrictRealtime':'yes','UMask':'0077',
            'BindReadOnlyPaths':self.source_root+':/src',
            'TemporaryFileSystem':'/work:rw,nosuid,nodev,size=32M,mode=1777 /tmp:rw,nosuid,nodev,size=8M,mode=1777 /var/tmp:rw,nosuid,nodev,size=8M,mode=1777',
            'WorkingDirectory':'/work','Environment':'PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1',
            'StandardInput':'null'}
        if self.request_sha256 is not None:
            policy['BindReadOnlyPaths']+=' /var/lib/qwen-sandbox/requests/'+self.request_sha256+'.json:/run/qc-input.json'
        command=['/usr/bin/systemd-run','--system','--quiet','--pipe','--wait','--unit='+self.unit]
        for key,value in policy.items(): command+=['--property='+key+'='+value]
        # No model-selected argv, shell, host interpreter or test mount.
        return command+['--','/usr/bin/python3','-I','-B','/src/src/main.py']


class SystemdHost:
    """Explicit operator harness adapter. Never constructed by worker startup."""
    def __init__(self,spec):
        self.spec=spec.validate(); self.cgroup_seen=False; self.owner_token=uuid.uuid4().hex

    def _protected_regular(self,path):
        path=Path(path)
        for component in (path,*path.parents):
            info=component.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
                raise LinuxSandboxUnavailable('Runtime inputs/ancestors must be root-owned and not group/world writable')
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1: raise LinuxSandboxUnavailable('Regular non-linked input required')

    def _test_host_authorization(self):
        path=Path('/etc/qwen-sandbox/test-host.json'); self._protected_regular(path)
        with path.open('rb') as stream: raw=stream.read(16385)
        if len(raw)>16384: raise LinuxSandboxUnavailable('Test-host policy exceeds budget')
        def pairs(items):
            value={}
            for key,item in items:
                if key in value: raise ValueError('Duplicate policy key')
                value[key]=item
            return value
        try:
            policy=json.loads(raw,object_pairs_hook=pairs)
            if (set(policy)!={'schema','environment','machine_id_sha256','expires_at','allowed_image_sha256'}
                    or policy['schema']!='qc.linux_test_host.v1' or policy['environment']!='non-production'
                    or policy['machine_id_sha256']!=sha(Path('/etc/machine-id').read_bytes().strip())):
                raise ValueError('Host identity or environment mismatch')
            expiry=datetime.fromisoformat(policy['expires_at'].replace('Z','+00:00'))
            if expiry.tzinfo is None or not 0<(expiry-datetime.now(timezone.utc)).total_seconds()<=604800:
                raise ValueError('Test authorization expired or too broad')
            allowed=policy['allowed_image_sha256']
            if (not isinstance(allowed,list) or not 0<len(allowed)<=8 or len(set(allowed))!=len(allowed)
                    or any(hex_digest(value)!=value for value in allowed) or self.spec.image_sha256 not in allowed):
                raise ValueError('Runtime image is not approved for this host')
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            raise LinuxSandboxUnavailable('Explicit bounded non-production host/image approval required') from exc

    def preflight(self):
        if os.name!='posix' or not hasattr(os,'geteuid') or os.geteuid()!=0:
            raise LinuxSandboxUnavailable('Explicit privileged non-production Linux harness required')
        self._test_host_authorization()
        if self.spec.request_sha256 is not None:
            request=Path('/var/lib/qwen-sandbox/requests')/(self.spec.request_sha256+'.json')
            self._protected_regular(request)
            with request.open('rb') as stream: content=stream.read(4097)
            if len(content)>4096 or sha(content)!=self.spec.request_sha256:
                raise LinuxSandboxUnavailable('Request integrity failure')
        if not Path('/sys/fs/cgroup/cgroup.controllers').is_file(): raise LinuxSandboxUnavailable('cgroup v2 unavailable')
        for binary in ('/usr/bin/systemd-run','/usr/bin/systemctl'):
            self._protected_regular(binary)
        self._protected_regular(self.spec.image)
        h=hashlib.sha256()
        with open(self.spec.image,'rb') as stream:
            for chunk in iter(lambda:stream.read(1_048_576),b''): h.update(chunk)
        if h.hexdigest()!=self.spec.image_sha256: raise LinuxSandboxUnavailable('Root image digest mismatch')
        source=Path(self.spec.source_root); expected={p for p,_ in self.spec.source_files}
        observed=set()
        for path in source.rglob('*'):
            if path.is_symlink(): raise LinuxSandboxUnavailable('Linked source inputs forbidden')
            if path.is_file(): observed.add(path.relative_to(source).as_posix())
        if observed!=expected: raise LinuxSandboxUnavailable('Exact staged input inventory required')
        for relative,digest in self.spec.source_files:
            path=source/relative; self._protected_regular(path)
            with path.open('rb') as stream: content=stream.read(250_001)
            if len(content)>250_000 or sha(content)!=digest: raise LinuxSandboxUnavailable('Source bytes changed')
        return True

    def start(self):
        self.preflight()
        return subprocess.Popen(self.spec.command(owner_token=self.owner_token),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C'},close_fds=True)

    def _control(self,arguments):
        # Fixed system-manager properties only; never consumes application output.
        result=subprocess.run(['/usr/bin/systemctl','--system',*arguments,self.spec.unit],
            capture_output=True,timeout=3,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C'})
        if result.returncode or len(result.stdout)>8192: raise LinuxSandboxUnavailable('System manager query/control failed')
        return result.stdout.decode('utf-8')

    def observe(self):
        raw=self._control(['show','--no-pager','--property=Id,Description,LoadState,ActiveState,SubState,ControlGroup,MainPID,ExecMainCode,ExecMainStatus,Result'])
        values={}
        for line in raw.splitlines():
            key,separator,value=line.partition('=')
            if not separator or key in values: raise LinuxSandboxUnavailable('Invalid manager observation')
            values[key]=value
        required={'Id','Description','LoadState','ActiveState','SubState','ControlGroup','MainPID','ExecMainCode','ExecMainStatus','Result'}
        if (set(values)!=required or values['Id']!=self.spec.unit or values['LoadState']!='loaded'
                or values['Description']!='QwenSandbox '+self.owner_token):
            raise LinuxSandboxUnavailable('Unknown or mismatched service identity')
        if values['ControlGroup']==self.spec.control_group:
            self.cgroup_seen=True
        elif not (self.cgroup_seen and values['ControlGroup']=='' and values['MainPID']=='0'
                and values['SubState'] in ('exited','dead','failed')):
            raise LinuxSandboxUnavailable('Unverified cgroup identity')
        return {'main_done':values['SubState'] in ('exited','dead','failed'),
            'exit_code':int(values['ExecMainStatus']) if values['ExecMainCode']=='1' else None,
            'service_result':values['Result'],'cgroup':self.spec.control_group}

    def kill(self):
        self.observe()  # Never control a colliding/replaced unit owned by another operation.
        if self.empty(): return
        self._control(['kill','--kill-whom=all','--signal=SIGKILL'])
    def stop(self):
        self.observe()
        self._control(['stop'])

    def empty(self):
        if not self.cgroup_seen: return False
        directory=Path('/sys/fs/cgroup'+self.spec.control_group)
        if directory.is_symlink(): return False
        try:
            raw=(directory/'cgroup.events').read_text()
        except FileNotFoundError:
            # A removed, previously observed cgroup cannot retain kernel tasks.
            return not directory.exists()
        fields={}
        for line in raw.splitlines():
            key,value=line.split()
            if key in fields: return False
            fields[key]=value
        return fields.get('populated')=='0'


def run_linux_candidate(spec,*,host=None,cancel_event=None,output_limit=65536):
    """Developer/operator API, not a worker execution action or certification gate.

    Unit tests inject a modeled host. Actual kernel containment remains unverified
    until the independent Linux canary suite has run on a prepared test machine.
    """
    spec.validate()
    if type(output_limit) is not int or not 1<=output_limit<=65536: raise ValueError('Bounded output required')
    if cancel_event is not None and cancel_event.is_set():
        return {'reason':'cancelled','executed':False,'cleanup_confirmed':True,'accepted':False,'certification_eligible':False}
    host=host or SystemdHost(spec); process=None; threads=[]; buffers=[bytearray(),bytearray()]
    lock=threading.Lock(); overflow=threading.Event(); stored=0; seen=0
    reason='backend_unavailable'; cleanup=False; code=None; started=time.monotonic()
    def read(stream,index):
        nonlocal stored,seen
        try:
            while True:
                chunk=os.read(stream.fileno(),4096)
                if not chunk: break
                with lock:
                    seen+=len(chunk); part=chunk[:max(0,output_limit-stored)]
                    buffers[index].extend(part); stored+=len(part)
                    if seen>output_limit: overflow.set()
                if overflow.is_set(): break
        except (OSError,ValueError): overflow.set()
    try:
        process=host.start()
        for index,stream in enumerate((process.stdout,process.stderr)):
            thread=threading.Thread(target=read,args=(stream,index),daemon=True); thread.start(); threads.append(thread)
        deadline=started+spec.seconds
        while True:
            if cancel_event is not None and cancel_event.is_set(): reason='cancelled'; break
            if overflow.is_set(): reason='output_limit'; break
            if time.monotonic()>=deadline: reason='timeout'; break
            try:
                observation=host.observe()
            except LinuxSandboxUnavailable:
                # StartTransientUnit and the client are asynchronous. Do not
                # call unknown-unit state success or issue an unowned kill.
                if not getattr(host,'cgroup_seen',False) and process.poll() is None and time.monotonic()<min(deadline,started+5):
                    time.sleep(.02); continue
                raise
            if observation['main_done']:
                code=observation['exit_code']
                reason='completed' if code is not None and observation['service_result']=='success' else 'service_failure'
                if reason=='completed' and not host.empty(): reason='orphaned_descendant'
                break
            time.sleep(.02)
    except Exception:
        reason='backend_unavailable'
    finally:
        if process is not None:
            control_ok=True
            # Try both independent stop operations even when one fails.
            for operation in (host.kill,host.stop):
                try: operation()
                except Exception: control_ok=False
            try:
                # Client exit/status is never evidence that the service tree died.
                until=time.monotonic()+3
                while time.monotonic()<until:
                    if host.empty(): cleanup=True; break
                    time.sleep(.02)
                cleanup=cleanup and control_ok
                if not cleanup: reason='cleanup_unverified'
            except Exception: cleanup=False; reason='cleanup_unverified'
            try: process.wait(timeout=3)
            except Exception: process.kill(); process.wait(timeout=3); cleanup=False; reason='cleanup_unverified'
            for thread in threads: thread.join(timeout=.5)
            if any(t.is_alive() for t in threads): cleanup=False; reason='cleanup_unverified'
            for stream in (process.stdout,process.stderr): stream.close()
    if overflow.is_set() and reason=='completed': reason='output_limit'
    return {'reason':reason,'executed':process is not None,'exit_code':code,'cleanup_confirmed':cleanup,
        'unit':spec.unit,'source_sha256':spec.source_sha256,'image_sha256':spec.image_sha256,
        'stdout':buffers[0].decode('utf-8',errors='replace'),'stderr':buffers[1].decode('utf-8',errors='replace'),
        'captured_bytes':stored,'output_bytes_seen':seen,
        'backend_verified':False,'accepted':False,'certification_eligible':False}
