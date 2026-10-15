import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accept as A
from datetime import datetime, timezone
def age(ts): return round((datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds(), 1) if ts else None
print("== TEST A evidence", datetime.now(timezone.utc).isoformat())
for r in A.q("select * from infrastructure_nodes"):
    r["hb_age_s"] = age(r["last_contact_at"]); print(json.dumps(r)[:600])
print("tokens:")
for r in A.q("select * from worker_auth_tokens"):
    print({k: (v if k in ("id", "worker_id", "node_id", "created_at", "last_used_at", "revoked_at", "revoked") else "...") for k, v in r.items() if k not in ("token_hash",)})
print("== post-hardening laptop exec (C2)")
it = A.mk_exec("C2_LAPTOP_POST_HARDEN", "qwen-laptop"); A.wait([it["id"]], 90); A.show(it)
st = A.load(); st.setdefault("exec", []).append(it); A.save(st)
print("== queue/lease state")
print(A.q("select status,count(*) n from tasks group by status"))
print(A.q("select status,count(*) n from worker_task_leases group by status"))
print(A.q("select id,title,status,assigned_worker from tasks where title like 'QC_ACCEPTANCE_20261015_%'"))
