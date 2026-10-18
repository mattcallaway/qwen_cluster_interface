"""Test G: worker interruption/recovery for qwen-laptop (worker service only; no lease held; no inference active)."""
import subprocess, sys, time, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accept as A
from datetime import datetime, timezone

PY = sys.executable
S = os.path.dirname(os.path.abspath(__file__))
def ssh(script):
    p = os.path.join(S, "g_tmp.sh"); open(p, "w", newline="\n").write(script)
    return subprocess.run([PY, os.path.join(S, "rsh.py"), "100.101.187.115", "emceemcee", p, "60"], capture_output=True, text=True).stdout.strip()
def node():
    return A.q("select health_status,last_contact_at from infrastructure_nodes where id='qwen-laptop'")[0]
def age(ts): return round((datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds(), 1)
def stamp(): return datetime.now(timezone.utc).strftime("%H:%M:%S")

active = A.q("select id from worker_task_leases where worker_id='qwen-laptop' and status in ('leased','active')")
assert not active, f"laptop holds active lease: {active}"
print("pre-state:", node(), "age", age(node()["last_contact_at"]))
print("ollama loaded:", ssh("curl -s 127.0.0.1:11434/api/ps"))
t_stop = datetime.now(timezone.utc)
print(stamp(), "STOP:", ssh("sudo systemctl stop qwen-worker; systemctl is-active qwen-worker"))
item = A.mk_exec("G_LAPTOP_DURING_PAUSE", "qwen-laptop")
print("created pinned task during pause:", item["id"])
for i in range(11):
    time.sleep(10)
    n = node(); t, l = A.snap(item["id"])
    print(f"{stamp()} +{int((datetime.now(timezone.utc)-t_stop).total_seconds())}s health={n['health_status']} hb_age={age(n['last_contact_at'])}s task={t['status']} leases={len(l)}")
t_start = datetime.now(timezone.utc)
print(stamp(), "START:", ssh("sudo systemctl start qwen-worker; sleep 2; systemctl is-active qwen-worker"))
sts = A.wait([item["id"]], 90); print("statuses:", sts)
n = node(); print("post-state:", n, "age", age(n["last_contact_at"]))
t, leases = A.show(item)
print("lease count for task:", len(leases), "(must be exactly 1)")
print("recovery: start->lease s =", (datetime.fromisoformat(leases[0]["leased_at"]) - t_start).total_seconds() if leases else None)
print("worker journal tail:", ssh("journalctl -u qwen-worker --since '-3 min' --no-pager | grep -ciE 'error|traceback'"))
