"""Controller audit (read-only). Prints evidence for baseline."""
import sqlite3, json, os, hashlib, shutil
from datetime import datetime, timezone
DB = r"C:\Users\Matt C\.qwen-collective\data\qwen_collective.db"
STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
BK = rf"C:\Users\Matt C\.qwen-collective\data\qwen_collective.pre-audit-{STAMP}.db"

# Online-backup API (safe while service is running)
src = sqlite3.connect(DB); dst = sqlite3.connect(BK); src.backup(dst); dst.close(); src.close()
h = hashlib.sha256(open(BK, "rb").read()).hexdigest()
b = sqlite3.connect(BK)
print("BACKUP", BK, os.path.getsize(BK), "bytes sha256", h)
print("BACKUP integrity_check:", b.execute("PRAGMA integrity_check").fetchone()[0])
print("BACKUP restorable table count:", b.execute("select count(*) from sqlite_master where type='table'").fetchone()[0])
b.close()

c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
print("user_version:", c.execute("PRAGMA user_version").fetchone()[0])
print("integrity_check:", c.execute("PRAGMA integrity_check").fetchone()[0])
print("foreign_key_check rows:", len(c.execute("PRAGMA foreign_key_check").fetchall()))
print("journal_mode:", c.execute("PRAGMA journal_mode").fetchone()[0])
now = datetime.now(timezone.utc)
print("NOW", now.isoformat())
print("\n-- infrastructure_nodes (heartbeat age) --")
for r in c.execute("select id,role,health_status,last_contact_at,available_models_json,is_enabled from infrastructure_nodes"):
    age = None
    if r["last_contact_at"]:
        age = round((now - datetime.fromisoformat(r["last_contact_at"])).total_seconds())
    print(r["id"], r["role"], r["health_status"], "age_s=", age, r["available_models_json"], "enabled", r["is_enabled"])
print("\n-- machines --")
for r in c.execute("select id,display_name,ip_address,connection_status,role,is_enabled from machines"): print(dict(r))
print("\n-- duplicate IPs in machines/infrastructure --")
print(c.execute("select ip_address,count(*) n from machines group by ip_address having n>1").fetchall())
print("\n-- tasks by status --")
for r in c.execute("select status,count(*) n from tasks group by status"): print(r["status"], r["n"])
print("-- unfinished/leasable tasks --")
for r in c.execute("select id,title,task_type,status,assigned_worker,approval_required from tasks where status in ('queued','pending','approved','in_progress')"): print(dict(r))
print("-- leases --")
for r in c.execute("select id,task_id,worker_id,status,lease_expires_at from worker_task_leases order by created_at desc limit 10"): print(dict(r))
print("-- worker_auth_tokens (no secrets) --")
cols = [x[1] for x in c.execute("pragma table_info(worker_auth_tokens)")]
print(cols)
for r in c.execute("select * from worker_auth_tokens"):
    d = dict(r)
    for k in list(d):
        if "hash" in k or "token" in k and k not in ("token_id",): d[k] = "<redacted>"
    print(d)
print("-- enrollment tokens --")
for r in c.execute("select id,allowed_node_id,status,expires_at,used_at from worker_enrollment_tokens"): print(dict(r))
print("-- inference_backends --")
for r in c.execute("select id,name,endpoint_url,status,model_name from inference_backends"): print(dict(r))
print("-- tables --"); print([r[0] for r in c.execute("select name from sqlite_master where type='table'")])
