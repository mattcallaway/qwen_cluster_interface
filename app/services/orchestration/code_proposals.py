"""Version-bound, non-executing software proposals. No execution/certification authority.

Approved test bytes are kept outside the proposed source tree. Staging is a
developer primitive, not OS confinement against a hostile local filesystem actor.
Schema installation is explicit; no production migration or startup hook.
"""
from datetime import datetime,timezone
import hashlib,json,re,shutil,stat,tempfile
import uuid
from pathlib import Path
from app.services.artifacts import artifact_service
from app.services.research.claim_contracts import strict_json


def digest(data): return hashlib.sha256(data).hexdigest()
def encoded(value): return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def install_proposal_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS software_review_items (
        project_id TEXT NOT NULL,run_id TEXT NOT NULL,kind TEXT NOT NULL,
        item_id TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,
        PRIMARY KEY(project_id,run_id,kind,item_id))''')
    for operation in ('UPDATE','DELETE'):
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS software_review_no_{operation.lower()}
            BEFORE {operation} ON software_review_items
            BEGIN SELECT RAISE(ABORT,'software reviews are append-only'); END''')


def safe_path(value,tests=False):
    if not isinstance(value,str) or len(value)>180 or not re.fullmatch(r'[A-Za-z0-9_./-]+',value):
        raise ValueError('ASCII relative path required')
    parts=value.split('/')
    if (len(parts)<2 or any(not p or p in ('.','..') or p.endswith(('.',' ')) for p in parts)
            or parts[0] not in ({'tests'} if tests else {'src','web','docs'})):
        raise ValueError('Path outside approved source/test namespace')
    reserved={'con','prn','aux','nul','clock$'}|{f'{prefix}{i}' for prefix in ('com','lpt') for i in range(1,10)}
    if any(p.split('.')[0].lower() in reserved or p.startswith('.') for p in parts):
        raise ValueError('Reserved path component')
    name=parts[-1].lower()
    if (Path(value).suffix.lower() not in {'.py','.html','.css','.js','.json','.md'}
            or name in {'package.json','package-lock.json','setup.py','conftest.py','sitecustomize.py','usercustomize.py'}):
        raise ValueError('Dependency/configuration hook or unsupported file')
    return value


def files_from(document,tests=False):
    files=document['files']
    if not isinstance(files,list) or not 0<len(files)<=64: raise ValueError('Bounded file list required')
    result={}; aliases=set(); directories={}; total=0
    for entry in files:
        if not isinstance(entry,dict) or set(entry)!={'path','content'}: raise ValueError('Regular text files only')
        path=safe_path(entry['path'],tests)
        if path.casefold() in aliases: raise ValueError('Duplicate/case-colliding path')
        aliases.add(path.casefold())
        for i in range(1,len(path.split('/'))):
            prefix='/'.join(path.split('/')[:i]); key=prefix.casefold()
            if key in directories and directories[key]!=prefix: raise ValueError('Case-colliding directory')
            directories[key]=prefix
        if not isinstance(entry['content'],str) or '\x00' in entry['content']: raise ValueError('UTF-8 text required')
        content=entry['content'].encode('utf-8'); total+=len(content)
        if len(content)>250_000 or total>1_000_000: raise ValueError('Bundle exceeds byte budget')
        result[path]=content
    # A file may not also be the parent directory of another file.
    names={p.casefold() for p in result}
    if any('/'.join(p.split('/')[:i]).casefold() in names for p in result for i in range(1,len(p.split('/')))):
        raise ValueError('File/directory collision')
    return result


