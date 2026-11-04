import concurrent.futures
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import pytest
from app.services.research import native_retrieval
from app.services.research import retrieval_attempts as attempts
from app.services import worker
from vps_agent import research_job
from test_native_retrieval import native_fetch

ROOT=Path(__file__).resolve().parents[1]


def location(ledger):
    return next(r[2] for r in ledger.conn.execute('PRAGMA database_list') if r[1]=='main')


def connection(ledger):
    c=sqlite3.connect(location(ledger),timeout=1); c.row_factory=sqlite3.Row
    return c


@pytest.mark.parametrize('phase',['stall_fetch','slow_stream','redirect_stall','stall_extract'])
def test_hard_deadline_kills_stalled_phase(monkeypatch,phase):
    monkeypatch.setattr(research_job,'_process_args',lambda:[sys.executable,'-I',
        str(ROOT/'tests/retrieval_process_driver.py'),str(ROOT),phase,'The source statement.'])
    real_popen=subprocess.Popen; children=[]
    def track(*args,**kwargs):
        process=real_popen(*args,**kwargs); children.append(process); return process
    monkeypatch.setattr(research_job.subprocess,'Popen',track)
    started=time.monotonic()
    with pytest.raises(research_job.RetrievalDeadlineExceeded):
        research_job.run_bounded_retrieval('https://terms.example/',1.2)
    assert time.monotonic()-started<4
    assert children and all(child.poll() is not None for child in children)


def test_fast_process_returns_verified_bytes(monkeypatch):
    monkeypatch.setattr(research_job,'_process_args',lambda:[sys.executable,'-I',
        str(ROOT/'tests/retrieval_process_driver.py'),str(ROOT),'good','Exact source statement.'])
    result=research_job.run_bounded_retrieval('https://terms.example/',8)
    assert result['extracted_text']=='Exact source statement.'


def test_failure_charge_survives_caller_rollback(native_fetch):
    f=native_fetch; eid=f['complete']()
    f['state']['body']=b'A conflicting response.'
    with pytest.raises(ValueError,match='disagree'): f['verifier'].observe(f['ledger'],eid)
    f['ledger'].conn.rollback()
    with connection(f['ledger']) as c:
        row=c.execute("SELECT status FROM research_retrieval_attempts WHERE actor='controller' AND operation_id=?",(eid,)).fetchone()
        budget=c.execute('SELECT docs_used,browser_seconds_used FROM research_budgets WHERE project_id=?',
            (f['ledger'].project_id,)).fetchone()
    assert row['status']=='FAILED' and budget['docs_used']==2
    assert budget['browser_seconds_used']==16  # Worker and controller reservations, no failure refund.
    assert not f['ledger'].items('retrieval_receipt')


def test_failed_observation_backoff_prevents_immediate_refetch(native_fetch):
    f=native_fetch; eid=f['complete'](); f['state']['body']=b'Different bytes.'
    with pytest.raises(ValueError): f['verifier'].observe(f['ledger'],eid)
    calls=f['state']['calls']
    with pytest.raises(attempts.RetrievalBudgetError,match='backoff'):
        f['verifier'].observe(f['ledger'],eid)
    assert f['state']['calls']==calls


def test_budget_and_reservation_commit_before_network_without_writer_lock(native_fetch,monkeypatch):
    f=native_fetch; eid=f['complete'](); original=native_retrieval.run_bounded_retrieval
    def another_writer(url,seconds):
        with connection(f['ledger']) as c:
            c.execute('BEGIN IMMEDIATE')
            assert c.execute('SELECT docs_used FROM research_budgets WHERE project_id=?',
                (f['ledger'].project_id,)).fetchone()[0]==2
            assert c.execute("SELECT count(*) FROM research_retrieval_attempts WHERE actor='controller' AND status='ACTIVE'").fetchone()[0]>=1
            c.execute("UPDATE infrastructure_nodes SET capability_restrictions_json='{}' WHERE id=?",(f['observer'],))
        return original(url,seconds)
    monkeypatch.setattr(native_retrieval,'run_bounded_retrieval',another_writer)
    with pytest.raises(ValueError,match='hold'): f['verifier'].observe(f['ledger'],eid)
    assert not f['ledger'].items('retrieval_receipt')


