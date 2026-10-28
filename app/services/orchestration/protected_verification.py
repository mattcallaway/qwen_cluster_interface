"""Controller-side bridge from native proposals to a protected test verifier.

No routes, startup hooks, production defaults, worker-selected transport or
certification override. A trusted operator must explicitly supply the transport.
Native task/lease/execution evidence is mandatory, including after execution.
"""
from datetime import datetime,timedelta,timezone
import secrets,uuid
from .code_proposals import encoded,digest
from app.services.artifacts import artifact_service
from app.services.research.claim_contracts import strict_json
from vps_agent.independent_verifier import validate_grant,check_receipt


class ProtectedVerification:
    def __init__(self,reviewer,transport=None,*,nonproduction=False):
        self.reviewer=reviewer; self.transport=transport; self.nonproduction=nonproduction

    def _fresh(self,review_id):
        r=self.reviewer
        row=r.conn.execute('''SELECT payload_json FROM software_review_items
            WHERE project_id=? AND run_id=? AND kind='review' AND item_id=?''',
            (r.project_id,r.run_id,review_id)).fetchone()
        if not row: raise ValueError('Recorded native proposal review required')
        old=strict_json(row[0]); fresh,_,_=r._inspect(old['deliverable_key'],old['proposal']['artifact_id'])
        if digest(encoded(fresh))!=review_id: raise ValueError('Stale native proposal review')
        rows=r.conn.execute('''SELECT e.id execution_id,l.id lease_id,e.idempotency_key,e.result_payload_json FROM worker_executions e
            JOIN worker_task_leases l ON e.lease_id=l.id AND e.task_id=l.task_id
                AND e.worker_id=l.worker_id AND e.idempotency_key=l.idempotency_key
            WHERE e.task_id=? AND e.worker_id=? AND e.status='success' AND e.exit_code=0 AND l.status='completed' ''',
            (fresh['proposal']['task_id'],fresh['proposal']['worker_id'])).fetchall()
        if len(rows)!=1 or not rows[0]['idempotency_key']: raise ValueError('Unique native execution/lease required')
        payload=strict_json(rows[0]['result_payload_json'])
        response=payload.get('response') if isinstance(payload,dict) else None
        if not isinstance(response,str) or digest(response.encode())!=fresh['proposal']['sha256']:
            raise ValueError('Proposal bytes must equal immutable native worker response; origin labels are insufficient')
        return fresh,{k:rows[0][k] for k in ('execution_id','lease_id','idempotency_key')}

    def verify(self,review_id,image_sha256,*,seconds=5):
        if self.nonproduction is not True or not callable(self.transport):
            raise ValueError('Protected verifier transport held; explicit non-production configuration required')
        r=self.reviewer
        if r.conn.in_transaction: raise ValueError('Commit caller transaction before independent verification')
        fresh,native=self._fresh(review_id)
        binding={k:fresh[k] for k in ('proposal','approved_tests','plan_revision_id','plan_sha256','contract_sha256','requirement_ids')}
        binding.update(review_id=review_id,project_id=r.project_id,run_id=r.run_id,image_sha256=image_sha256,
            native_execution_id=native['execution_id'],native_lease_id=native['lease_id'],native_idempotency_key=native['idempotency_key'])
        def raw(pin):
            data=artifact_service.storage.read_cas_blob(pin['cas_hash'])
            if digest(data)!=pin['sha256']: raise ValueError('Canonical bytes changed')
            return data.decode('utf-8')
        grant={'schema':'qc.protected_verifier_grant.v1','nonce':uuid.uuid4().hex,
            'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=180)).isoformat(),
            'binding':binding,'source_json':raw(fresh['proposal']),'tests_json':raw(fresh['approved_tests']),'seconds':seconds}
        validate_grant(grant)
        # Persist authority before IO. Never automatically retry a lost grant.
        issued=r._append('verification_grant',grant); r.conn.commit()
        key=secrets.token_bytes(32)  # passed only through trusted transport; never recorded in DB/artifacts
        try:
            receipt=self.transport(grant,key)
            if len(encoded(receipt))>600_000: raise ValueError('Receipt exceeds budget')
            cases=check_receipt(grant,receipt,key)
            after,native_after=self._fresh(review_id)
            if after!=fresh or native_after!=native: raise ValueError('Native binding changed during verification')
            result=r._append('protected_verification',{'schema':'qc.protected_verification.v1',
                'review_id':review_id,'grant_id':issued['id'],'receipt':receipt,'cases':cases,
                'executed':True,'behavior_verified':all(c['passed'] for c in cases),
                'scope':'approved integer black-box cases only','accepted':False,'certification_eligible':False,
                'decision':'CASES_PASSED' if all(c['passed'] for c in cases) else 'CASES_FAILED'})
            r.conn.commit(); return result
        except Exception:
            r._append('verification_failure',{'grant_id':issued['id'],'review_id':review_id,
                'decision':'BLOCKED','behavior_verified':False,'accepted':False,'certification_eligible':False})
            r.conn.commit(); raise

    def current_result(self,result_id):
        """Historical pass records never override changed current inputs.

        Only reads controller-created append-only records. Not an import API for
        externally supplied receipts; their signatures are checked before append.
        """
        r=self.reviewer
        row=r.conn.execute('''SELECT payload_json FROM software_review_items
            WHERE project_id=? AND run_id=? AND kind='protected_verification' AND item_id=?''',
            (r.project_id,r.run_id,result_id)).fetchone()
        if not row: raise ValueError('Recorded protected verification required')
        result=strict_json(row[0]); fresh,native=self._fresh(result['review_id'])
        binding=result['receipt']['body']['binding']
        for name in ('proposal','approved_tests','plan_revision_id','plan_sha256','contract_sha256','requirement_ids'):
            if binding[name]!=fresh[name]: raise ValueError('Stale protected verification')
        if (binding['native_execution_id']!=native['execution_id'] or binding['native_lease_id']!=native['lease_id']
                or binding['native_idempotency_key']!=native['idempotency_key']):
            raise ValueError('Stale native execution binding')
        return dict(result,current_inputs_revalidated=True)
