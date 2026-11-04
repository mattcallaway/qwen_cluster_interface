"""Reviewed local process fixtures; no generated code or live fleet execution."""
import os,sys,time,threading,subprocess,json
from pathlib import Path
import pytest
from vps_agent import bounded_process as control
BASE_PYTHON=getattr(sys,'_base_executable',None) or sys.executable


def run(script,tmp_path,**kwargs):
    env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP'}}
    return control.run_bounded_process([BASE_PYTHON,'-I','-c',script],cwd=str(tmp_path),env=env,
                                      timeout_seconds=kwargs.pop('seconds',3),**kwargs)


def test_actual_kernel_job_runs_reviewed_fixture(tmp_path):
    result=run('print("reviewed fixture")',tmp_path)
    assert result.reason=='completed',result
    assert result.returncode==0 and result.stdout.splitlines()==['reviewed fixture']
    assert result.cleanup_confirmed and result.boundary=='windows_job'


def test_hard_deadline_stops_actual_process(tmp_path):
    result=run('import time;time.sleep(60)',tmp_path,seconds=.8)
    assert result.reason=='timeout' and result.returncode==124,result
    assert result.cleanup_confirmed and result.elapsed_seconds<4


def assert_dead(pids):
    import ctypes
    from ctypes import wintypes as W
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]; k.OpenProcess.restype=W.HANDLE
    k.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD]; k.WaitForSingleObject.restype=W.DWORD
    k.CloseHandle.argtypes=[W.HANDLE]; k.CloseHandle.restype=W.BOOL
    for pid in pids:
        handle=k.OpenProcess(0x100000,False,pid)
        if not handle:
            assert ctypes.get_last_error()==87,('PID state could not be verified',pid)
            continue
        try: assert k.WaitForSingleObject(handle,2000)==0,('Process still running',pid)
        finally: k.CloseHandle(handle)


def tree_script(tmp_path,orphan=False):
    leaf='import time;time.sleep(60)'
    child=('import subprocess,sys,json,time,os;'
        'p=subprocess.Popen([sys.executable,"-I","-c",'+repr(leaf)+'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);'
        'open('+repr(str(tmp_path/'child.json'))+',"w").write(json.dumps([os.getpid(),p.pid]));time.sleep(60)')
    return ('import subprocess,sys,json,time,os;'
        'p=subprocess.Popen([sys.executable,"-I","-c",'+repr(child)+'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP);'
        'open('+repr(str(tmp_path/'root.json'))+',"w").write(json.dumps([os.getpid(),p.pid]));'
        + ('time.sleep(.5)' if orphan else 'time.sleep(60)'))


def test_deadline_kills_children_and_grandchildren_even_in_new_process_group(tmp_path):
    result=run(tree_script(tmp_path),tmp_path,seconds=2)
    assert result.reason=='timeout' and result.cleanup_confirmed,result
    assert_dead(json.loads((tmp_path/'root.json').read_text())+json.loads((tmp_path/'child.json').read_text()))


def test_parent_exit_zero_with_live_descendants_is_not_success(tmp_path):
    result=run(tree_script(tmp_path,orphan=True),tmp_path)
    assert result.reason=='orphaned_descendant' and result.returncode!=0 and result.cleanup_confirmed,result
    assert_dead(json.loads((tmp_path/'root.json').read_text())+json.loads((tmp_path/'child.json').read_text()))


@pytest.mark.parametrize('stream',[1,2])
def test_runaway_output_is_killed_before_capture_grows_unbounded(tmp_path,stream):
    script=f'import os\nwhile True: os.write({stream},b"x"*4096)'
    result=run(script,tmp_path,output_limit=16384)
    assert result.reason=='output_limit' and result.cleanup_confirmed,result
    assert result.captured_bytes==16384 and result.output_bytes_seen>16384
    assert len(result.stdout.encode())+len(result.stderr.encode())<=16384
    assert result.elapsed_seconds<3


def test_exact_combined_output_budget_is_accepted(tmp_path):
    result=run('import os;os.write(1,b"a"*8192);os.write(2,b"b"*8192)',tmp_path,output_limit=16384)
    assert result.reason=='completed' and result.returncode==0 and result.cleanup_confirmed,result
    assert result.captured_bytes==result.output_bytes_seen==16384


