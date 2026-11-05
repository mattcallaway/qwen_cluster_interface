"""Cancellation propagation and fail-closed executor cleanup."""
import copy,json,threading,time
from pathlib import Path
from uuid import uuid4
import pytest
from app.database import db_transaction
from vps_agent import executor,bounded_process
from vps_agent.agent import VPSWorkerAgent,_run_lease_heartbeats
from vps_agent.buffer import OfflineBuffer
from vps_agent.config import AgentConfig
from script_permit_fixtures import reviewed_fixture_permit
from test_bounded_process import tree_script,assert_dead
from test_script_authorization import authorized_script


@pytest.mark.parametrize('status',[401,403,404,500,'unreachable'])
def test_lease_denial_or_network_loss_sets_cancellation(status):
    class Stop:
        def wait(self,seconds): return False
    class Agent:
        config=type('Config',(),{'heartbeat_interval':5,'controller_url':'http://127.0.0.1'})()
        def _headers(self): return {}
        def send_heartbeat(self): pytest.fail('Node heartbeat continued after execution cancellation')
    class Session:
        def post(self,*a,**k):
            if status=='unreachable': raise OSError('simulated controller disconnection')
            return type('Response',(),{'status_code':status})()
    agent=Agent(); agent.session=Session(); cancel=threading.Event()
    _run_lease_heartbeats(agent,'reviewed-fixture-lease',Stop(),cancel)
    assert cancel.is_set()


def reviewed_task(script):
    import hashlib
    return reviewed_fixture_permit({'task_id':'cleanup-fixture','task_type':'python_execution',
        'input_data':{'script':script},'execution_manifest':{'task_id':'cleanup-fixture',
            'script_checksum':hashlib.sha256(script.encode()).hexdigest(),'allowed_capabilities':['python_execution']}})


def test_cleanup_failure_cannot_return_success_or_artifacts(monkeypatch,tmp_path):
    monkeypatch.setenv('QC_WORKER_WORKSPACE_DIR',str(tmp_path))
    monkeypatch.setattr(executor,'_detect_os_isolation_mechanism',lambda:'restricted_subprocess')
    def failed_delete(*args,**kwargs): raise OSError('simulated workspace deletion failure')
    monkeypatch.setattr(executor.shutil,'rmtree',failed_delete)
    task,permit=reviewed_task('from pathlib import Path\nPath("result.txt").write_text("fixture")\n')
    result=executor.execute_task(task,script_permit=permit)
    assert result['status']=='error' and result['error_category']=='workspace_cleanup_failed'
    assert result['result_payload']['workspace_retained'] and result['result_payload']['artifacts']==[]
    assert len(list(tmp_path.iterdir()))==1


def test_unverified_process_cleanup_keeps_workspace_and_blocks_artifacts(monkeypatch,tmp_path):
    monkeypatch.setenv('QC_WORKER_WORKSPACE_DIR',str(tmp_path))
    monkeypatch.setattr(executor,'_detect_os_isolation_mechanism',lambda:'restricted_subprocess')
    def unverified(argv,**kwargs):
        return bounded_process.ProcessResult(argv,126,'','failure','cleanup_unverified',False,0,0,0)
    monkeypatch.setattr(executor,'run_bounded_process',unverified)
    task,permit=reviewed_task('print("not executed by this mock")')
    result=executor.execute_task(task,script_permit=permit)
    assert result['error_category']=='cleanup_unverified' and result['result_payload']['workspace_retained']
    assert result['result_payload']['artifacts']==[] and len(list(tmp_path.iterdir()))==1


def test_authenticated_hold_change_cancels_native_reviewed_job_and_descendants(authorized_script,monkeypatch,tmp_path):
    from app.services.orchestration import script_library
    f=authorized_script; script=tree_script(tmp_path)
    # Reviewed long-running fixture only. No generated code or live VPS is used.
    library=dict(script_library.LIBRARY['web_status_pilot'],script=script)
    monkeypatch.setitem(script_library.LIBRARY,'web_status_pilot',library)
    f['conn'].execute('UPDATE tasks SET input_data_json=? WHERE id=?',
        (json.dumps({'script':script}),f['task']['id'])); f['conn'].commit()
    class Adapter:
        verify=True
        revoked=False
        def post(self,url,**kwargs):
            if url.endswith('/heartbeat') and '/tasks/' in url:
                with db_transaction() as c:
                    c.execute('UPDATE infrastructure_nodes SET capability_restrictions_json=? WHERE id=?',
                        (json.dumps({'operational_hold':True}),f['worker']))
                self.revoked=True
            kwargs['follow_redirects']=kwargs.pop('allow_redirects',False)
            return f['client'].post(url,**kwargs)
    agent=VPSWorkerAgent(AgentConfig(controller_url='http://127.0.0.1',worker_id=f['worker'],
        worker_token=f['token'],data_dir=tmp_path,allow_script_execution=True,heartbeat_interval=5),
        OfflineBuffer(tmp_path/'buffer.db'))
    agent.is_registered=True; agent.session=Adapter()
    monkeypatch.setattr(executor,'_detect_os_isolation_mechanism',lambda:'restricted_subprocess')
    result=agent.poll_and_execute_task()
    assert agent.session.revoked and result['error_category']=='cancelled',result
    assert result['result_payload']['process_control']['cleanup_confirmed']
    assert result['result_payload']['artifacts']==[]
    assert_dead(json.loads((tmp_path/'root.json').read_text())+json.loads((tmp_path/'child.json').read_text()))
    row=f['conn'].execute('SELECT * FROM worker_executions WHERE task_id=?',(f['task']['id'],)).fetchone()
    assert row and row['status']=='error' and row['exit_code']!=0
