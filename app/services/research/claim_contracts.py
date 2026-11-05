"""Version-bound review of a deliberately narrow source-assertion format.

Mechanical applicability is not general factual/legal entailment. Arbitrary
Markdown, inferred scope, calculations and free-form claim extraction fail closed.
This module never grants certification or changes human approval records.
"""
from datetime import date,datetime,timezone
import json
from urllib.parse import urlparse
from app.services.research.feedback import _dump,_hash
from app.services.artifacts import artifact_service


def strict_json(text):
    def pairs(entries):
        result={}
        for key,value in entries:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    return json.loads(text,object_pairs_hook=pairs,parse_constant=lambda value:
        (_ for _ in ()).throw(ValueError('Nonfinite JSON value')))


def _day(value):
    parsed=date.fromisoformat(value)
    if parsed.isoformat()!=value: raise ValueError('Canonical ISO date required')
    return parsed


def _url(value):
    parsed=urlparse(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Exact HTTPS source URL required')
    return value


class ClaimDocumentReview:
    def __init__(self,ledger): self.ledger=ledger

    def _approved(self,key):
        c=self.ledger.conn
        row=c.execute('''SELECT rev.id,rev.plan_json,r.id FROM research_runs rr
            JOIN orchestration_runs r ON r.id=rr.orchestration_run_id AND r.project_id=rr.project_id
            JOIN orchestration_plan_revisions rev ON rev.id=r.current_revision_id
            WHERE rr.id=? AND rr.project_id=? AND rev.status='approved' ''',
            (self.ledger.run_id,self.ledger.project_id)).fetchone()
        if not row: raise ValueError('Current approved plan required')
        matches=[d for d in strict_json(row[1]).get('deliverables',[]) if d.get('key')==key]
        if len(matches)!=1: raise ValueError('Unique approved deliverable required')
        raw=matches[0].get('acceptance_criteria')
        contract=strict_json(raw) if isinstance(raw,str) else raw
        required={'schema','claim_mode','program','jurisdiction','as_of','required_requirement_ids',
            'allowed_source_urls','allowed_headings','max_snapshot_age_seconds'}
        if (not isinstance(contract,dict) or set(contract) not in (required,required|{'financial_spec'})
                or contract['schema']!='qc.claim_contract.v1' or contract['claim_mode']!='source_assertion'):
            raise ValueError('Unsupported claim contract; arbitrary facts/calculations require another verifier')
        if not all(isinstance(contract[k],str) and contract[k].strip() for k in ('program','jurisdiction')):
            raise ValueError('Explicit program/jurisdiction required')
        _day(contract['as_of'])
        for field in ('required_requirement_ids','allowed_source_urls','allowed_headings'):
            values=contract[field]
            if not isinstance(values,list) or not values or any(not isinstance(v,str) or not v.strip() for v in values):
                raise ValueError('Explicit nonempty contract lists required')
            if len(set(values))!=len(values): raise ValueError('Duplicate contract entries')
        for url in contract['allowed_source_urls']: _url(url)
        if not set(contract['allowed_headings']).issubset({'Scope','Evidence','Sources','Limitations','Claims','Requirements'}):
            raise ValueError('Headings must be nonassertive structural labels')
        age=contract['max_snapshot_age_seconds']
        if type(age) is not int or not 0<age<=2_592_000: raise ValueError('Explicit bounded freshness required')
        requirements={r['id']:r for r in self.ledger.items('requirement')}
        if any(r not in requirements or requirements[r]['deliverable_key']!=key
                for r in contract['required_requirement_ids']):
            raise ValueError('Contract requirement scope mismatch')
        return row[0],row[2],contract

    def bind(self,deliverable_key,artifact_id):
        plan_id,run_id,contract=self._approved(deliverable_key)
        artifact=self.ledger.conn.execute('SELECT * FROM artifacts WHERE id=?',(artifact_id,)).fetchone()
        if not artifact: raise ValueError('Canonical artifact required')
        artifact=dict(artifact)
        if artifact['origin_project_id']!=self.ledger.project_id or artifact['origin_run_id']!=run_id:
            raise ValueError('Artifact project/run mismatch')
        version=self.ledger.conn.execute('''SELECT * FROM artifact_versions
            WHERE artifact_id=? AND version_number=?''',(artifact_id,artifact['current_version_number'])).fetchone()
        if not version or version['checksum_sha256']!=artifact['checksum_sha256'] or version['producing_run_id']!=run_id:
            raise ValueError('Artifact version integrity mismatch')
        content=artifact_service.storage.read_cas_blob(version['cas_hash'])
        if len(content)>2_000_000 or _hash(content.decode('utf-8'))!=version['checksum_sha256']:
            raise ValueError('Canonical bytes do not match artifact version')
        payload={'deliverable_key':deliverable_key,'artifact_id':artifact_id,
            'artifact_version':artifact['current_version_number'],'artifact_sha256':artifact['checksum_sha256'],
            'cas_hash':version['cas_hash'],'plan_revision_id':plan_id,'contract':contract,
            'contract_sha256':_hash(_dump(contract)),'orchestration_run_id':run_id,
            'producer_task_id':version['producing_task_id'],'producer_worker_id':version['producing_worker_id'],
            'producer_attempt':version['producer_attempt']}
        payload['id']=_hash(_dump(payload))
        return self.ledger._append('deliverable_revision',payload['id'],payload)

    def _source(self,snapshot,contract):
        if snapshot['url'] not in contract['allowed_source_urls']: return 'UNAPPROVED_SOURCE_URL'
        if _hash(snapshot['text'])!=snapshot['sha256']: return 'SNAPSHOT_INTEGRITY_FAILURE'
        if self.ledger.receipt_verifier is None or not snapshot.get('retrieval_receipt_id'):
            return 'AUTHENTICATED_RETRIEVAL_REQUIRED'
        receipts={r['id']:r for r in self.ledger.items('retrieval_receipt')}
        receipt=receipts.get(snapshot['retrieval_receipt_id'])
        try:
            body=self.ledger.receipt_verifier.verify(self.ledger,receipt)
            if (body['extracted_text']!=snapshot['text'] or body['extracted_sha256']!=snapshot['sha256']
                    or body['final_url']!=snapshot['url'] or body['observed_at']!=snapshot['retrieved_at']):
                return 'RETRIEVAL_SNAPSHOT_MISMATCH'
        except Exception: return 'RETRIEVAL_ATTESTATION_UNVERIFIED'
        try:
            observed=datetime.fromisoformat(snapshot['retrieved_at'])
            age=(datetime.now(timezone.utc)-observed).total_seconds()
            if not -60<=age<=contract['max_snapshot_age_seconds']: return 'SOURCE_STALE_OR_FUTURE'
            lines=snapshot['text'].splitlines()
            if len(lines)!=2 or len(lines[0])>4000 or len(lines[1])>8000 or not lines[0].startswith('QC-Source-Scope: '): return 'UNSUPPORTED_SOURCE_CARD'
            scope=strict_json(lines[0][17:])
            if set(scope)!={'schema','program','jurisdiction','valid_from','valid_until'} or scope['schema']!='qc.source_scope.v1':
                return 'UNSUPPORTED_SOURCE_SCOPE'
            if scope['program']!=contract['program']: return 'PROGRAM_MISMATCH'
            if scope['jurisdiction']!=contract['jurisdiction']: return 'JURISDICTION_MISMATCH'
            lower,upper=_day(scope['valid_from']),_day(scope['valid_until'])
            if lower>upper or not lower<=_day(contract['as_of'])<=upper: return 'OUTSIDE_EFFECTIVE_PERIOD'
        except (ValueError,TypeError,KeyError): return 'UNVERIFIABLE_SOURCE_SCOPE'
        return None

    def evaluate(self,revision_id):
        revisions={r['id']:r for r in self.ledger.items('deliverable_revision')}
        if revision_id not in revisions: raise ValueError('Missing scoped deliverable binding')
        binding=revisions[revision_id]; errors=[]; occurrences=[]; relevant=[]; financial_review=None
        try:
            plan_id,run_id,contract=self._approved(binding['deliverable_key'])
            if plan_id!=binding['plan_revision_id'] or _hash(_dump(contract))!=binding['contract_sha256']:
                errors.append('CONTRACT_OR_PLAN_CHANGED')
            artifact=self.ledger.conn.execute('SELECT * FROM artifacts WHERE id=?',(binding['artifact_id'],)).fetchone()
            if (not artifact or artifact['origin_project_id']!=self.ledger.project_id or artifact['origin_run_id']!=run_id
                    or artifact['current_version_number']!=binding['artifact_version']
                    or artifact['checksum_sha256']!=binding['artifact_sha256']
                    or artifact['status'] not in ('available','accepted') or artifact['validation_status']!='valid'):
                errors.append('ARTIFACT_CHANGED_OR_UNAVAILABLE')
            version=self.ledger.conn.execute('SELECT * FROM artifact_versions WHERE artifact_id=? AND version_number=?',
                (binding['artifact_id'],binding['artifact_version'])).fetchone()
            if not version or any(version[field]!=binding[key] for field,key in (
                ('checksum_sha256','artifact_sha256'),('cas_hash','cas_hash'),('producing_run_id','orchestration_run_id'),
                ('producing_task_id','producer_task_id'),('producing_worker_id','producer_worker_id'),('producer_attempt','producer_attempt'))):
                errors.append('PRODUCER_OR_VERSION_BINDING_CHANGED')
            content=artifact_service.storage.read_cas_blob(binding['cas_hash'])
            if _hash(content.decode('utf-8'))!=binding['artifact_sha256']: raise ValueError('Artifact hash mismatch')
            document=strict_json(content.decode('utf-8'))
            expected_fields={'schema','blocks'}|({'financial_ledger'} if 'financial_spec' in contract else set())
            if not isinstance(document,dict) or set(document)!=expected_fields or document['schema']!='qc.claim_document.v1':
                raise ValueError('Unsupported document shape')
            if not isinstance(document['blocks'],list) or not 0<len(document['blocks'])<=256: raise ValueError('Invalid claim inventory size')
            claims={c['id']:c for c in self.ledger.items('claim')}
            covered=set()
            snapshots=self.ledger.items('snapshot')
            if len(snapshots)>32: raise ValueError('Source review size limit exceeded')
            source_checks=[(s,self._source(s,contract)) for s in snapshots]
            for index,block in enumerate(document['blocks']):
                if not isinstance(block,dict): raise ValueError('Invalid document block')
                if block.get('kind')=='heading':
                    if set(block)!={'kind','text'} or block['text'] not in contract['allowed_headings']:
                        raise ValueError('Unapproved structural text')
                    continue
                if set(block)!={'kind','claim_id','text'} or block.get('kind')!='claim': raise ValueError('Uninventoried prose')
                claim=claims.get(block['claim_id'])
                if not claim or block['text']!=claim['statement']: raise ValueError('Claim inventory/text mismatch')
                support=[]; contradictions=[]; issues=[]
                for snapshot,issue in source_checks:
                    matches=self.ledger._passages(snapshot,claim['statement'])
                    opposed=self.ledger._passages(snapshot,claim['counterstatement']) if claim['counterstatement'] else []
                    if matches or opposed:
                        relevant.append({'id':snapshot['id'],'sha256':snapshot['sha256'],'issue':issue})
                        if issue: issues.append(issue)
                        else:
                            support.extend(matches); contradictions.extend(opposed)
                required=set(claim['requirement_ids'])&set(contract['required_requirement_ids'])
                if not required: errors.append('CLAIM_OUTSIDE_REQUIREMENT_SCOPE')
                if contradictions: errors.append('APPLICABLE_EVIDENCE_CONFLICT')
                elif not support: errors.append('CLAIM_APPLICABILITY_UNVERIFIED')
                else: covered.update(required)
                occurrences.append({'pointer':f'/blocks/{index}/text','claim_id':claim['id'],
                    'text_sha256':_hash(block['text']),'support':support,'contradictions':contradictions,
                    'applicability_issues':sorted(set(issues)),
                    'status':'CONFLICT' if contradictions else 'APPLICABLE_SOURCE_ASSERTION' if support else 'UNVERIFIED'})
            if set(contract['required_requirement_ids'])-covered: errors.append('REQUIREMENT_COVERAGE_INCOMPLETE')
            if not occurrences: errors.append('CLAIM_INVENTORY_EMPTY')
            if 'financial_spec' in contract:
                from app.services.research.financial_reasoning import verify_financial_document
                supported={o['claim_id'] for o in occurrences if o['status']=='APPLICABLE_SOURCE_ASSERTION'}
                financial_review=verify_financial_document(document,contract['financial_spec'],claims,supported)
                errors.extend(financial_review['errors'])
        except (ValueError,TypeError,KeyError,UnicodeError,OSError):
            errors.append('UNVERIFIABLE_DOCUMENT_OR_BINDING')
        result={'schema':'qc.claim_document_review.v1','revision_id':revision_id,
            'deliverable_key':binding['deliverable_key'],'artifact_id':binding['artifact_id'],
            'artifact_version':binding['artifact_version'],'artifact_sha256':binding['artifact_sha256'],
            'plan_revision_id':binding['plan_revision_id'],'contract_sha256':binding['contract_sha256'],
            'occurrences':occurrences,'evidence_digest':_hash(_dump(sorted(relevant,key=lambda r:r['id']))),
            'coverage_complete':not errors,'errors':sorted(set(errors)),
            'requirement_entailment_verified':False,'financial_reasoning':financial_review,
            'accepted':False,'certification_eligible':False,'decision':'BLOCKED','fulfillment_status':'BLOCKED','fulfillment_score':0.0,
            'limits':['Applicability of explicit source assertions only; no general factual/legal entailment.',
                'No native producer/complete certification authority is granted by this review.']}
        result['id']=_hash(_dump(result))
        previous=[r for r in self.ledger.items('deliverable_assessment') if r['revision_id']==revision_id]
        self.ledger._append('deliverable_assessment',result['id'],result)
        if errors and any(r['coverage_complete'] for r in previous):
            notice={'revision_id':revision_id,'deliverable_key':binding['deliverable_key'],
                'assessment_id':result['id'],'reason_codes':result['errors'],'requires_revalidation':True}
            self.ledger._append('deliverable_invalidation',_hash(_dump(notice)),notice)
        return result