def test_cancel_signal_kills_entire_tree(tmp_path):
    cancel=threading.Event()
    def revoke():
        until=time.monotonic()+3
        while not (tmp_path/'child.json').exists() and time.monotonic()<until: time.sleep(.01)
        cancel.set()
    watcher=threading.Thread(target=revoke); watcher.start()
    result=run(tree_script(tmp_path),tmp_path,seconds=5,cancel_event=cancel)
    watcher.join(timeout=1)
    assert result.reason=='cancelled' and result.cleanup_confirmed,result
    assert_dead(json.loads((tmp_path/'root.json').read_text())+json.loads((tmp_path/'child.json').read_text()))


def test_parent_process_crash_closes_last_handle_and_kills_job(tmp_path):
    root=Path(__file__).resolve().parents[1]
    script='''import os,sys,threading,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from vps_agent.bounded_process import run_bounded_process
def crash():
    until=time.monotonic()+5
    while not Path(sys.argv[3]).exists() and time.monotonic()<until: time.sleep(.01)
    os._exit(23)
threading.Thread(target=crash,daemon=True).start()
run_bounded_process([sys.executable,'-I','-c',sys.argv[2]],cwd=str(Path(sys.argv[3]).parent),
    env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP'}},timeout_seconds=15)
'''
    driver=subprocess.run([BASE_PYTHON,'-I','-c',script,str(root),tree_script(tmp_path),str(tmp_path/'child.json')],
        capture_output=True,timeout=8)
    assert driver.returncode==23,driver.stderr
    assert_dead(json.loads((tmp_path/'root.json').read_text())+json.loads((tmp_path/'child.json').read_text()))


def test_supervisor_watchdog_kills_tree_while_parent_loop_is_stalled(tmp_path):
    root=Path(__file__).resolve().parents[1]
    script='''import os,sys,time,json
sys.path.insert(0,sys.argv[1])
from vps_agent import bounded_process as c
real_sleep=time.sleep
c.time.sleep=lambda seconds:real_sleep(5)
result=c.run_bounded_process([sys.executable,'-I','-c',sys.argv[2]],cwd=sys.argv[3],
    env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP'}},timeout_seconds=2)
print(json.dumps({'reason':result.reason,'cleanup':result.cleanup_confirmed}))
'''
    driver=subprocess.Popen([BASE_PYTHON,'-I','-c',script,str(root),tree_script(tmp_path),str(tmp_path)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        until=time.monotonic()+3
        while not (tmp_path/'child.json').exists() and time.monotonic()<until: time.sleep(.01)
        assert (tmp_path/'child.json').exists()
        assert_dead(json.loads((tmp_path/'root.json').read_text())+json.loads((tmp_path/'child.json').read_text()))
        assert driver.poll() is None,'Parent resumed before independent cleanup was observed'
        out,err=driver.communicate(timeout=7)
        assert driver.returncode==0,err
        assert json.loads(out)=={'reason':'timeout','cleanup':True}
    finally:
        if driver.poll() is None: driver.kill(); driver.communicate(timeout=3)


def test_unavailable_kernel_boundary_has_no_subprocess_fallback(tmp_path,monkeypatch):
    from vps_agent.windows_job import BoundaryUnavailable
    def unavailable(): raise BoundaryUnavailable('simulated unsupported backend')
    monkeypatch.setattr(control,'WindowsJob',unavailable)
    monkeypatch.setattr(control.subprocess,'Popen',lambda *a,**k:pytest.fail('Fallback process started'))
    with pytest.raises(BoundaryUnavailable): run('print("must not run")',tmp_path)


def test_termination_accounting_failure_is_never_success(tmp_path,monkeypatch):
    from vps_agent.windows_job import BoundaryUnavailable
    def failed(self): raise BoundaryUnavailable('simulated termination failure')
    monkeypatch.setattr(control.WindowsJob,'terminate',failed)
    result=run('print("exit zero cannot certify cleanup")',tmp_path)
    assert result.reason=='cleanup_unverified' and result.returncode!=0 and not result.cleanup_confirmed


@pytest.mark.parametrize('invalid_budget',[{'seconds':0},{'seconds':float('inf')},{'seconds':31},{'output_limit':65537},{'output_limit':0}])
def test_invalid_budgets_cannot_start(tmp_path,invalid_budget):
    with pytest.raises(ValueError): run('print("invalid request")',tmp_path,**invalid_budget)
