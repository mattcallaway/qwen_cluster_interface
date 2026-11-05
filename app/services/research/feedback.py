"""Append-only research feedback, scoped to a project and research run.

Snapshot quotation support is not proof of external truth. This subsystem cannot
certify deliverables or authorize execution. Caller owns the database transaction.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


def _dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def install_feedback_schema(conn):
    """Explicit development schema installation; never called by app startup."""
    from app.services.research.retrieval_attempts import install_attempt_schema
    install_attempt_schema(conn)
    conn.execute('''CREATE TABLE IF NOT EXISTS research_feedback_items (
        project_id TEXT NOT NULL, run_id TEXT NOT NULL, kind TEXT NOT NULL,
        item_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(project_id,run_id,kind,item_id))''')
    for operation in ('UPDATE', 'DELETE'):
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS feedback_no_{operation.lower()}
            BEFORE {operation} ON research_feedback_items
            BEGIN SELECT RAISE(ABORT,'research feedback is append-only'); END''')


class ProjectEvidenceFeedback:
    def __init__(self, conn, project_id, research_run_id, receipt_verifier=None):
        self.conn, self.project_id, self.run_id = conn, project_id, research_run_id
        self.receipt_verifier=receipt_verifier
        row = conn.execute('SELECT project_id FROM research_runs WHERE id=?', (research_run_id,)).fetchone()
        if not row or row[0] != project_id:
            raise ValueError('Research run does not belong to project')

    def items(self, kind):
        return [json.loads(row[0]) for row in self.conn.execute(
            '''SELECT payload_json FROM research_feedback_items
               WHERE project_id=? AND run_id=? AND kind=? ORDER BY item_id''',
            (self.project_id, self.run_id, kind))]

    def _append(self, kind, item_id, payload):
        old = self.conn.execute('''SELECT payload_json FROM research_feedback_items
            WHERE project_id=? AND run_id=? AND kind=? AND item_id=?''',
            (self.project_id, self.run_id, kind, item_id)).fetchone()
        encoded = _dump(payload)
        if old:
            if old[0] != encoded:
                raise ValueError('Immutable identity reused with different content')
            return payload
        self.conn.execute('INSERT INTO research_feedback_items VALUES (?,?,?,?,?,?)',
            (self.project_id, self.run_id, kind, item_id, encoded, datetime.now(timezone.utc).isoformat()))
        return payload

    def add_requirement(self, requirement_id, question, deliverable_key):
        if not all(isinstance(x,str) and x.strip() for x in (requirement_id,question,deliverable_key)):
            raise ValueError('Requirement identity, question and deliverable required')
        return self._append('requirement', requirement_id,
            {'id':requirement_id, 'question':question, 'deliverable_key':deliverable_key})

    def add_claim(self, claim_id, statement, requirement_ids, counterstatement=None):
        if not isinstance(statement,str) or not statement.strip() or '\n' in statement:
            raise ValueError('One complete source assertion is required')
        if not isinstance(claim_id,str) or not claim_id.strip():
            raise ValueError('Claim ID required')
        if counterstatement is not None and (not isinstance(counterstatement,str)
                or not counterstatement.strip() or '\n' in counterstatement or counterstatement==statement):
            raise ValueError('Counterstatement must be a distinct complete assertion')
        known={r['id'] for r in self.items('requirement')}
        if not requirement_ids or not set(requirement_ids).issubset(known):
            raise ValueError('Claims must link to existing scoped requirements')
        return self._append('claim',claim_id,{'id':claim_id,'statement':statement,
            'counterstatement':counterstatement,'requirement_ids':sorted(set(requirement_ids))})

    def capture_registered_source(self, source_id, allowed_storage_root):
        """Freeze actual extracted bytes, not caller-supplied excerpts/verdicts.

        Existing source registry provenance remains untrusted for certification:
        ingest_text can currently create fetched-looking records. This adapter
        therefore labels snapshots REGISTERED_SOURCE, never VERIFIED_RETRIEVAL.
        """
        cursor=self.conn.execute('SELECT * FROM research_sources WHERE id=? AND project_id=? AND research_run_id=?',
            (source_id,self.project_id,self.run_id))
        row=cursor.fetchone()
        if not row: raise ValueError('Missing or cross-run source')
        source=dict(zip([c[0] for c in cursor.description],row))
        if source.get('status')!='fetched' or source.get('retrieval_method') not in ('http','browser'):
            raise ValueError('Source retrieval metadata unavailable')
        url=source.get('canonical_url','')
        if urlparse(url).scheme not in ('https','http') or not urlparse(url).hostname:
            raise ValueError('Invalid source URL')
        if not source.get('retrieved_at'): raise ValueError('Retrieval time required')
        stamp=datetime.fromisoformat(source['retrieved_at'].replace('Z','+00:00'))
        if stamp.tzinfo is None: raise ValueError('Retrieval time must include timezone')
        root=Path(allowed_storage_root).resolve()
        path=Path(source['storage_path']).resolve()
        if not path.is_relative_to(root): raise ValueError('Source file outside allowed storage root')
        with path.open('rb') as stream:
            raw=stream.read(2_000_001)
        if len(raw)>2_000_000: raise ValueError('Snapshot exceeds feedback size limit')
        text=raw.decode('utf-8')
        if not text.strip(): raise ValueError('Empty snapshot')
        digest=hashlib.sha256(raw).hexdigest()
        # The source library's hash may refer to raw HTML/PDF, not extracted text.
        payload={'id':_hash(source_id+'\0'+digest), 'source_id':source_id, 'url':url,
            'retrieved_at':source['retrieved_at'],'text':text,'sha256':digest,
            'reported_raw_hash':source.get('content_hash'),
            'content_family':_hash(' '.join(text.split())),
            'provenance':'REGISTERED_SOURCE', 'independence':'UNVERIFIED'}
        return self._append('snapshot',payload['id'],payload)

    def capture_native_retrieval(self, execution_id):
        if self.receipt_verifier is None:
            raise ValueError('A controller receipt verifier is required')
        receipt=self.receipt_verifier.observe(self,execution_id)
        body=receipt['body']
        snapshot={'id':_hash(receipt['id']+'\0'+body['extracted_sha256']),
            'source_id':receipt['id'],'url':body['final_url'],'retrieved_at':body['observed_at'],
            'text':body['extracted_text'],'sha256':body['extracted_sha256'],
            'reported_raw_hash':body['raw_sha256'],'content_family':_hash(' '.join(body['extracted_text'].split())),
            'provenance':'CONTROLLER_OBSERVED_NATIVE_RETRIEVAL','independence':'UNVERIFIED',
            'retrieval_receipt_id':receipt['id']}
        return self._append('snapshot',snapshot['id'],snapshot)

    @staticmethod
    def _passages(snapshot, assertion):
        found=[]; offset=0
        for line in snapshot['text'].splitlines(keepends=True):
            body=line.rstrip('\r\n')
            if body==assertion:
                found.append({'snapshot_id':snapshot['id'],'start':offset,'end':offset+len(body),
                    'sha256':snapshot['sha256'],'quote':body})
            offset+=len(line)
        return found

    def assess(self):
        snapshots=self.items('snapshot')
        authenticated=set()
        for snapshot in snapshots:
            if _hash(snapshot['text'])!=snapshot['sha256']:
                raise ValueError('Stored snapshot integrity failure')
            if snapshot.get('retrieval_receipt_id') and self.receipt_verifier is not None:
                receipt=next((r for r in self.items('retrieval_receipt') if r['id']==snapshot['retrieval_receipt_id']),None)
                if not receipt: raise ValueError('Missing controller retrieval receipt')
                body=self.receipt_verifier.verify(self,receipt)
                if body['extracted_text']!=snapshot['text'] or body['extracted_sha256']!=snapshot['sha256']:
                    raise ValueError('Snapshot and retrieval attestation disagree')
                authenticated.add(snapshot['id'])
        results=[]
        for claim in self.items('claim'):
            support=[]; conflict=[]; families=set(); conflict_families=set(); authenticated_support=set()
            for snapshot in snapshots:
                matches=self._passages(snapshot,claim['statement'])
                support.extend(matches)
                if matches: families.add(snapshot['content_family'])
                if matches and snapshot['id'] in authenticated: authenticated_support.add(snapshot['content_family'])
                if claim['counterstatement']:
                    opposing=self._passages(snapshot,claim['counterstatement'])
                    conflict.extend(opposing)
                    if opposing: conflict_families.add(snapshot['content_family'])
            status='CONFLICT' if conflict else ('SUPPORTED_QUOTATION' if support else 'MISSING_EVIDENCE')
            results.append({**claim,'status':status,'support':support,'contradictions':conflict,
                'distinct_content_count':len(families),'independent_source_count':None,
                'authenticated_content_count':len(authenticated_support),
                'confidence': 'CONFLICTED' if conflict else ('AUTHENTICATED_SOURCE_QUOTATION' if authenticated_support
                    else ('SOURCE_QUOTATION_ONLY' if support else 'UNKNOWN')),
                'empirical_probability':None,'certification_eligible':False,
                'revision':_hash(_dump([claim, sorted(families), sorted(conflict_families),sorted(authenticated_support)]))})
        return {'schema':'qc.research_feedback.v1','verifier_version':'exact-source-quotation.v1',
            'project_id':self.project_id,'research_run_id':self.run_id,
            'claims':results,'requirements':self.items('requirement'),
            'certification_eligible':False,
            'limits':['Exact whole-line quotation support only; no paraphrase or legal applicability proof.',
                'Counterstatements are explicit propositions to investigate, not model-generated verdicts.',
                'Registry metadata is not authenticated retrieval; native receipts require controller revalidation.',
                'Authenticated retrieval is not factual entailment; publisher independence is unverified.']}

    def propose_paths(self, max_paths=3, max_cycles=3):
        if type(max_paths) is not int or type(max_cycles) is not int or not 0<=max_paths<=20 or not 0<=max_cycles<=20:
            raise ValueError('Invalid bounded feedback budget')
        assessment=self.assess()
        if self.items('deliverable_revision'):
            from app.services.research.claim_contracts import ClaimDocumentReview
            reviewer=ClaimDocumentReview(self)
            assessment['deliverable_assessments']=[reviewer.evaluate(r['id']) for r in self.items('deliverable_revision')]
        self._append('assessment',_hash(_dump(assessment)),assessment)
        if max_paths==0:
            return {'assessment':assessment,'paths':[],'stop_reason':'PATH_BUDGET_EXHAUSTED'}
        cycle_count=len(self.items('cycle'))
        if cycle_count>=max_cycles:
            return {'assessment':assessment,'paths':[],'stop_reason':'CYCLE_BUDGET_EXHAUSTED'}
        existing={p['id'] for p in self.items('path')}
        requirements={r['id']:r for r in assessment['requirements']}
        candidates=[]; linked=set()
        for claim in assessment['claims']:
            linked.update(claim['requirement_ids'])
            for rid in claim['requirement_ids']:
                req=requirements[rid]
                reviewed=any(review['deliverable_key']==req['deliverable_key'] and review['coverage_complete']
                    and any(o['claim_id']==claim['id'] for o in review['occurrences'])
                    for review in assessment.get('deliverable_assessments',[]))
                reason=claim['status']
                if claim['status']=='SUPPORTED_QUOTATION':
                    reason='CLAIM_APPLICABILITY_REQUIRED' if claim['authenticated_content_count'] else 'AUTHENTICATED_RETRIEVAL_REQUIRED'
                candidate={'requirement_id':rid,'deliverable_key':req['deliverable_key'],
                    'claim_id':claim['id'],'reason':reason,'revision':claim['revision'],
                    'question':req['question'],'assertion_to_check':claim['statement'],
                    'counterstatement':claim['counterstatement']}
                if reviewed:
                    candidate['reason']='CERTIFICATION_GATE_REMAINS'
                    candidate['_action']='review_deliverable_certification'
                candidates.append(candidate)
        for rid,req in requirements.items():
            if rid not in linked:
                candidates.append({'requirement_id':rid,'deliverable_key':req['deliverable_key'],
                    'claim_id':None,'reason':'NO_CLAIM','revision':_hash(_dump(req)),
                    'question':req['question']})
        for review in assessment.get('deliverable_assessments',[]):
            if review['coverage_complete']: continue
            for rid,req in requirements.items():
                if req['deliverable_key']==review['deliverable_key']:
                    candidates.append({'requirement_id':rid,'deliverable_key':req['deliverable_key'],
                        'claim_id':None,'reason':'REVALIDATE_DELIVERABLE','revision':review['id'],
                        'question':req['question'],'artifact_id':review['artifact_id'],
                        'artifact_version':review['artifact_version'],'_action':'revalidate_deliverable'})
        candidates.sort(key=lambda p:(p['reason']!='CONFLICT',p['requirement_id'],p.get('claim_id') or ''))
        paths=[]
        for candidate in candidates:
            action=candidate.pop('_action','research_evidence_gap')
            pid=_hash(_dump(candidate))
            if pid in existing or len(paths)>=max_paths: continue
            path={**candidate,'id':pid,'status':'PROPOSED','action':action,
                'requires_native_dispatch':True,'grants_execution_authority':False,
                'parent_research_run_id':self.run_id}
            self._append('path',pid,path); paths.append(path)
        if paths:
            self._append('cycle',str(cycle_count+1),{'number':cycle_count+1,'path_ids':[p['id'] for p in paths]})
        return {'assessment':assessment,'paths':paths,'stop_reason':
            'PATHS_PROPOSED' if paths else ('NO_NEW_PATHS' if candidates else 'NO_UNRESOLVED_REGISTERED_CLAIMS')}
