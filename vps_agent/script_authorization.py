"""Online start permits; controller signing secrets never enter the worker.

The authenticated controller channel is the authority, not task JSON. Permits
are process-local objects; they are not a boundary against compromised worker code.
"""
from dataclasses import dataclass,field
import hashlib,json,threading,time
from urllib.parse import urlparse


def sha(value): return hashlib.sha256(value).hexdigest()
def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def binding(task):
    inputs=task.get('input_data') or {}; script=inputs.get('script') or inputs.get('code')
    manifest=task.get('execution_manifest') or inputs.get('execution_manifest')
    result={key:task[key] for key in ('task_id','lease_id','worker_id','idempotency_key','task_type')}
    if any(not isinstance(v,str) or not v for v in result.values()) or not isinstance(script,str):
        raise ValueError('Complete leased task identity required')
    result.update(script_sha256=sha(script.encode()),manifest_sha256=sha(canonical(manifest)))
    return result


def secure_controller_url(url):
    parsed=urlparse(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('Canonical controller origin required')
    if not parsed.hostname or (parsed.scheme!='https' and not
            (parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','::1','localhost'})):
        raise ValueError('HTTPS controller required except explicit loopback development')
    return url.rstrip('/')


@dataclass
class ScriptPermit:
    task_binding: dict
    authorization_id: str
    deadline_monotonic: float
    max_runtime_seconds: int
    _consumed: bool = field(default=False,init=False)
    _lock: object = field(default_factory=threading.Lock,init=False,repr=False)

    def consume(self,task):
        with self._lock:
            if (self._consumed or time.monotonic()>=self.deadline_monotonic
                    or self.task_binding!=binding(task) or type(self.max_runtime_seconds) is not int
                    or not 1<=self.max_runtime_seconds<=30):
                raise ValueError('Permit is stale, reused or does not match this task')
            self._consumed=True
            return self.max_runtime_seconds

    def check_start_deadline(self):
        if not self._consumed or time.monotonic()>=self.deadline_monotonic:
            raise ValueError('Permit start deadline expired')


def permit_from_response(task,response,nonce,request_started):
    fields={'schema','authorization_id','nonce','binding','start_within_seconds','max_runtime_seconds'}
    if (not isinstance(response,dict) or set(response)!=fields or response['schema']!='qc.script_start.v1'
            or response['nonce']!=nonce or response['binding']!=binding(task)
            or not isinstance(response['authorization_id'],str) or not response['authorization_id']
            or type(response['start_within_seconds']) is not int or not 1<=response['start_within_seconds']<=5
            or type(response['max_runtime_seconds']) is not int or not 1<=response['max_runtime_seconds']<=30):
        raise ValueError('Controller authorization response mismatch')
    deadline=request_started+response['start_within_seconds']
    if time.monotonic()>=deadline: raise ValueError('Authorization response arrived too late')
    return ScriptPermit(binding(task),response['authorization_id'],deadline,response['max_runtime_seconds'])
