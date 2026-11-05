"""Fixed external oracles over raw live observations, independent of candidate imports.

This does not authenticate HMAC receipts: the controller does that in the live
tests. It independently checks the known fixture outcomes and host cleanup.
"""
import json
from pathlib import Path
root=Path(__file__).resolve().parent.parent
out=root/'outputs'; batch=(out/'laptop-verifier-latest-batch.txt').read_text().strip()
reports=[]
for path in sorted(out.glob('laptop-verifier-*.json')):
    value=json.loads(path.read_text())
    if value.get('batch_id')==batch: reports.append((path.name,value))
checks=[]
def check(name,passed): checks.append({'name':name,'passed':bool(passed)})
check('exact six final controller API scenarios',len(reports)==6)
expected=[5,-5,0]; counts={'correct':0,'wrong':0,'forged':0,'timeout':0,'output_limit':0};total=0
for name,report in reports:
    obs=report['receipt']['body']['observations'];total+=len(obs)
    check(name+': exact case inventory',[o['case_id'] for o in obs]==['positive','negative','zero'])
    check(name+': signed binding matches native records',report['native_binding']==report['receipt']['body']['binding'])
    check(name+': all process groups empty',len(report['units'])==3 and all(u['empty'] and u['safe'] and u['main_pid']=='0' for u in report['units']))
    check(name+': completed root nonce cannot replay',report.get('root_nonce_replay_denied') is True)
    check(name+': temporary policy absent',report['policy_absent'])
    check(name+': existing service PIDs/status unchanged',report['services_before']==report['services_after'] and report['existing_services_unchanged'])
    check(name+': all cleanup confirmed',all(o['cleanup_confirmed'] is True for o in obs))
    check(name+': no certification authority',report['accepted'] is False and report['certification_eligible'] is False and report['production_approved'] is False)
    reasons=[o['reason'] for o in obs]
    answers=[]
    for observation in obs:
        try: answers.append(json.loads(observation['stdout']))
        except ValueError: answers.append(None)
    if reasons==['completed']*3:
        check(name+': successful exit is distinct from oracle answer',all(o['executed'] and o['exit_code']==0 for o in obs))
        if answers==[{'value':v} for v in expected]: counts['correct']+=1
        elif answers==[{'value':999}]*3: counts['wrong']+=1
        elif answers==[{'accepted':True,'behavior_verified':True}]*3: counts['forged']+=1
        else: check(name+': recognized reviewed fixture output',False)
    elif reasons==['timeout']*3: counts['timeout']+=1
    elif reasons==['output_limit']*3: counts['output_limit']+=1
    else: check(name+': recognized failure reason',False)
check('two successful code/probe scenarios plus four failing scenarios',counts=={'correct':2,'wrong':1,'forged':1,'timeout':1,'output_limit':1})
check('eighteen actual confined executions',total==18)
result={'batch_id':batch,'evidence_class':'fixed independent fixture oracles; not live model dispatch or public-key certification',
    'scenario_counts':counts,'actual_executions':total,'checks':checks,'passed':all(c['passed'] for c in checks),
    'accepted':False,'certification_eligible':False,'production_approved':False}
(out/'laptop-verifier-independent-checks.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({'checks':len(checks),'passed':result['passed'],'actual_executions':total,'scenario_counts':counts}))
if not result['passed']:raise SystemExit(1)
