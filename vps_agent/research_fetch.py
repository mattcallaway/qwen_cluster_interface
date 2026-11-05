"""Dedicated bounded research operation; never executes scripts from task data."""
import hashlib
import time
from urllib.parse import urlparse
from vps_agent.research_runtime.retrieval import SecureFetcher
from vps_agent.research_runtime.policy import ResearchSecurityPolicy
from vps_agent.research_runtime.extraction import DocumentExtractor, detect_challenge_page


class HttpsRetrievalPolicy(ResearchSecurityPolicy):
    def validate_url(self,url):
        if urlparse(url).scheme!='https': return False,'Retrieval requires HTTPS at every redirect',None
        return super().validate_url(url)


def execute_retrieval(task,timeout_seconds=8,allow_web_fetch=False):
    start=time.monotonic()
    def failure(message):
        return {'status':'error','exit_code':1,'runtime_seconds':time.monotonic()-start,
            'stdout_summary':'','stderr_summary':message,'error_category':'research_retrieval_rejected',
            'result_payload':{}}
    if allow_web_fetch is not True: return failure('Local web retrieval is disabled')
    if not all(task.get(k) for k in ('task_id','lease_id','idempotency_key')):
        return failure('Native lease identity is required')
    inputs=task.get('input_data') or {}
    if set(inputs)-{'url','feedback_path_id'}:
        return failure('Unsupported retrieval task fields')
    if not inputs.get('url') or not inputs.get('feedback_path_id'):
        return failure('Queued URL and feedback proposal required')
    try:
        from datetime import datetime,timezone
        from vps_agent.research_job import run_bounded_retrieval,RetrievalDeadlineExceeded
        reservation=task.get('retrieval_reservation') or {}
        if not reservation.get('id') or not reservation.get('io_deadline_at'):
            return failure('Controller retrieval budget reservation is required')
        limit=datetime.fromisoformat(reservation['io_deadline_at'])
        remaining=(limit-datetime.now(timezone.utc)).total_seconds()
        if remaining<=0: raise RetrievalDeadlineExceeded('Reservation deadline expired')
        result=run_bounded_retrieval(inputs['url'],min(8,float(timeout_seconds),remaining))
        return {'status':'success','exit_code':0,'runtime_seconds':time.monotonic()-start,
            'stdout_summary':'Bounded source retrieval completed; factual claims remain unverified.',
            'stderr_summary':'','error_category':None,'result_payload':{
                'schema':'qc.retrieval_result.v1','requested_url':inputs['url'],'final_url':result['final_url'],
                'raw_sha256':result['raw_sha256'],'extracted_sha256':result['extracted_sha256']}}
    except TimeoutError:
        result=failure('Retrieval hard deadline exceeded')
        result['status']='timeout'; result['error_category']='retrieval_deadline'
        return result
    except Exception as exc:
        return failure('Retrieval failed: '+type(exc).__name__)
