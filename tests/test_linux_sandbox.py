import copy,io,json,os,subprocess,sys,threading
from dataclasses import replace
from pathlib import Path
from datetime import datetime,timedelta,timezone
import pytest
from vps_agent.linux_sandbox import LinuxSpec,SystemdHost,LinuxSandboxUnavailable,run_linux_candidate
from test_code_proposals import proposal


def spec(): return LinuxSpec('a'*32,'b'*64,'c'*64,(('src/main.py','d'*64),))


def test_fixed_service_policy_has_independent_deadline_tree_kill_and_no_test_or_host_mounts():
    command=spec().command(); joined=' '.join(command)
    expected=['RuntimeMaxSec=15','TimeoutStopSec=2','KillMode=control-group','SendSIGKILL=yes',
        'MemoryMax=256M','MemorySwapMax=0','TasksMax=32','CPUQuota=100%',
        'RootImageOptions=ro,nosuid,nodev','PrivateNetwork=yes','IPAddressDeny=any',
        'NoNewPrivileges=yes','ProtectControlGroups=yes','CapabilityBoundingSet=',
        'RestrictNamespaces=yes','NotifyAccess=none','Restart=no']
    for setting in expected: assert '--property='+setting in command
    assert command[-5:]==['--','/usr/bin/python3','-I','-B','/src/src/main.py']
    assert 'acceptance' not in joined and '/home/' not in joined and 'sudo' not in command
    assert '--scope' not in command and '--collect' not in command
    assert sum('BindReadOnlyPaths=' in item for item in command)==1


@pytest.mark.parametrize('change',[
    {'job_id':'../other.service'},{'job_id':'a'*32+'%n'}, {'source_sha256':'/etc/shadow'},
    {'image_sha256':'latest'},{'seconds':31},{'seconds':True},{'memory_mb':1024},
    {'processes':1000},{'source_files':(('src/../evil.py','d'*64),)},
    {'source_files':(('src/main.py','d'*64),('src/main.py','e'*64))},
    {'source_files':(('web/app.js','d'*64),)}])
def test_unsafe_policy_cannot_build_command(change):
    with pytest.raises((ValueError,TypeError)): replace(spec(),**change).command()


