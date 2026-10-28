"""Protected, non-production black-box verifier. No worker startup hook.

The controller provisions an authenticated grant and a private per-run key over
a trusted transport. Only the public input is mounted inside each service.
This process and the controller, not submitted stdout, decide case outcomes.
"""
import hashlib,hmac,json,os,re,stat,uuid
from datetime import datetime,timezone
from pathlib import Path
from .linux_sandbox import LinuxSpec,run_linux_candidate,hex_digest


def encoded(value): return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def digest(data): return hashlib.sha256(data).hexdigest()
def strict(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def cases_from(raw,requirement_ids):
    if not isinstance(raw,str) or len(raw.encode())>100_000: raise ValueError('Bounded approved tests required')
    doc=strict(raw)
    if (not isinstance(doc,dict) or set(doc)!={'schema','requirement_ids','files'}
            or doc['schema']!='qc.independent_tests.v1' or doc['requirement_ids']!=requirement_ids
            or not isinstance(doc['files'],list) or len(doc['files'])!=1
            or set(doc['files'][0])!={'path','content'} or doc['files'][0]['path']!='tests/cases.json'):
        raise ValueError('Approved declarative black-box cases required')
    suite=strict(doc['files'][0]['content'])
    if not isinstance(suite,dict) or set(suite)!={'schema','cases'}:
        raise ValueError('Unsupported test profile')
    cases=suite['cases']; seen=set()
    if suite['schema']=='qc.integer_cases.v1':
        if not isinstance(cases,list) or not 2<=len(cases)<=8: raise ValueError('Two to eight independent cases required')
        for case in cases:
            if (not isinstance(case,dict) or set(case)!={'id','requirement_id','input','expected'}
                    or not isinstance(case['id'],str) or not re.fullmatch('[a-zA-Z0-9_-]{1,64}',case['id'])
                    or case['id'] in seen or case['requirement_id'] not in requirement_ids
                    or not isinstance(case['input'],dict) or set(case['input'])!={'a','b'}
                    or any(type(v) is not int or abs(v)>1_000_000 for v in [*case['input'].values(),case['expected']])):
                raise ValueError('Invalid integer case')
            seen.add(case['id'])
    elif suite['schema']=='qc.json_io_cases.v1':
        if not isinstance(cases,list) or not 2<=len(cases)<=16: raise ValueError('Two to sixteen independent cases required')
        for case in cases:
            if (not isinstance(case,dict) or set(case)!={'id','requirement_id','input','expected'}
                    or not isinstance(case['id'],str) or not re.fullmatch('[a-zA-Z0-9_-]{1,64}',case['id'])
                    or case['id'] in seen or case['requirement_id'] not in requirement_ids):
                raise ValueError('Invalid JSON I/O case')
            if len(encoded(case['input']))>32_768 or len(encoded(case['expected']))>32_768:
                raise ValueError('Case I/O exceeds byte limit')
            seen.add(case['id'])
    else:
        raise ValueError('Unsupported test profile')
    if {c['requirement_id'] for c in cases}!=set(requirement_ids): raise ValueError('Uncovered requirement')
    return cases


def validate_grant(grant,now=None):
    if not isinstance(grant,dict) or set(grant)!={'schema','nonce','expires_at','binding','source_json','tests_json','seconds'}:
        raise ValueError('Closed verification grant required')
    if grant['schema']!='qc.protected_verifier_grant.v1' or not re.fullmatch('[0-9a-f]{32}',grant['nonce']):
        raise ValueError('Invalid grant identity')
    now=now or datetime.now(timezone.utc)
    expiry=datetime.fromisoformat(grant['expires_at'])
    if expiry.tzinfo is None or not 0<(expiry-now).total_seconds()<=300: raise ValueError('Expired/broad grant')
    if type(grant['seconds']) is not int or not 1<=grant['seconds']<=15: raise ValueError('Bounded per-case deadline required')
    binding=grant['binding']
    required={'review_id','project_id','run_id','native_execution_id','native_lease_id','native_idempotency_key',
              'proposal','approved_tests','plan_revision_id','plan_sha256','contract_sha256','image_sha256','requirement_ids'}
    if not isinstance(binding,dict) or set(binding)!=required: raise ValueError('Closed native binding required')
    for field in ('review_id','plan_sha256','contract_sha256','image_sha256'): hex_digest(binding[field])
    for name in ('source_json','tests_json'):
        if not isinstance(grant[name],str) or len(grant[name].encode())>1_500_000: raise ValueError('Bounded JSON bytes required')
        pin=binding['proposal' if name=='source_json' else 'approved_tests']['sha256']
        if digest(grant[name].encode())!=hex_digest(pin): raise ValueError('Grant byte pin mismatch')
    source=strict(grant['source_json'])
    if not isinstance(source,dict) or set(source)!={'schema','files'} or source['schema']!='qc.code_bundle.v1':
        raise ValueError('Closed source bundle required')
    files={}; aliases=set(); total=0
    if not isinstance(source['files'],list) or not 0<len(source['files'])<=64: raise ValueError('Bounded files required')
    for entry in source['files']:
        if not isinstance(entry,dict) or set(entry)!={'path','content'}: raise ValueError('Regular source text required')
        path=entry['path']
        if (not isinstance(path,str) or len(path)>180 or not re.fullmatch('(src|web|docs)/[A-Za-z0-9_./-]+',path)
                or any(p in ('','.','..') or p.startswith('.') for p in path.split('/'))
                or path.casefold() in aliases or not isinstance(entry['content'],str) or '\x00' in entry['content']):
            raise ValueError('Unsafe source file')
        content=entry['content'].encode(); total+=len(content)
        if len(content)>250_000 or total>1_000_000: raise ValueError('Source budget exceeded')
        aliases.add(path.casefold()); files[path]=content
    if 'src/main.py' not in files: raise ValueError('Python entry point required')
    for path in files:
        if any('/'.join(path.split('/')[:i]).casefold() in aliases for i in range(1,len(path.split('/')))):
            raise ValueError('File/directory collision')
    return files,cases_from(grant['tests_json'],binding['requirement_ids'])


def evaluate_observations(cases,observations):
    if not isinstance(observations,list) or len(observations)!=len(cases): raise ValueError('Exact observation inventory required')
    results=[]
    for case,observation in zip(cases,observations):
        if (not isinstance(observation,dict) or set(observation)!={'case_id','executed','reason','exit_code','cleanup_confirmed','stdout','unit'}
                or observation['case_id']!=case['id'] or type(observation['executed']) is not bool
                or type(observation['cleanup_confirmed']) is not bool
                or not isinstance(observation['reason'],str)
                or not isinstance(observation['unit'],str) or not re.fullmatch('qc-sandbox-[0-9a-f]{32}.service',observation['unit'])
                or not isinstance(observation['stdout'],str) or len(observation['stdout'].encode())>65536):
            raise ValueError('Closed ordered observation required')
        passed=False
        if (observation['executed'] is True and observation['cleanup_confirmed'] is True
                and observation['reason']=='completed' and type(observation['exit_code']) is int and observation['exit_code']==0):
            try:
                answer=strict(observation['stdout'])
                if isinstance(answer,dict) and set(answer)=={'value'} and type(answer['value']) is int and type(case['expected']) is int and answer['value']==case['expected']:
                    passed=True
                elif type(answer) is type(case['expected']) and answer==case['expected']:
                    passed=True
            except (ValueError,TypeError): pass
        results.append({'case_id':case['id'],'requirement_id':case['requirement_id'],'passed':passed})
    return results


def receipt_for(grant,observations,key):
    if not isinstance(key,bytes) or len(key)!=32: raise ValueError('Private 256-bit receipt key required')
    body={'schema':'qc.protected_verifier_receipt.v1','grant_sha256':digest(encoded(grant)),
          'nonce':grant['nonce'],'binding':grant['binding'],'observations':observations}
    return {'body':body,'signature':hmac.new(key,encoded(body),hashlib.sha256).hexdigest()}


def check_receipt(grant,receipt,key):
    _,cases=validate_grant(grant)
    if not isinstance(receipt,dict) or set(receipt)!={'body','signature'} or not isinstance(receipt['signature'],str):
        raise ValueError('Authenticated receipt required')
    body=receipt['body']
    if (not isinstance(body,dict) or set(body)!={'schema','grant_sha256','nonce','binding','observations'}
            or body['schema']!='qc.protected_verifier_receipt.v1' or body['binding']!=grant['binding']
            or body['nonce']!=grant['nonce'] or body['grant_sha256']!=digest(encoded(grant))
            or not hmac.compare_digest(receipt['signature'],hmac.new(key,encoded(body),hashlib.sha256).hexdigest())):
        raise ValueError('Receipt authentication/binding failure')
    return evaluate_observations(cases,body['observations'])


def protected_directory(path):
    path=Path(path)
    for part in (path,*path.parents):
        info=part.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise ValueError('Root-owned non-writable directory required')
    return path


def immutable_file(path,content):
    path=Path(path); protected_directory(path.parent)
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444)
    except FileExistsError:
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode&0o222 or path.read_bytes()!=content:
            raise ValueError('Conflicting immutable input')
    else:
        with os.fdopen(fd,'wb') as stream: stream.write(content)


