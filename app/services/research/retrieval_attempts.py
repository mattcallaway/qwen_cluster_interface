"""Durable, conservatively charged retrieval reservations and crash recovery.

Call transactional helpers only inside a short BEGIN IMMEDIATE transaction.
Reservations charge one request and the full time allowance before any I/O.
Failures/crashes never refund it. Successful controller observations may refund
unused time; worker-reported durations never authorize a refund.
"""
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4


def utc_now(): return datetime.now(timezone.utc)


class RetrievalBudgetError(ValueError): pass


def install_attempt_schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS research_retrieval_attempts (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, research_run_id TEXT NOT NULL,
        actor TEXT NOT NULL CHECK(actor IN ('worker','controller')), operation_id TEXT NOT NULL,
        worker_lease_id TEXT,
        attempt_number INTEGER NOT NULL, status TEXT NOT NULL,
        seconds_reserved REAL NOT NULL, started_at TEXT NOT NULL, deadline_at TEXT NOT NULL,
        finished_at TEXT, next_retry_at TEXT, error_code TEXT,
        UNIQUE(project_id,research_run_id,actor,operation_id,attempt_number))''')
    c.execute('''CREATE UNIQUE INDEX IF NOT EXISTS retrieval_one_active
        ON research_retrieval_attempts(project_id,research_run_id,actor,operation_id)
        WHERE status='ACTIVE' ''')
    c.execute('''CREATE TABLE IF NOT EXISTS research_retrieval_events (
        id INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL, event TEXT NOT NULL,
        recorded_at TEXT NOT NULL, detail_json TEXT NOT NULL)''')
    for operation in ('UPDATE','DELETE'):
        c.execute(f'''CREATE TRIGGER IF NOT EXISTS retrieval_event_no_{operation.lower()}
            BEFORE {operation} ON research_retrieval_events
            BEGIN SELECT RAISE(ABORT,'retrieval events are append-only'); END''')


def _event(c,attempt_id,event,detail):
    c.execute('INSERT INTO research_retrieval_events(attempt_id,event,recorded_at,detail_json) VALUES (?,?,?,?)',
        (attempt_id,event,utc_now().isoformat(),json.dumps(detail,sort_keys=True)))


def recover_expired(c):
    now=utc_now()
    rows=c.execute("SELECT id,attempt_number FROM research_retrieval_attempts WHERE status='ACTIVE' AND deadline_at<=?",
        (now.isoformat(),)).fetchall()
    for row in rows:
        retry=(now+timedelta(seconds=min(60,5*2**(row['attempt_number']-1)))).isoformat()
        c.execute("UPDATE research_retrieval_attempts SET status='ABANDONED',finished_at=?,next_retry_at=?,error_code='PROCESS_LOST' WHERE id=? AND status='ACTIVE'",
            (now.isoformat(),retry,row['id']))
        _event(c,row['id'],'ABANDONED',{'reason':'expired reservation; budget retained','next_retry_at':retry})
    return len(rows)


def reserve(c,project_id,research_run_id,actor,operation_id,seconds=8,worker_lease_id=None):
    if actor not in ('worker','controller') or not 0<float(seconds)<=8:
        raise RetrievalBudgetError('Invalid retrieval reservation')
    now=utc_now()
    previous=c.execute('''SELECT * FROM research_retrieval_attempts
        WHERE project_id=? AND research_run_id=? AND actor=? AND operation_id=?
        ORDER BY attempt_number DESC LIMIT 1''',(project_id,research_run_id,actor,operation_id)).fetchone()
    if previous:
        if previous['status']=='ACTIVE': raise RetrievalBudgetError('Retrieval is active; recovery required if expired')
        if previous['status']=='SUCCEEDED': raise RetrievalBudgetError('Successful operation cannot be repeated')
        if previous['attempt_number']>=3: raise RetrievalBudgetError('Retrieval attempt limit exhausted')
        if previous['next_retry_at'] and previous['next_retry_at']>now.isoformat():
            raise RetrievalBudgetError('Retrieval retry backoff is active')
    charged=c.execute('''UPDATE research_budgets SET docs_used=docs_used+1,
        browser_seconds_used=browser_seconds_used+? WHERE project_id=? AND docs_used<max_docs
        AND browser_seconds_used+?<=max_browser_seconds''',(seconds,project_id,seconds))
    if charged.rowcount!=1: raise RetrievalBudgetError('Persisted retrieval budget unavailable or exhausted')
    number=previous['attempt_number']+1 if previous else 1
    attempt={'id':'retrieval-'+uuid4().hex,'project_id':project_id,'research_run_id':research_run_id,
        'actor':actor,'operation_id':operation_id,'attempt_number':number,'status':'ACTIVE',
        'worker_lease_id':worker_lease_id,
        'seconds_reserved':float(seconds),'started_at':now.isoformat(),
        'deadline_at':(now+timedelta(seconds=float(seconds)+2)).isoformat()}
    # Two seconds allow protocol return/settlement after the separately enforced
    # eight-second network/extraction process deadline; they grant no extra I/O.
    c.execute('''INSERT INTO research_retrieval_attempts
        (id,project_id,research_run_id,actor,operation_id,worker_lease_id,attempt_number,status,seconds_reserved,started_at,deadline_at)
        VALUES (:id,:project_id,:research_run_id,:actor,:operation_id,:worker_lease_id,:attempt_number,:status,:seconds_reserved,:started_at,:deadline_at)''',attempt)
    _event(c,attempt['id'],'RESERVED',{'requests_charged':1,'seconds_charged':seconds,'attempt_number':number})
    return attempt


def settle(c,attempt_id,status,error_code=None,observed_seconds=None):
    if status not in ('SUCCEEDED','FAILED','TIMED_OUT'): raise ValueError('Invalid settlement')
    row=c.execute('SELECT * FROM research_retrieval_attempts WHERE id=?',(attempt_id,)).fetchone()
    if not row or row['status']!='ACTIVE': return False
    now=utc_now()
    if now.isoformat()>row['deadline_at']: status,error_code='TIMED_OUT','LATE_RESULT'
    retry=None if status=='SUCCEEDED' else (now+timedelta(seconds=min(60,5*2**(row['attempt_number']-1)))).isoformat()
    c.execute('''UPDATE research_retrieval_attempts SET status=?,finished_at=?,next_retry_at=?,error_code=?
        WHERE id=? AND status='ACTIVE' ''',(status,now.isoformat(),retry,error_code,attempt_id))
    if status=='SUCCEEDED' and row['actor']=='controller' and observed_seconds is not None:
        refund=max(0,row['seconds_reserved']-max(0,float(observed_seconds)))
        c.execute('UPDATE research_budgets SET browser_seconds_used=MAX(0,browser_seconds_used-?) WHERE project_id=?',
            (refund,row['project_id']))
    _event(c,attempt_id,status,{'error_code':error_code,'next_retry_at':retry,'budget_retained':status!='SUCCEEDED'})
    return status=='SUCCEEDED'
