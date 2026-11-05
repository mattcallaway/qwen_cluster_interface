"""Controller-observed retrieval attestations for completed native fetch tasks.

An authenticated worker result is not proof of HTTP retrieval. The controller
independently retrieves the queued URL, compares exact raw/extracted bytes and
signs a receipt. Key provisioning/process isolation are deployment prerequisites.
No key is loaded or generated here; no startup hook enables this service.
"""
import base64
import hashlib
import hmac
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse
from app.services.research.feedback import _dump, _hash
from app.services.research.retrieval import SecureFetcher
from app.services.research.extraction import DocumentExtractor, detect_challenge_page
from app.services.research.policy import ResearchSecurityPolicy
from app.services.worker import check_worker_leasing_authority
from app.services.research.retrieval_attempts import reserve,settle,recover_expired
from vps_agent.research_job import run_bounded_retrieval


class _HttpsRetrievalPolicy(ResearchSecurityPolicy):
    def validate_url(self,url):
        if urlparse(url).scheme!='https':
            return False,'Native attestation requires HTTPS at every redirect',None
        return super().validate_url(url)


class NativeRetrievalVerifier:
    def __init__(self, signing_key, observer_node_id):
        if not isinstance(signing_key,bytes) or len(signing_key)<32:
            raise ValueError('A separately provisioned controller key is required')
        self._key=signing_key
        self.observer_node_id=observer_node_id

    def _context(self,ledger,execution_id):
        conn=ledger.conn
        cursor=conn.execute('''SELECT e.*,o.id AS orchestration_task_id,o.action_json,o.plan_key,
                o.revision_id,o.attempts AS producer_attempt,l.attempt_count AS lease_attempt,
                l.leased_at,l.lease_expires_at,
                rev.plan_json AS approved_plan_json,
                o.run_id AS orchestration_run_id,t.input_data_json,t.task_type
            FROM worker_executions e
            JOIN worker_task_leases l ON l.id=e.lease_id AND l.task_id=e.task_id
                AND l.worker_id=e.worker_id AND l.idempotency_key=e.idempotency_key
            JOIN tasks t ON t.id=e.task_id
            JOIN orchestration_tasks o ON o.current_task_id=t.id AND o.dispatched_node=e.worker_id
            JOIN orchestration_runs r ON r.id=o.run_id AND r.project_id=t.project_id
            JOIN orchestration_plan_revisions rev ON rev.id=o.revision_id AND rev.id=r.current_revision_id
            JOIN research_runs rr ON rr.orchestration_run_id=r.id AND rr.project_id=r.project_id
            JOIN projects p ON p.id=r.project_id AND p.selected_path_id=t.path_id
            JOIN project_paths pp ON pp.id=t.path_id AND pp.project_id=p.id
            JOIN path_stages stage ON stage.id=t.stage_id AND stage.path_id=pp.id
            WHERE e.id=? AND rr.id=? AND r.project_id=?
                AND e.status='success' AND e.exit_code=0
                AND l.status='completed' AND t.status='completed'
                AND o.state IN ('dispatched','accepted') AND r.state IN ('RUNNING','EVALUATING')
                AND rr.status='active' AND rev.status='approved' AND p.status='active'
                AND pp.status='active' AND stage.status IN ('planned','active') ''',
            (execution_id,ledger.run_id,ledger.project_id))
        rows=cursor.fetchall()
        if len(rows)!=1: raise ValueError('No unique successful native retrieval chain')
        native=dict(zip([c[0] for c in cursor.description],rows[0]))
        times=[datetime.fromisoformat(native[k].replace('Z','+00:00'))
            for k in ('leased_at','completed_at','lease_expires_at')]
        if any(t.tzinfo is None for t in times) or not times[0]<=times[1]<=times[2]:
            raise ValueError('Execution did not complete within its native lease')
        if native['task_type']!='research_fetch': raise ValueError('Not a native research-fetch task')
        authority=check_worker_leasing_authority(conn,native['worker_id'],['research_fetch'])
        if authority['restrictions'].get('allow_web_fetch') is not True:
            raise ValueError('Worker web retrieval is not explicitly authorized')
        observer=conn.execute('''SELECT role,is_enabled,capability_restrictions_json
            FROM infrastructure_nodes WHERE id=?''',(self.observer_node_id,)).fetchone()
        if not observer or observer[0]!='controller' or not observer[1]:
            raise ValueError('Controller observer is not enabled')
        policy=json.loads(observer[2])
        if policy.get('allow_web_fetch') is not True or policy.get('operational_hold',False):
            raise ValueError('Controller observation is on hold')
        action=json.loads(native['action_json']); inputs=json.loads(native['input_data_json'])
        approved=json.loads(native['approved_plan_json'])
        planned=[t for t in approved.get('tasks',[]) if t.get('key')==native['plan_key']]
        if len(planned)!=1 or planned[0].get('action')!=action:
            raise ValueError('Retrieval action is not bound to the current approved plan')
        path_id=inputs.get('feedback_path_id')
        if (action.get('type')!='research_fetch' or action.get('url')!=inputs.get('url')
                or action.get('feedback_path_id')!=path_id):
            raise ValueError('Queued action and worker task disagree')
        path=next((p for p in ledger.items('path') if p['id']==path_id),None)
        if not path or path.get('status')!='PROPOSED': raise ValueError('Missing scoped feedback proposal')
        output=json.loads(native['result_payload_json'])
        if output.get('schema')!='qc.retrieval_result.v1' or output.get('requested_url')!=inputs['url']:
            raise ValueError('Worker output is not bound to the queued URL')
        worker_attempt=conn.execute("""SELECT id FROM research_retrieval_attempts WHERE actor='worker'
            AND project_id=? AND research_run_id=? AND operation_id=? AND worker_lease_id=? AND status='SUCCEEDED' """,
            (ledger.project_id,ledger.run_id,native['task_id'],native['lease_id'])).fetchone()
        if not worker_attempt: raise ValueError('Worker retrieval reservation did not succeed')
        return native,inputs,output

    @contextmanager
    def _database(self,ledger,write=False):
        location=next(row[2] for row in ledger.conn.execute('PRAGMA database_list') if row[1]=='main')
        if not location: raise ValueError('Durable retrieval requires a file-backed database')
        conn=sqlite3.connect(Path(location).as_uri()+'?mode=rw',uri=True,timeout=1)
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            if write: conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally: conn.close()

    def recover(self,ledger):
        if ledger.conn.in_transaction: raise ValueError('Recovery requires an idle caller connection')
        with self._database(ledger,write=True) as conn:
            return recover_expired(conn)

    def observe(self,ledger,execution_id):
        from app.services.research.feedback import ProjectEvidenceFeedback
        native,inputs,output=self._context(ledger,execution_id)
        existing=next((r for r in ledger.items('retrieval_receipt')
            if r['body']['execution_id']==execution_id),None)
        if existing:
            self.verify(ledger,existing)
            return existing
        if ledger.conn.in_transaction:
            raise ValueError('Network observation requires an idle caller connection')
        self.recover(ledger)
        with self._database(ledger,write=True) as conn:
            fresh=ProjectEvidenceFeedback(conn,ledger.project_id,ledger.run_id,receipt_verifier=self)
            self._context(fresh,execution_id)
            remaining=conn.execute('SELECT max_browser_seconds-browser_seconds_used FROM research_budgets WHERE project_id=?',
                (ledger.project_id,)).fetchone()
            if not remaining or remaining[0]<=0: raise ValueError('Persisted retrieval budget exhausted')
            attempt=reserve(conn,ledger.project_id,ledger.run_id,'controller',execution_id,min(8,remaining[0]))
        started=time.monotonic()
        try:
            # Both network and extraction run in a killable process. No database
            # write transaction spans this call, and no key enters that child.
            data=run_bounded_retrieval(inputs['url'],attempt['seconds_reserved'])
            raw=base64.b64decode(data['raw_base64'],validate=True)
            if (len(raw)>2_000_000 or hashlib.sha256(raw).hexdigest()!=data['raw_sha256']
                    or _hash(data['extracted_text'])!=data['extracted_sha256']):
                raise ValueError('Controller retrieval bytes failed integrity checks')
            if (output.get('raw_sha256')!=data['raw_sha256']
                    or output.get('extracted_sha256')!=data['extracted_sha256']
                    or output.get('final_url')!=data['final_url']):
                raise ValueError('Worker and independent controller observations disagree')
            with self._database(ledger,write=True) as conn:
                fresh=ProjectEvidenceFeedback(conn,ledger.project_id,ledger.run_id,receipt_verifier=self)
                after,after_inputs,after_output=self._context(fresh,execution_id)
                if (after!=native or after_inputs!=inputs or after_output!=output):
                    raise ValueError('Native retrieval changed during verification')
                if not settle(conn,attempt['id'],'SUCCEEDED',observed_seconds=time.monotonic()-started):
                    raise TimeoutError('Observation result was late or abandoned')
                receipt=self._make_receipt(fresh,native,inputs,execution_id,data,attempt['id'])
                self.verify(fresh,receipt)
                fresh._append('retrieval_receipt',receipt['id'],receipt)
                return receipt
        except BaseException as exc:
            # This separate commit survives caller rollback and process errors.
            with self._database(ledger,write=True) as conn:
                settle(conn,attempt['id'],'TIMED_OUT' if isinstance(exc,TimeoutError) else 'FAILED',
                    error_code=type(exc).__name__)
            raise

    def _make_receipt(self,ledger,native,inputs,execution_id,data,attempt_id):
        body={'schema':'qc.controller_retrieval_receipt.v2','project_id':ledger.project_id,
            'research_run_id':ledger.run_id,'orchestration_run_id':native['orchestration_run_id'],
            'orchestration_task_id':native['orchestration_task_id'],'task_id':native['task_id'],
            'lease_id':native['lease_id'],'execution_id':execution_id,'worker_id':native['worker_id'],
            'observer_node_id':self.observer_node_id,'idempotency_key':native['idempotency_key'],
            'action_sha256':_hash(native['action_json']),'worker_output_sha256':_hash(native['result_payload_json']),
            'revision_id':native['revision_id'],'approved_plan_sha256':_hash(native['approved_plan_json']),
            'producer_attempt':native['producer_attempt'],'lease_attempt':native['lease_attempt'],
            'feedback_path_id':inputs['feedback_path_id'],'requested_url':inputs['url'],
            'final_url':data['final_url'],'redirect_chain':data['redirect_chain'],
            'status_code':data['status_code'],'content_type':data['content_type'],
            'observed_at':datetime.now(timezone.utc).isoformat(),'raw_sha256':data['raw_sha256'],
            'raw_base64':data['raw_base64'],
            'extracted_sha256':data['extracted_sha256'],'extracted_text':data['extracted_text'],
            'observation_attempt_id':attempt_id,'verifier_version':'controller-observation.v2','certification_eligible':False}
        receipt={'id':_hash(_dump(body)),'body':body,
            'signature':hmac.new(self._key,_dump(body).encode(),hashlib.sha256).hexdigest()}
        return receipt

    def verify(self,ledger,receipt):
        body=receipt['body']
        expected=hmac.new(self._key,_dump(body).encode(),hashlib.sha256).hexdigest()
        if (not hmac.compare_digest(receipt['signature'],expected) or receipt['id']!=_hash(_dump(body))
                or body['project_id']!=ledger.project_id or body['research_run_id']!=ledger.run_id
                or body['observer_node_id']!=self.observer_node_id):
            raise ValueError('Invalid or cross-scope controller retrieval signature')
        if body.get('schema')!='qc.controller_retrieval_receipt.v2':
            raise ValueError('Unsupported retrieval receipt version')
        reservation=ledger.conn.execute("""SELECT id FROM research_retrieval_attempts WHERE id=?
            AND project_id=? AND research_run_id=? AND actor='controller' AND operation_id=? AND status='SUCCEEDED' """,
            (body.get('observation_attempt_id'),ledger.project_id,ledger.run_id,body['execution_id'])).fetchone()
        if not reservation: raise ValueError('Missing successful controller observation reservation')
        raw=base64.b64decode(body['raw_base64'],validate=True)
        if (len(raw)>2_000_000 or hashlib.sha256(raw).hexdigest()!=body['raw_sha256']
                or _hash(body['extracted_text'])!=body['extracted_sha256']):
            raise ValueError('Retrieval snapshot integrity failure')
        native,inputs,output=self._context(ledger,body['execution_id'])
        expected_values={'task_id':native['task_id'],'lease_id':native['lease_id'],
            'worker_id':native['worker_id'],'idempotency_key':native['idempotency_key'],
            'orchestration_task_id':native['orchestration_task_id'],
            'orchestration_run_id':native['orchestration_run_id'],
            'action_sha256':_hash(native['action_json']),
            'worker_output_sha256':_hash(native['result_payload_json']),
            'revision_id':native['revision_id'],'approved_plan_sha256':_hash(native['approved_plan_json']),
            'producer_attempt':native['producer_attempt'],'lease_attempt':native['lease_attempt'],
            'feedback_path_id':inputs['feedback_path_id'],'requested_url':inputs['url'],
            'raw_sha256':output['raw_sha256'],'extracted_sha256':output['extracted_sha256'],
            'final_url':output['final_url']}
        if any(body[k]!=v for k,v in expected_values.items()):
            raise ValueError('Receipt does not match current native execution')
        return body
