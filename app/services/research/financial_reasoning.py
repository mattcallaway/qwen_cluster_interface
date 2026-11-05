"""Deterministic financial derivations from scoped, authenticated rule assertions.

No prose-to-rule inference or certification authority. Source authorship and the
truth of reported commitments remain separate verification responsibilities.
"""
from decimal import localcontext
from app.services.orchestration.financial_ledger import Cost, Fund, Allocation, reconcile
from app.services.research.feedback import _dump, _hash


def verify_financial_document(document, specification, claims, supported_claim_ids):
    from app.services.research.claim_contracts import strict_json
    errors=[]; steps=[]; reconciliation=None
    try:
        if (set(specification)!={'schema','total','costs','fund_claim_ids'}
                or specification['schema']!='qc.financial_spec.v1'):
            raise ValueError('Unsupported approved financial specification')
        ledger=document['financial_ledger']
        if set(ledger)!={'total','costs','fund_claim_ids','allocations','conclusions'}:
            raise ValueError('Uninventoried financial content')
        # Preserve the actual approved cost identities and amounts, not merely a sum.
        for key in ('total','costs','fund_claim_ids'):
            if ledger[key]!=specification[key]: errors.append('APPROVED_FINANCIAL_INPUT_CHANGED')
        if (not isinstance(ledger['costs'],list) or not 0<len(ledger['costs'])<=128
                or not isinstance(ledger['fund_claim_ids'],list) or not 0<len(ledger['fund_claim_ids'])<=32
                or len(set(ledger['fund_claim_ids']))!=len(ledger['fund_claim_ids'])
                or not isinstance(ledger['allocations'],list) or len(ledger['allocations'])>512):
            raise ValueError('Bounded unique financial inputs required')
        costs=[Cost(**c) for c in ledger['costs']]
        funds=[]
        for cid in ledger['fund_claim_ids']:
            if cid not in supported_claim_ids: raise ValueError('Applicable verified rule claim required')
            rule=strict_json(claims[cid]['statement'])
            fields={'schema','id','kind','status','amount','eligible_categories','match_rate',
                    'match_basis','accepted_match_kinds','category_caps'}
            if set(rule)!=fields or rule['schema']!='qc.funding_rule.v1':
                raise ValueError('Exact structured rule assertion required')
            for key in ('eligible_categories','accepted_match_kinds'):
                if (not isinstance(rule[key],list) or any(not isinstance(v,str) or not v for v in rule[key])
                        or len(set(rule[key]))!=len(rule[key])): raise ValueError('Invalid rule categories')
            if not set(rule['accepted_match_kinds'])<= {'cash','inkind'}: raise ValueError('Invalid match kinds')
            if (not isinstance(rule['category_caps'],list)
                    or any(not isinstance(p,list) or len(p)!=2 for p in rule['category_caps'])
                    or len({p[0] for p in rule['category_caps']})!=len(rule['category_caps'])):
                raise ValueError('Invalid category caps')
            values={k:v for k,v in rule.items() if k!='schema'}
            for key in ('eligible_categories','accepted_match_kinds'): values[key]=frozenset(values[key])
            values['category_caps']=tuple(tuple(p) for p in values['category_caps'])
            funds.append(Fund(**values,rule_evidence_id=cid))
            steps.append({'operation':'apply_scoped_funding_rule','claim_id':cid,
                          'statement_sha256':_hash(claims[cid]['statement']),'fund_id':rule['id']})
        # Bound decimal text before arithmetic; no floats, exponents or caller
        # decimal context may alter these cent-based derivations.
        import re
        monetary=[ledger['total']]+[c.amount for c in costs]+[f.amount for f in funds]
        allocations=[Allocation(**a) for a in ledger['allocations']]
        monetary += [a.amount for a in allocations]
        ratios=[f.match_rate for f in funds]+[p[1] for f in funds for p in f.category_caps]
        if any(not isinstance(v,str) or not re.fullmatch(r'\d{1,15}(?:\.\d{1,2})?',v) for v in monetary):
            raise ValueError('Bounded decimal monetary strings required')
        if any(not isinstance(v,str) or not re.fullmatch(r'\d{1,2}(?:\.\d{1,8})?',v) for v in ratios):
            raise ValueError('Bounded decimal ratio strings required')
        with localcontext() as ctx:
            ctx.prec=64
            reconciliation=reconcile(ledger['total'],tuple(costs),tuple(funds),tuple(allocations))
        steps.append({'operation':'decimal_reconciliation','input_sha256':_hash(_dump({
            k:v for k,v in ledger.items() if k!='conclusions'})),'result':reconciliation})
        expected={'balanced':reconciliation['valid'],'funding_gap':reconciliation['funding_gap'],
                  'match_deficits':reconciliation['match_deficits']}
        if not isinstance(ledger['conclusions'],dict) or type(ledger['conclusions'].get('balanced')) is not bool:
            raise ValueError('Typed financial conclusions required')
        if ledger['conclusions']!=expected: errors.append('DERIVED_CONCLUSION_MISMATCH')
        errors.extend('FINANCE_'+e.upper() for e in reconciliation['errors'])
    except (ValueError,TypeError,KeyError,ArithmeticError):
        errors.append('UNVERIFIABLE_FINANCIAL_REASONING')
    return {'schema':'qc.financial_reasoning.v1','verified':not errors,
            'errors':sorted(set(errors)),'steps':steps,'reconciliation':reconciliation,
            'certification_eligible':False,
            'limits':['Structured rule assertions only; no natural-language rule inference.',
                      'Authenticated retrieval does not establish source authority or commitment truth.']}