def test_crash_reservation_recovers_after_restart_without_refund(native_fetch,monkeypatch):
    ledger=native_fetch['ledger']; native_fetch['complete']()
    script='''import sys,sqlite3,os
sys.path.insert(0,sys.argv[1])
import dotenv
dotenv.load_dotenv=lambda *a,**k:False
from app.services.research.retrieval_attempts import reserve
c=sqlite3.connect(sys.argv[2]); c.row_factory=sqlite3.Row
c.execute('BEGIN IMMEDIATE')
reserve(c,sys.argv[3],sys.argv[4],'controller','crash-operation')
c.commit()
os._exit(17)
'''
    result=subprocess.run([sys.executable,'-I','-c',script,str(ROOT),location(ledger),ledger.project_id,ledger.run_id],
        capture_output=True,timeout=10)
    assert result.returncode==17,result.stderr
    now=attempts.utc_now()+timedelta(seconds=20)
    monkeypatch.setattr(attempts,'utc_now',lambda:now)
    assert native_fetch['verifier'].recover(ledger)==1
    assert native_fetch['verifier'].recover(ledger)==0
    with connection(ledger) as c:
        row=c.execute("SELECT * FROM research_retrieval_attempts WHERE operation_id='crash-operation'").fetchone()
        assert row['status']=='ABANDONED'
        assert c.execute('SELECT docs_used FROM research_budgets WHERE project_id=?',(ledger.project_id,)).fetchone()[0]==2
        with pytest.raises(attempts.RetrievalBudgetError,match='backoff'):
            attempts.reserve(c,ledger.project_id,ledger.run_id,'controller','crash-operation')


def test_expired_worker_reservation_recovered_by_existing_reconciler(native_fetch,monkeypatch):
    f=native_fetch
    response=f['client'].post('/api/v1/worker/tasks/lease',json={'worker_id':f['worker'],
        'supported_categories':['research_fetch']},headers=f['auth'])
    assert response.status_code==200 and response.json()['status']=='leased'
    now=attempts.utc_now()+timedelta(seconds=20)
    monkeypatch.setattr(attempts,'utc_now',lambda:now)
    worker.reconcile_expired_leases()
    with connection(f['ledger']) as c:
        row=c.execute('SELECT status FROM research_retrieval_attempts WHERE operation_id=?',(f['task']['id'],)).fetchone()
        assert row['status']=='ABANDONED'
        assert c.execute('SELECT docs_used FROM research_budgets WHERE project_id=?',(f['ledger'].project_id,)).fetchone()[0]==1
    assert f['state']['calls']==0


@pytest.mark.parametrize('same_operation',[True,False])
def test_concurrent_reservations_cannot_overspend(native_fetch,same_operation):
    ledger=native_fetch['ledger']; native_fetch['complete']()
    database_path=location(ledger)
    with connection(ledger) as c:
        c.execute('UPDATE research_budgets SET max_docs=2 WHERE project_id=?',(ledger.project_id,))
    def reserve_one(index):
        c=sqlite3.connect(database_path,timeout=1); c.row_factory=sqlite3.Row
        with c:
            c.execute('BEGIN IMMEDIATE')
            try:
                attempts.reserve(c,ledger.project_id,ledger.run_id,'controller',
                    'same' if same_operation else 'operation-'+str(index))
            except attempts.RetrievalBudgetError: return False
            return True
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(reserve_one,[1,2]))
    assert sum(outcomes)==1
    with connection(ledger) as c:
        assert c.execute('SELECT docs_used FROM research_budgets WHERE project_id=?',(ledger.project_id,)).fetchone()[0]==2


