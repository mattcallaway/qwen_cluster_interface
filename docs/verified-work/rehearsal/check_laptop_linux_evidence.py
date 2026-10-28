"""Independent fixed oracles over manual fixture observations, not certification."""
import hashlib,json
from pathlib import Path
base=Path('outputs')
names=['linux-candidate-inventory.json','laptop-linux-isolation-tests.json','laptop-linux-resource-tests.json','laptop-linux-final-state.json']
data={name:json.loads((base/name).read_bytes()) for name in names}
isolation=data[names[1]];resources=data[names[2]];final=data[names[3]]
cases={case['name']:case for case in isolation['tests']+resources['tests']}
checks=[]
def check(name,value):checks.append({'name':name,'passed':bool(value)})
def result(name):return cases[name]['result']
def kernel(name,key,value):return any(o.get('kernel_limits',{}).get(key)==value for o in result(name).get('observations',[]))
expected_image='43b057aa6a7133de2bbed57a8caaeecd07aea9228fa7cb6bec1608286b967b61'
check('pinned_image_consistent',isolation['image_sha256']==resources['image_sha256']==expected_image)
current=hashlib.sha256(Path('work/qwen-candidate/vps_agent/linux_sandbox.py').read_text().encode()).hexdigest()
check('tested_backend_matches_candidate',isolation['backend_source_sha256']==resources['backend_source_sha256']==current)
smoke=result('smoke');check('non_root_smoke',smoke['exit_code']==0 and smoke['cleanup_confirmed'] and json.loads(smoke['stdout'])['uid']>0)
denials=json.loads(result('isolation_denials')['stdout'])
required={'non_root','host_sentinel_hidden','credential_env_absent','network_socket_denied','source_read_only','cgroup_write_denied','scratch_limit'}
check('seven_isolation_denials',set(denials)==required and all(v is True for v in denials.values()))
check('deadline_stops_with_cleanup',result('deadline')['reason']=='timeout' and result('deadline')['cleanup_confirmed'])
flood=result('output_flood');check('stream_budget_enforced',flood['reason']=='output_limit' and flood['captured_bytes']==65536 and flood['output_bytes_seen']>65536 and flood['cleanup_confirmed'])
orphan=result('orphan');check('zero_exit_cannot_hide_descendant',orphan['exit_code']==0 and orphan['reason']=='orphaned_descendant' and orphan['cleanup_confirmed'])
memory=result('memory_limit');check('memory_cap_kernel_and_oom',kernel('memory_limit','memory.max','67108864') and any(o['manager']['service_result']=='oom-kill' for o in memory['observations']) and memory['cleanup_confirmed'])
pid=json.loads(result('pid_limit')['stdout']);check('pid_cap_kernel_and_denial',kernel('pid_limit','pids.max','8') and pid['pid_limit_denied'] is True and pid['spawned']==7 and result('pid_limit')['cleanup_confirmed'])
client=cases['client_loss'];check('service_deadline_survives_client_loss',client['observed_running_before_client_loss'] and client['manager_after_client_loss']['service_result']=='timeout' and client['cleanup_confirmed'] and client['service_ended_within_seconds']<6)
check('swap_and_cpu_policy_readback',kernel('smoke','memory.swap.max','0') and kernel('smoke','cpu.max','100000 100000'))
check('all_ten_test_cgroups_empty',len(final['units'])==10 and all(row['cgroup_absent_or_empty'] for row in final['units']))
check('temporary_permission_removed',isolation['host_policy_removed'] and resources['host_policy_removed'] and final['policy_absent'])
check('existing_services_not_restarted',isolation['existing_service_pids_unchanged'] and resources['existing_service_pids_unchanged'] and final['services_after']==resources['services_before'])
summary={'schema':'qc.manual_linux_evidence.v1','evidence_class':'MANUAL_REVIEWED_FIXTURES',
    'checks':checks,'all_checks_passed':all(c['passed'] for c in checks),
    'input_sha256':{name:hashlib.sha256((base/name).read_bytes()).hexdigest() for name in names},
    'backend_verified_for_generated_code':False,'certification_eligible':False,'production_approved':False,
    'limits':['Selected fixed fixtures on the laptop, not a native generated-code project.',
              'CPU quota readback is not a CPU stress benchmark; broad escape testing is incomplete.',
              'These file hashes are integrity checks, not independent cryptographic attestations.']}
(base/'laptop-linux-evidence-checks.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps({'passed':sum(c['passed'] for c in checks),'total':len(checks),'all_checks_passed':summary['all_checks_passed']}))
if not summary['all_checks_passed']:raise SystemExit(1)