def run_protected(grant,key):
    """Called only by root on an explicitly authorized test host, never by code.

    Root's private audit directory consumes the nonce BEFORE any execution. A
    transport failure or crash cannot authorize a second execution of that grant.
    """
    if os.name!='posix' or os.geteuid()!=0: raise ValueError('Protected Linux verifier required')
    files,cases=validate_grant(grant)
    base=protected_directory('/var/lib/qwen-sandbox')
    for name in ('inputs','requests','verifier-audit'):
        (base/name).mkdir(mode=0o755,exist_ok=True); protected_directory(base/name)
    audit=base/'verifier-audit'/grant['nonce']; audit.mkdir(mode=0o700)
    immutable_file(audit/'grant.json',encoded(grant))
    source=base/'inputs'/grant['binding']['proposal']['sha256']
    source.mkdir(mode=0o755,exist_ok=True); protected_directory(source)
    for path,content in files.items():
        parent=source/Path(path).parent
        parent.mkdir(mode=0o755,parents=True,exist_ok=True); protected_directory(parent)
        immutable_file(source/path,content)
    observations=[]
    for case in cases:
        # Recheck expiry before every start, not just upon receiving the grant.
        validate_grant(grant)
        request=encoded(case['input']); request_sha=digest(request)
        immutable_file(base/'requests'/(request_sha+'.json'),request)
        spec=LinuxSpec(uuid.uuid4().hex,grant['binding']['proposal']['sha256'],grant['binding']['image_sha256'],
            tuple((p,digest(b)) for p,b in sorted(files.items())),seconds=grant['seconds'],request_sha256=request_sha)
        result=run_linux_candidate(spec)
        observations.append({'case_id':case['id'],**{k:result.get(k) for k in
            ('executed','reason','exit_code','cleanup_confirmed','stdout','unit')}})
        # Never start another case after unconfirmed process cleanup.
        if result.get('cleanup_confirmed') is not True: raise RuntimeError('Unconfirmed cleanup; verifier stopped')
    receipt=receipt_for(grant,observations,key)
    immutable_file(audit/'receipt.json',encoded(receipt))
    return receipt