def test_retry_limit_and_time_allowance_are_durable(native_fetch,monkeypatch):
    ledger=native_fetch['ledger']; native_fetch['complete']()
    now=attempts.utc_now()
    monkeypatch.setattr(attempts,'utc_now',lambda:now)
    for _ in range(3):
        with connection(ledger) as c:
            c.execute('BEGIN IMMEDIATE')
            reservation=attempts.reserve(c,ledger.project_id,ledger.run_id,'controller','retry-operation')
            attempts.settle(c,reservation['id'],'FAILED','TEST_FAILURE')
        now+=timedelta(seconds=70)
    with connection(ledger) as c:
        with pytest.raises(attempts.RetrievalBudgetError,match='attempt limit'):
            attempts.reserve(c,ledger.project_id,ledger.run_id,'controller','retry-operation')
        assert c.execute('SELECT docs_used,browser_seconds_used FROM research_budgets WHERE project_id=?',
            (ledger.project_id,)).fetchone()[:]==(4,32)


def test_controller_timeout_is_durably_recorded(native_fetch,monkeypatch):
    f=native_fetch; eid=f['complete']()
    def timeout(*args): raise research_job.RetrievalDeadlineExceeded('stalled')
    monkeypatch.setattr(native_retrieval,'run_bounded_retrieval',timeout)
    with pytest.raises(TimeoutError): f['verifier'].observe(f['ledger'],eid)
    with connection(f['ledger']) as c:
        assert c.execute("SELECT status FROM research_retrieval_attempts WHERE actor='controller' AND operation_id=?",(eid,)).fetchone()[0]=='TIMED_OUT'


def test_attempt_events_are_append_only(native_fetch):
    native_fetch['complete']()
    with connection(native_fetch['ledger']) as c:
        with pytest.raises(sqlite3.IntegrityError,match='append-only'):
            c.execute('DELETE FROM research_retrieval_events')


def test_receipt_persistence_failure_keeps_charge_and_no_success_event(native_fetch,monkeypatch):
    f=native_fetch; eid=f['complete']()
    def failed_receipt(*args): raise RuntimeError('simulated persistence failure')
    monkeypatch.setattr(f['verifier'],'_make_receipt',failed_receipt)
    with pytest.raises(RuntimeError,match='persistence failure'):
        f['verifier'].observe(f['ledger'],eid)
    f['ledger'].conn.rollback()
    with connection(f['ledger']) as c:
        row=c.execute("SELECT id,status FROM research_retrieval_attempts WHERE actor='controller' AND operation_id=?",(eid,)).fetchone()
        events=[r[0] for r in c.execute('SELECT event FROM research_retrieval_events WHERE attempt_id=? ORDER BY id',(row['id'],))]
        assert row['status']=='FAILED' and events==['RESERVED','FAILED']
        assert c.execute('SELECT browser_seconds_used FROM research_budgets WHERE project_id=?',
            (f['ledger'].project_id,)).fetchone()[0]==16
    assert not f['ledger'].items('retrieval_receipt')


def test_child_watchdog_survives_parent_process_crash(tmp_path):
    marker=tmp_path/'child-watchdog-exit.txt'
    script='''import json,subprocess,sys,time,os
child=subprocess.Popen([sys.executable,'-I',sys.argv[1],sys.argv[2],'stall_fetch','Source',sys.argv[3]],
    stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
child.stdin.write(json.dumps({'url':'https://terms.example/','seconds':2,
    'deadline_monotonic':time.monotonic()+2}).encode())
child.stdin.close()
os._exit(19)
'''
    crashed=subprocess.run([sys.executable,'-I','-c',script,str(ROOT/'tests/retrieval_process_driver.py'),
        str(ROOT),str(marker)],capture_output=True,timeout=10)
    assert crashed.returncode==19
    deadline=time.monotonic()+5
    while not marker.exists() and time.monotonic()<deadline: time.sleep(.05)
    assert marker.exists() and marker.read_text()=='124'
