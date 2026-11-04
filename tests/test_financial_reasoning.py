import copy
import json
import pytest
from app.services.research.financial_reasoning import verify_financial_document
from test_native_retrieval import native_fetch,URL
from test_claim_contracts import CONTRACT,card,prepare,refresh

RULE={'schema':'qc.funding_rule.v1','id':'cash','kind':'cash','status':'committed','amount':'100.00',
      'eligible_categories':['engineering'],'match_rate':'0','match_basis':'grant',
      'accepted_match_kinds':[],'category_caps':[]}
STATEMENT=json.dumps(RULE,sort_keys=True)
SPEC={'schema':'qc.financial_spec.v1','total':'100.00',
      'costs':[{'id':'engineering','category':'engineering','amount':'100.00'}],
      'fund_claim_ids':['financial-rule']}
FIN_CONTRACT={**CONTRACT,'financial_spec':SPEC}


def document():
    return {'schema':'qc.claim_document.v1','blocks':[
        {'kind':'claim','claim_id':'financial-rule','text':STATEMENT}],
        'financial_ledger':{k:copy.deepcopy(v) for k,v in SPEC.items() if k!='schema'} | {
            'allocations':[{'cost_id':'engineering','fund_id':'cash','amount':'100.00'}],
            'conclusions':{'balanced':True,'funding_gap':'0','match_deficits':{}}}}


def verify(doc=None,rule=None,supported=True):
    return verify_financial_document(doc or document(),SPEC,
        {'financial-rule':{'statement':json.dumps(rule or RULE,sort_keys=True)}},
        {'financial-rule'} if supported else set())


def test_exact_derived_result_has_trace_without_certification():
    result=verify()
    assert result['verified']
    assert result['steps'][0]['claim_id']=='financial-rule'
    assert result['steps'][1]['result']['valid']
    assert not result['certification_eligible']


@pytest.mark.parametrize('mutation,code',[
    ('rename_cost','APPROVED_FINANCIAL_INPUT_CHANGED'),
    ('invent_total','APPROVED_FINANCIAL_INPUT_CHANGED'),
    ('lie_about_gap','DERIVED_CONCLUSION_MISMATCH'),
    ('underallocate','FINANCE_FUNDING_GAP'),
    ('double_count','FINANCE_COST_OVERALLOCATED'),
    ('unknown_cost','UNVERIFIABLE_FINANCIAL_REASONING'),
    ('hidden_explanation','UNVERIFIABLE_FINANCIAL_REASONING'),
    ('float','UNVERIFIABLE_FINANCIAL_REASONING'),
    ('exponent','UNVERIFIABLE_FINANCIAL_REASONING'),
    ('huge','UNVERIFIABLE_FINANCIAL_REASONING')])
def test_adversarial_documents(mutation,code):
    doc=document(); ledger=doc['financial_ledger']
    if mutation=='rename_cost': ledger['costs'][0]['id']='Grant Name'
    if mutation=='invent_total': ledger['total']='200.00'
    if mutation=='lie_about_gap': ledger['conclusions']['funding_gap']='10.00'
    if mutation=='underallocate': ledger['allocations'][0]['amount']='90.00'
    if mutation=='double_count': ledger['allocations']*=2
    if mutation=='unknown_cost': ledger['allocations'][0]['cost_id']='invented'
    if mutation=='hidden_explanation': ledger['explanation']='Everything is eligible.'
    if mutation=='float': ledger['allocations'][0]['amount']=100.0
    if mutation=='exponent': ledger['allocations'][0]['amount']='1e2'
    if mutation=='huge': ledger['allocations'][0]['amount']='9'*500
    result=verify(doc)
    assert not result['verified'] and code in result['errors']


@pytest.mark.parametrize('change,code',[
    ({'eligible_categories':['planting']},'FINANCE_INELIGIBLE_EXPENDITURE'),
    ({'status':'proposed'},'FINANCE_UNCOMMITTED_FUNDING'),
    ({'amount':'90.00'},'FINANCE_FUND_OVERALLOCATED'),
    ({'kind':'grant','match_rate':'0.25','accepted_match_kinds':['cash']},'FINANCE_MATCH_DEFICIT'),
    ({'kind':'grant','category_caps':[['engineering','0.5']]},'FINANCE_CATEGORY_CAP_EXCEEDED')])
def test_balanced_arithmetic_does_not_prove_eligibility(change,code):
    result=verify(rule=RULE|change)
    assert not result['verified'] and code in result['errors']


def test_unsupported_source_cannot_supply_financial_rules():
    assert not verify(supported=False)['verified']


def test_calculation_ignores_callers_decimal_precision():
    from decimal import localcontext
    with localcontext() as ctx:
        ctx.prec=2
        assert verify()['verified']


def test_numeric_truth_flag_is_not_a_boolean_conclusion():
    doc=document(); doc['financial_ledger']['conclusions']['balanced']=1
    assert not verify(doc)['verified']


def test_small_cash_allocations_against_integer_cents_oracle():
    # Expected outcomes specified with integer arithmetic, independently of Decimal.
    for cents in range(0,121):
        doc=document(); ledger=doc['financial_ledger']
        ledger['allocations'][0]['amount']=f'{cents//100}.{cents%100:02d}'
        # Budget is $100; every small allocation leaves a known gap.
        result=verify(doc)
        from decimal import Decimal
        assert Decimal(result['reconciliation']['funding_gap'])==Decimal(10000-cents)/100
        assert not result['verified']


def test_total_cost_match_has_independently_calculated_ceiling():
    # $100 grant, 20% total-cost match -> $25; the deficit is not a funding gap.
    result=verify(rule=RULE|{'kind':'grant','match_rate':'0.2','match_basis':'total',
                             'accepted_match_kinds':['cash']})
    match=result['reconciliation']['derivations']['match']['cash']
    assert match['required']=='25.00' and match['deficit']=='25.00'
    assert result['reconciliation']['funding_gap']=='0'
    assert 'FINANCE_MATCH_DEFICIT' in result['errors']


@pytest.mark.parametrize('native_fetch',[{'source_text':card(STATEMENT),
                                        'claim_contract':FIN_CONTRACT}],indirect=True)
def test_native_evidence_to_derived_result_and_invalidation(native_fetch):
    native_fetch['ledger'].add_claim('financial-rule',STATEMENT,['match'])
    native_fetch['ledger'].conn.commit()
    reviewer,binding,_,_=prepare(native_fetch,doc=document())
    result=reviewer.evaluate(binding['id'])
    assert result['coverage_complete'] and result['financial_reasoning']['verified']
    assert not result['accepted'] and not result['requirement_entailment_verified']
    # An additional out-of-scope source must not displace valid evidence.
    refresh(native_fetch,card(STATEMENT,valid_until='2025-12-31'))
    # Old evidence is still applicable, so a second wrong-scope card does not
    # invalidate it. Changing the underlying artifact does invalidate the binding.
    assert reviewer.evaluate(binding['id'])['coverage_complete']
    native_fetch['ledger'].conn.execute('UPDATE artifacts SET current_version_number=current_version_number+1 WHERE id=?',
                                       (binding['artifact_id'],))
    result=reviewer.evaluate(binding['id'])
    assert not result['coverage_complete']
    assert native_fetch['ledger'].items('deliverable_invalidation')