class FakeHost:
    """Models manager/cgroup responses, not Linux kernel enforcement."""
    def __init__(self,*,fail_kill=False,empty=True,observation=None):
        self.fail_kill=fail_kill; self.is_empty=empty; self.operations=[]
        self.observation=observation or {'main_done':True,'exit_code':0,'service_result':'success'}
    def start(self):
        self.operations.append('start')
        # Reviewed local process supplies pipe handles only; not a Linux service.
        self.process=subprocess.Popen([getattr(sys,'_base_executable',None) or sys.executable,'-I','-c','print("fixture output")'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        return self.process
    def observe(self):
        self.operations.append('observe'); self.process.wait(timeout=2)
        return self.observation
    def kill(self):
        self.operations.append('kill_all')
        if self.fail_kill: raise OSError('simulated cgroup control failure')
    def stop(self):
        self.operations.append('stop')
        if self.process.poll() is None: self.process.terminate()
    def empty(self): self.operations.append('empty'); return self.is_empty


def test_client_exit_is_not_cgroup_cleanup_or_software_acceptance():
    host=FakeHost(); result=run_linux_candidate(spec(),host=host)
    assert result['reason']=='completed' and result['cleanup_confirmed']
    assert host.operations.index('kill_all')<host.operations.index('stop')
    assert host.operations[-1]=='empty'
    assert result['accepted'] is False and result['backend_verified'] is False and not result['certification_eligible']


def test_failed_kernel_cleanup_does_not_turn_exit_zero_into_verified_success():
    host=FakeHost(fail_kill=True); result=run_linux_candidate(spec(),host=host)
    assert result['reason']=='cleanup_unverified' and not result['cleanup_confirmed'] and not result['accepted']
    assert 'stop' in host.operations


def test_unknown_manager_status_still_attempts_own_unit_cleanup():
    host=FakeHost()
    def invalid(): raise OSError('manager unavailable')
    host.observe=invalid
    result=run_linux_candidate(spec(),host=host)
    assert result['reason']=='backend_unavailable' and not result['accepted']
    assert 'kill_all' in host.operations


def test_unknown_cgroup_never_confirms_cleanup():
    host=SystemdHost(spec())
    assert host.empty() is False


def test_start_failure_never_claims_execution_or_kernel_verification():
    host=FakeHost()
    def held(): raise LinuxSandboxUnavailable('unprovisioned')
    host.start=held
    result=run_linux_candidate(spec(),host=host)
    assert not result['executed'] and result['reason']=='backend_unavailable' and not result['backend_verified']


def test_service_observation_rejects_forged_unit_identity(monkeypatch):
    host=SystemdHost(spec())
    raw='Id=other.service\nLoadState=loaded\nActiveState=active\nSubState=exited\nControlGroup=/system.slice/other.service\nMainPID=0\nExecMainCode=1\nExecMainStatus=0\nResult=success\n'
    monkeypatch.setattr(host,'_control',lambda args:raw)
    with pytest.raises(LinuxSandboxUnavailable): host.observe()
    assert not host.cgroup_seen


@pytest.mark.parametrize('previously_seen',[False,True])
def test_completed_cgroup_requires_prior_observed_identity(monkeypatch,previously_seen):
    host=SystemdHost(spec());host.cgroup_seen=previously_seen
    raw=f'Id={host.spec.unit}\nDescription=QwenSandbox {host.owner_token}\nLoadState=loaded\nActiveState=active\nSubState=exited\nControlGroup=\nMainPID=0\nExecMainCode=1\nExecMainStatus=0\nResult=success\n'
    monkeypatch.setattr(host,'_control',lambda args:raw)
    if previously_seen: assert host.observe()['main_done']
    else:
        with pytest.raises(LinuxSandboxUnavailable):host.observe()


def test_separate_approved_tests_and_image_are_bound_without_dispatch(proposal):
    f=proposal; review=f['reviewer'].review('app',f['artifact']['id'])
    plan=f['reviewer'].prepare_linux_plan(review['id'],'c'*64)
    assert plan['approved_tests']['artifact_id']==f['tests']['id']
    assert plan['proposal']['sha256']==f['artifact']['checksum_sha256']
    assert not plan['dispatchable'] and plan['image_approval_required'] and not plan['production_approved']
    assert not plan['accepted'] and not plan['certification_eligible']


def test_stale_proposal_cannot_prepare_linux_execution(proposal):
    f=proposal; review=f['reviewer'].review('app',f['artifact']['id'])
    f['conn'].execute('UPDATE artifacts SET current_version_number=2 WHERE id=?',(f['artifact']['id'],))
    with pytest.raises(ValueError): f['reviewer'].prepare_linux_plan(review['id'],'c'*64)


def test_real_backend_cannot_start_on_windows():
    if os.name!='nt': pytest.skip('Windows-only environment boundary check')
    result=run_linux_candidate(spec())
    assert not result['executed'] and result['reason']=='backend_unavailable'


def test_missing_cgroup_emptiness_never_becomes_cleanup_success():
    result=run_linux_candidate(spec(),host=FakeHost(empty=False))
    assert result['reason']=='cleanup_unverified' and not result['cleanup_confirmed']


def test_parent_deadline_does_not_depend_on_client_exit():
    host=FakeHost(observation={'main_done':False,'exit_code':None,'service_result':'success'})
    result=run_linux_candidate(replace(spec(),seconds=1),host=host)
    assert result['reason']=='timeout' and result['cleanup_confirmed']
    assert 'kill_all' in host.operations


def test_cancellation_before_start_cannot_launch():
    cancel=threading.Event(); cancel.set(); host=FakeHost()
    result=run_linux_candidate(spec(),host=host,cancel_event=cancel)
    assert result['reason']=='cancelled' and not result['executed'] and not host.operations


def test_application_output_is_not_verifier_authority():
    host=FakeHost()
    def start():
        host.process=subprocess.Popen([getattr(sys,'_base_executable',None) or sys.executable,'-I','-c',
            'print(\'{"accepted":true,"backend_verified":true}\')'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        return host.process
    host.start=start
    result=run_linux_candidate(spec(),host=host)
    assert not result['accepted'] and not result['backend_verified']
    assert '"accepted":true' in result['stdout']


@pytest.mark.parametrize('operation',['kill','stop'])
def test_unit_collision_or_replacement_cannot_control_another_job(monkeypatch,operation):
    host=SystemdHost(spec()); calls=[]
    raw=f'Id={host.spec.unit}\nDescription=QwenSandbox different-owner\nLoadState=loaded\nActiveState=active\nSubState=running\nControlGroup={host.spec.control_group}\nMainPID=123\nExecMainCode=0\nExecMainStatus=0\nResult=success\n'
    def control(arguments): calls.append(arguments); return raw
    monkeypatch.setattr(host,'_control',control)
    with pytest.raises(LinuxSandboxUnavailable): getattr(host,operation)()
    assert len(calls)==1 and calls[0][0]=='show'


def test_read_only_probe_cannot_mark_windows_ready():
    import runpy
    module=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/probe_linux_sandbox.py'))
    result=module['probe']()
    assert result['verdict']=='NO_GO' and not result['backend_verified'] and result['mutations']==[]


@pytest.mark.parametrize('change',['valid','production','wrong_host','unapproved_image','expired','too_long'])
def test_explicit_host_and_image_approval_is_required(monkeypatch,change):
    from vps_agent.linux_sandbox import sha
    host=SystemdHost(spec()); machine=b'synthetic-test-host'
    policy={'schema':'qc.linux_test_host.v1','environment':'non-production',
        'machine_id_sha256':sha(machine),'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
        'allowed_image_sha256':['c'*64]}
    if change=='production': policy['environment']='production'
    if change=='wrong_host': policy['machine_id_sha256']='f'*64
    if change=='unapproved_image': policy['allowed_image_sha256']=['f'*64]
    if change=='expired': policy['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    if change=='too_long': policy['expires_at']=(datetime.now(timezone.utc)+timedelta(days=8)).isoformat()
    # Policy-parser tests only; these mocks do not prove filesystem ownership.
    monkeypatch.setattr(host,'_protected_regular',lambda path:None)
    original_open=Path.open; original_read=Path.read_bytes
    def opened(path,*args,**kwargs):
        if path.as_posix()=='/etc/qwen-sandbox/test-host.json': return io.BytesIO(json.dumps(policy).encode())
        return original_open(path,*args,**kwargs)
    def read(path): return machine if path.as_posix()=='/etc/machine-id' else original_read(path)
    monkeypatch.setattr(Path,'open',opened); monkeypatch.setattr(Path,'read_bytes',read)
    if change=='valid': host._test_host_authorization()
    else:
        with pytest.raises(LinuxSandboxUnavailable): host._test_host_authorization()
