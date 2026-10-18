cat > /tmp/qc_bench.py <<'PYEOF'
import json, time, threading, subprocess, urllib.request, os, sys

OLLAMA = "http://127.0.0.1:11434"
def vm():
    d = {}
    for l in open("/proc/vmstat"):
        k, v = l.split()
        if k in ("pswpin", "pswpout", "pgmajfault"): d[k] = int(v)
    return d
def cpu():
    p = open("/proc/stat").readline().split()[1:]
    p = list(map(int, p)); return sum(p), p[3] + p[4]
def temp():
    t = []
    for i in range(10):
        try: t.append(int(open(f"/sys/class/thermal/thermal_zone{i}/temp").read()) / 1000)
        except Exception: pass
    return max(t) if t else None
def meminfo():
    m = {}
    for l in open("/proc/meminfo"):
        k, v = l.split(":"); m[k] = int(v.split()[0]) // 1024
    return m
def ollama_rss():
    out = subprocess.run("ps -eo rss,comm,args | grep -i '[o]llama' | awk '{s+=$1} END{print s/1024}'", shell=True, capture_output=True, text=True).stdout.strip()
    return float(out or 0)

class Sampler(threading.Thread):
    def __init__(self): super().__init__(daemon=True); self.stop = False; self.peak_rss = 0; self.peak_temp = 0; self.min_avail = 10**9; self.cpu_pct = []
    def run(self):
        t0, i0 = cpu()
        while not self.stop:
            self.peak_rss = max(self.peak_rss, ollama_rss())
            tp = temp(); self.peak_temp = max(self.peak_temp, tp or 0)
            self.min_avail = min(self.min_avail, meminfo()["MemAvailable"])
            time.sleep(0.4)
            t1, i1 = cpu()
            if t1 > t0: self.cpu_pct.append(100.0 * (1 - (i1 - i0) / (t1 - t0)))
            t0, i0 = t1, i1

def call(model, prompt, num_predict, keep="5m", opts=None):
    o = {"num_predict": num_predict, "temperature": 0}
    if opts: o.update(opts)
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "keep_alive": keep, "options": o}).encode()
    req = urllib.request.Request(OLLAMA + "/api/generate", body, {"Content-Type": "application/json"})
    v0 = vm(); s = Sampler(); s.start(); t = time.time()
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=300).read()); ok = True; err = None
    except Exception as e:
        r = {}; ok = False; err = str(e)
    wall = time.time() - t; s.stop = True; s.join(); v1 = vm()
    ev = r.get("eval_count", 0); ed = r.get("eval_duration", 1) or 1
    return {
        "ok": ok, "error": err, "model_returned": r.get("model"),
        "response": (r.get("response") or "")[:120],
        "wall_s": round(wall, 2),
        "load_s": round(r.get("load_duration", 0) / 1e9, 2),
        "prompt_tokens": r.get("prompt_eval_count"),
        "ttft_s_approx": round((r.get("load_duration", 0) + r.get("prompt_eval_duration", 0)) / 1e9, 2),
        "gen_tokens": ev, "tok_per_s": round(ev / (ed / 1e9), 2) if ev else 0,
        "peak_ollama_rss_mb": round(s.peak_rss), "min_mem_available_mb": s.min_avail,
        "swap_in_pages": v1["pswpin"] - v0["pswpin"], "swap_out_pages": v1["pswpout"] - v0["pswpout"],
        "major_faults": v1["pgmajfault"] - v0["pgmajfault"],
        "avg_cpu_pct": round(sum(s.cpu_pct) / len(s.cpu_pct), 1) if s.cpu_pct else None,
        "peak_temp_c": s.peak_temp,
    }

def unload_all():
    ps = json.loads(urllib.request.urlopen(OLLAMA + "/api/ps", timeout=10).read())
    for m in ps.get("models", []):
        call(m["name"], "", 1, keep="0")
    time.sleep(2)
def drop_caches():
    subprocess.run("sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null", shell=True)

SHORT = "Return exactly the word READY followed by the number 42."
MED = ("Summarize in three sentences: A distributed AI cluster contains a controller that schedules tasks, "
       "workers that lease tasks over authenticated HTTPS, and inference nodes that serve local language models. "
       "Workers send heartbeats every fifteen seconds, report their hardware and installed models, and execute "
       "approved tasks inside a sandbox. The controller must never assign a task to a worker that lacks the "
       "required model or that has stopped sending heartbeats, and results must be recorded exactly once.")
res = {"started_utc": time.strftime("%FT%TZ", time.gmtime()), "idle_mem_mb": meminfo(), "idle_temp_c": temp(), "runs": []}
for model in ("qwen2.5:1.5b", "llama3.2:1b"):
    unload_all(); drop_caches()
    res["runs"].append({"model": model, "case": "cold_short", **call(model, SHORT, 16)})
    res["runs"].append({"model": model, "case": "warm_short_1", **call(model, SHORT, 16)})
    res["runs"].append({"model": model, "case": "warm_short_2", **call(model, SHORT, 16)})
    res["runs"].append({"model": model, "case": "warm_medium", **call(model, MED, 96)})
# thread sweep (warm qwen)
unload_all(); call("qwen2.5:1.5b", "hi", 2)
for nt in (() if os.environ.get("QC_BENCH_QUICK") else (2, 3, 4)):
    res["runs"].append({"model": "qwen2.5:1.5b", "case": f"thread_sweep_num_thread={nt}", **call("qwen2.5:1.5b", MED, 64, opts={"num_thread": nt})})
unload_all()
res["after_mem_mb"] = meminfo()
print(json.dumps(res, indent=1))
PYEOF
python3 /tmp/qc_bench.py
