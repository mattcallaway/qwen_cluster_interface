"""Three-node acceptance driver. Creates ONLY tasks titled QC_ACCEPTANCE_20261015_*; never touches other rows.
Usage: python accept.py exec | inf | g
State kept in acc_state.json next to this file."""
import os, sys, json, time, sqlite3, hashlib, textwrap
from datetime import datetime, timezone

os.environ["QC_DATA_DIR"] = r"C:\Users\Matt C\.qwen-collective\data"
sys.path.insert(0, r"C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective-dev")
from app.services.tasks import create_task, cancel_task  # noqa: E402

DB = os.path.join(os.environ["QC_DATA_DIR"], "qwen_collective.db")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "acc_state.json")
PFX = "QC_ACCEPTANCE_20261015_"


def now(): return datetime.now(timezone.utc)
def q(sql, *a):
    c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row
    try: return [dict(r) for r in c.execute(sql, a)]
    finally: c.close()
def load(): return json.load(open(STATE)) if os.path.exists(STATE) else {}
def save(s): json.dump(s, open(STATE, "w"), indent=1)

def script(nonce):
    return textwrap.dedent(f'''
        import json, os, hashlib, datetime, socket
        total = sum(i*i for i in range(1, 1001))
        try: hid = hashlib.sha256(open("/etc/machine-id").read().strip().encode()).hexdigest()[:16]
        except Exception as e: hid = "unreadable:" + type(e).__name__
        out = {{"nonce": "{nonce}", "sum_squares_1_1000": total, "uid": os.getuid(), "host_id_sha16": hid,
               "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "cwd": os.getcwd()}}
        probes = {{}}
        for p in ("/etc/qc_acceptance_probe", "/root/qc_acceptance_probe"):
            try:
                open(p, "w").write("x"); probes[p] = "WRITTEN"
            except Exception as e:
                probes[p] = "BLOCKED:" + type(e).__name__
        try:
            s = socket.socket(); s.settimeout(2); s.connect(("1.1.1.1", 53)); probes["net"] = "CONNECTED"
        except Exception as e:
            probes["net"] = "BLOCKED:" + type(e).__name__
        out["probes"] = probes
        open("result.json", "w").write(json.dumps(out))
        print(json.dumps(out))
    ''').strip() + "\n"

def mk_exec(tag, worker, extra=None):
    nonce = hashlib.sha256(f"{tag}{time.time()}".encode()).hexdigest()[:12]
    inp = {"script": script(nonce), "label": PFX + tag}
    inp.update(extra or {})
    t = create_task(title=PFX + tag, description="Harmless deterministic acceptance task (sum of squares + isolation probes).",
                    task_type="python_execution", assigned_worker=worker, input_data=inp)
    return {"id": t["id"], "tag": tag, "pinned": worker, "nonce": nonce, "created_at": t["created_at"], "kind": "exec"}

def mk_inf(tag, worker, model, extra=None):
    inp = {"model": model, "prompt": "Return exactly the word READY followed by the number 42.", "num_predict": 16, "label": PFX + tag}
    inp.update(extra or {})
    t = create_task(title=PFX + tag, description="Acceptance inference request.", task_type="inference",
                    assigned_worker=worker, input_data=inp)
    return {"id": t["id"], "tag": tag, "pinned": worker, "model": model, "created_at": t["created_at"], "kind": "inf"}

def snap(tid):
    t = q("select id,status,assigned_worker,created_at,updated_at,result_data_json,attempt_count from tasks where id=?", tid)[0]
    leases = q("select id,worker_id,status,leased_at,lease_expires_at from worker_task_leases where task_id=? order by leased_at", tid)
    return t, leases

def wait(ids, timeout=180, stop=("completed", "failed", "cancelled")):
    t0 = time.time()
    while time.time() - t0 < timeout:
        sts = {i: snap(i)[0]["status"] for i in ids}
        if all(s in stop for s in sts.values()): return sts
        time.sleep(3)
    return {i: snap(i)[0]["status"] for i in ids}

def show(item):
    t, leases = snap(item["id"])
    print(f"\n--- {item['tag']}  {t['id']}  pinned={item['pinned']}  status={t['status']}  attempts={t['attempt_count']}")
    print("    created:", t["created_at"], " updated:", t["updated_at"])
    for l in leases:
        lat = (datetime.fromisoformat(l["leased_at"]) - datetime.fromisoformat(t["created_at"])).total_seconds()
        print(f"    lease {l['id']} worker={l['worker_id']} status={l['status']} leased_at={l['leased_at']} queue->lease={lat:.1f}s")
    r = t["result_data_json"] or ""
    print("    result:", r[:1800])
    return t, leases

if __name__ == "__main__":
    ph = sys.argv[1]; st = load()
    assert not q("select id from tasks where status in ('queued','pending','approved','in_progress') and title not like ?", PFX + "%"), "unexpected non-acceptance work in queue - abort"
    if ph == "exec":
        items = [mk_exec("B_VPS_EXEC", "qwen-vps"), mk_exec("C_LAPTOP_EXEC", "qwen-laptop"),
                 mk_exec("F4_UNPINNED_MIN_RAM_1500", None, {"min_ram_mb": 1500}),
                 mk_inf("F3_UNPINNED_MISSING_MODEL", None, "qwen2.5:7b", {"required_model": "qwen2.5:7b"})]
        st["exec"] = items; save(st)
        print("created", [(i["tag"], i["id"]) for i in items])
        sts = wait([i["id"] for i in items[:3]], 150)
        print("statuses after wait:", sts)
        time.sleep(20)  # give the missing-model task time to (not) be leased
        for i in items: show(i)
        f3 = items[3]["id"]; t3, l3 = snap(f3)
        if t3["status"] == "queued" and not l3: cancel_task(f3); print("\nF3 stayed queued with no lease for >=20s (correct) -> cancelled")
    elif ph == "inf":
        items = [mk_inf("D_QWEN_LAPTOP", "qwen-laptop", "qwen2.5:1.5b"), mk_inf("E_LLAMA_LAPTOP", "qwen-laptop", "llama3.2:1b")]
        st["inf"] = items; save(st)
        sts = wait([i["id"] for i in items], 120, stop=("completed", "failed", "cancelled", "queued_retry")); print("statuses:", sts)
        for i in items: show(i)
    elif ph == "show":
        for k in st:
            for i in st[k]: show(i)