class CodeProposalReview:
    def __init__(self,conn,project_id,run_id):
        self.conn,self.project_id,self.run_id=conn,project_id,run_id

    def _append(self,kind,payload):
        identity=digest(encoded(payload)); text=encoded(payload).decode()
        old=self.conn.execute('''SELECT payload_json FROM software_review_items
            WHERE project_id=? AND run_id=? AND kind=? AND item_id=?''',
            (self.project_id,self.run_id,kind,identity)).fetchone()
        if old and old[0]!=text: raise ValueError('Immutable review collision')
        if not old:
            self.conn.execute('INSERT INTO software_review_items VALUES (?,?,?,?,?,?)',
                (self.project_id,self.run_id,kind,identity,text,datetime.now(timezone.utc).isoformat()))
        return dict(payload,id=identity)

    def _contract(self,key):
        row=self.conn.execute('''SELECT rev.id,rev.plan_json FROM orchestration_runs r
            JOIN orchestration_plan_revisions rev ON rev.id=r.current_revision_id AND rev.run_id=r.id
            WHERE r.id=? AND r.project_id=? AND rev.status='approved' ''',
            (self.run_id,self.project_id)).fetchone()
        if not row: raise ValueError('Current approved scoped plan required')
        plan=strict_json(row['plan_json']); deliverables=[d for d in plan['deliverables'] if d.get('key')==key]
        if len(deliverables)!=1: raise ValueError('Unique approved deliverable required')
        contract=deliverables[0]['acceptance_criteria']
        if isinstance(contract,str): contract=strict_json(contract)
        if (not isinstance(contract,dict) or set(contract)!={'schema','requirements','allowed_files',
                'test_artifact_id','test_version','test_sha256'} or contract['schema']!='qc.software_contract.v1'):
            raise ValueError('Unsupported software contract')
        requirements=contract['requirements']
        if (not isinstance(requirements,list) or not 0<len(requirements)<=64
                or any(not isinstance(r,dict) or set(r)!={'id','description'}
                    or any(not isinstance(v,str) or not v.strip() or len(v)>2000 for v in r.values()) for r in requirements)
                or len({r['id'] for r in requirements})!=len(requirements)):
            raise ValueError('Explicit unique requirement records required')
        allowed=contract['allowed_files']
        if not isinstance(allowed,list) or not 0<len(allowed)<=64: raise ValueError('Exact file inventory required')
        for path in allowed: safe_path(path)
        if len({p.casefold() for p in allowed})!=len(allowed): raise ValueError('Colliding approved paths')
        if type(contract['test_version']) is not int or contract['test_version']<1:
            raise ValueError('Pinned test version required')
        return {'plan_revision_id':row['id'],'producer_plan_key':deliverables[0].get('produced_by',key),
                'plan_sha256':digest(row['plan_json'].encode()),
                'contract_sha256':digest(encoded(contract))},contract

    def _artifact(self,identity):
        row=self.conn.execute('SELECT * FROM artifacts WHERE id=?',(identity,)).fetchone()
        if (not row or row['origin_project_id']!=self.project_id or row['origin_run_id']!=self.run_id
                or row['status'] not in ('available','accepted') or row['validation_status']!='valid'):
            raise ValueError('Current canonical scoped artifact required')
        version=self.conn.execute('SELECT * FROM artifact_versions WHERE artifact_id=? AND version_number=?',
                                 (identity,row['current_version_number'])).fetchone()
        if (not version or version['checksum_sha256']!=row['checksum_sha256']
                or version['cas_hash']!=row['cas_hash'] or version['validation_status']!='valid'
                or version['producer_attempt']!=row['producer_attempt']
                or version['producing_run_id']!=self.run_id
                or version['producing_task_id']!=row['origin_task_id']
                or version['producing_worker_id']!=row['origin_worker_id']):
            raise ValueError('Canonical artifact/version mismatch')
        raw=artifact_service.storage.read_cas_blob(version['cas_hash'])
        if len(raw)>1_500_000 or digest(raw)!=row['checksum_sha256']: raise ValueError('Artifact byte integrity failure')
        binding={'artifact_id':identity,'version':row['current_version_number'],'sha256':digest(raw),
                 'cas_hash':version['cas_hash'],'task_id':version['producing_task_id'],
                 'worker_id':version['producing_worker_id'],'attempt':version['producer_attempt']}
        return binding,strict_json(raw.decode('utf-8'))

    def _inspect(self,key,artifact_id):
        plan,contract=self._contract(key)
        source,document=self._artifact(artifact_id)
        if not isinstance(document,dict) or set(document)!={'schema','files'} or document['schema']!='qc.code_bundle.v1':
            raise ValueError('Closed code bundle required')
        proposal=files_from(document)
        if set(proposal)!=set(contract['allowed_files']): raise ValueError('Approved inventory mismatch')
        action=self.conn.execute('''SELECT DISTINCT o.id,o.action_json FROM orchestration_tasks o
            JOIN tasks t ON t.id=o.current_task_id AND t.project_id=?
            JOIN worker_task_leases l ON l.task_id=t.id AND l.status='completed'
            JOIN worker_executions e ON e.task_id=t.id AND e.lease_id=l.id
                AND e.worker_id=l.worker_id AND e.idempotency_key=l.idempotency_key
            JOIN artifacts a ON a.origin_task_id=t.id AND a.origin_worker_id=e.worker_id
            WHERE o.run_id=? AND o.revision_id=? AND o.plan_key=? AND t.id=? AND a.id=?
                AND t.status='completed' AND e.status='success' AND e.exit_code=0
                AND o.dispatched_node=e.worker_id AND a.checksum_sha256=?''',
            (self.project_id,self.run_id,plan['plan_revision_id'],plan['producer_plan_key'],source['task_id'],artifact_id,source['sha256'])).fetchall()
        if len(action)!=1 or strict_json(action[0]['action_json']).get('type')!='inference':
            raise ValueError('Native inference proposal required')
        tests_binding,tests_doc=self._artifact(contract['test_artifact_id'])
        if tests_binding['artifact_id']==artifact_id: raise ValueError('Tests must be separately approved')
        if tests_binding['version']!=contract['test_version'] or tests_binding['sha256']!=contract['test_sha256']:
            raise ValueError('Approved test binding changed')
        if (not isinstance(tests_doc,dict) or set(tests_doc)!={'schema','requirement_ids','files'}
                or tests_doc['schema']!='qc.independent_tests.v1'
                or not isinstance(tests_doc['requirement_ids'],list)
                or len(tests_doc['requirement_ids'])!=len(contract['requirements'])
                or set(tests_doc['requirement_ids'])!={r['id'] for r in contract['requirements']}):
            raise ValueError('Exact test requirement inventory required')
        tests=files_from(tests_doc,tests=True)
        payload={'schema':'qc.code_proposal_review.v1','deliverable_key':key,
            'proposal':source,'approved_tests':tests_binding,**plan,
            'requirement_ids':[r['id'] for r in contract['requirements']],
            'files':[{'path':p,'sha256':digest(b),'size':len(b)} for p,b in sorted(proposal.items())],
            'test_files':[{'path':p,'sha256':digest(b),'size':len(b)} for p,b in sorted(tests.items())],
            'stageable':True,'executed':False,'behavior_verified':False,
            'accepted':False,'certification_eligible':False,'decision':'BLOCKED',
            'limits':['Approved test mapping is not proof of test adequacy or independent authorship.',
                      'Staging is not execution authority or OS containment.']}
        return payload,proposal,tests

    def review(self,key,artifact_id):
        payload,_,_=self._inspect(key,artifact_id)
        return self._append('review',payload)

    def capture_native_response(self,key):
        """Capture exact immutable worker bytes, never developer-rewritten code.

        This does not call Ollama, run code, normalize fences or grant acceptance.
        The controller's protected verification API rechecks the resulting review.
        """
        plan,contract=self._contract(key)
        rows=self.conn.execute('''SELECT o.id orch_id,o.action_json,o.attempts,t.id task_id,
                e.worker_id,e.result_payload_json FROM orchestration_tasks o
            JOIN tasks t ON t.id=o.current_task_id AND t.project_id=? AND t.status='completed'
            JOIN worker_task_leases l ON l.task_id=t.id AND l.status='completed'
            JOIN worker_executions e ON e.lease_id=l.id AND e.task_id=t.id AND e.worker_id=l.worker_id
                AND e.idempotency_key=l.idempotency_key AND e.status='success' AND e.exit_code=0
            WHERE o.run_id=? AND o.revision_id=? AND o.plan_key=? AND o.dispatched_node=e.worker_id''',
            (self.project_id,self.run_id,plan['plan_revision_id'],plan['producer_plan_key'])).fetchall()
        if len(rows)!=1 or strict_json(rows[0]['action_json']).get('type')!='inference':
            raise ValueError('Unique completed native inference required')
        row=rows[0]; payload=strict_json(row['result_payload_json'])
        response=payload.get('response') if isinstance(payload,dict) else None
        if not isinstance(response,str) or len(response.encode())>1_500_000: raise ValueError('Bounded immutable native response required')
        doc=strict_json(response)
        if not isinstance(doc,dict) or set(doc)!={'schema','files'} or doc['schema']!='qc.code_bundle.v1':
            raise ValueError('Exact native code-bundle response required; no repair during capture')
        if set(files_from(doc))!=set(contract['allowed_files']): raise ValueError('Approved native file inventory mismatch')
        raw=response.encode(); checksum=digest(raw)
        existing=self.conn.execute('''SELECT id FROM artifacts WHERE origin_project_id=? AND origin_run_id=?
            AND origin_task_id=? AND origin_worker_id=? AND checksum_sha256=? AND validation_status='valid'
            AND status IN ('available','accepted') ORDER BY id''',
            (self.project_id,self.run_id,row['task_id'],row['worker_id'],checksum)).fetchall()
        if existing: return existing[0]['id']
        artifact=artifact_service.register_artifact(name='native-code-bundle.json',data=raw,
            project_id=self.project_id,run_id=self.run_id,task_id=row['task_id'],orch_task_id=row['orch_id'],
            worker_id=row['worker_id'],producer_attempt=max(1,row['attempts']),artifact_type='code',
            expected_sha256=checksum,validation_detail='Native bytes structurally checked; behavior unverified',db_conn=self.conn)
        return artifact['id']

    def prepare_linux_plan(self,review_id,image_sha256):
        """Reviewable preparation only. No Linux process or deployment starts."""
        from vps_agent.linux_sandbox import LinuxSpec
        row=self.conn.execute('''SELECT payload_json FROM software_review_items
            WHERE project_id=? AND run_id=? AND kind='review' AND item_id=?''',
            (self.project_id,self.run_id,review_id)).fetchone()
        if not row: raise ValueError('Recorded scoped proposal review required')
        old=strict_json(row[0]); fresh,_,_=self._inspect(old['deliverable_key'],old['proposal']['artifact_id'])
        if digest(encoded(fresh))!=review_id: raise ValueError('Stale proposal review')
        spec=LinuxSpec(uuid.uuid4().hex,fresh['proposal']['sha256'],image_sha256,
            tuple((f['path'],f['sha256']) for f in fresh['files'])).validate()
        return self._append('linux_build_plan',{'schema':'qc.linux_build_plan.v1','review_id':review_id,
            'proposal':fresh['proposal'],'approved_tests':fresh['approved_tests'],
            'plan_revision_id':fresh['plan_revision_id'],'contract_sha256':fresh['contract_sha256'],
            'requirement_ids':fresh['requirement_ids'],'unit':spec.unit,'image_sha256':spec.image_sha256,
            'source_files':fresh['files'],'service_argv':spec.command(),
            'image_approval_required':True,'backend_verified':False,'dispatchable':False,
            'accepted':False,'certification_eligible':False,'production_approved':False})

    def stage(self,review_id,root):
        row=self.conn.execute('''SELECT payload_json FROM software_review_items
            WHERE project_id=? AND run_id=? AND kind='review' AND item_id=?''',
            (self.project_id,self.run_id,review_id)).fetchone()
        if not row: raise ValueError('Recorded scoped review required')
        old=strict_json(row[0])
        try:
            payload,proposal,tests=self._inspect(old['deliverable_key'],old['proposal']['artifact_id'])
            if digest(encoded(payload))!=review_id: raise ValueError('Stale proposal review; revalidation required')
        except (ValueError,TypeError,KeyError,OSError) as exc:
            self._append('invalidation',{'review_id':review_id,'reason':'BINDING_OR_INPUT_CHANGED',
                                         'requires_revalidation':True})
            raise ValueError('Stale or unverifiable proposal review') from exc
        root=Path(root).absolute()
        for part in (root,*root.parents):
            if part.is_symlink() or (part.exists() and getattr(part.lstat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError('Linked/reparse staging roots forbidden')
        if not root.is_dir(): raise ValueError('Explicit existing staging root required')
        root=root.resolve(); directory=Path(tempfile.mkdtemp(prefix='qc-proposal-',dir=root)).resolve()
        try:
            for namespace,files in (('proposal',proposal),('acceptance',tests)):
                for path,content in files.items():
                    dest=directory/namespace/path
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    with dest.open('xb') as stream: stream.write(content)
            fresh,_,_=self._inspect(old['deliverable_key'],old['proposal']['artifact_id'])
            if digest(encoded(fresh))!=review_id: raise ValueError('Inputs changed during staging')
            manifest={'review_id':review_id,'proposal':payload['files'],'acceptance':payload['test_files'],
                      'executed':False,'certification_eligible':False}
            (directory/'staging-manifest.json').write_bytes(encoded(manifest))
            audit=self._append('staging',{'review_id':review_id,'directory':str(directory),
                                         'manifest_sha256':digest(encoded(manifest)),'executed':False})
            return {'directory':str(directory),'review_id':review_id,'audit_id':audit['id'],
                    'executed':False,'certification_eligible':False}
        except Exception:
            # Only the unique directory created above may be recursively removed.
            if directory.parent==root and directory.name.startswith('qc-proposal-') and directory.resolve().parent==root:
                shutil.rmtree(directory)
            raise
