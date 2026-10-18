import requests, time, statistics, ssl, socket, json, subprocess, re

LOCAL = "http://127.0.0.1:8000"
TS = "https://desktop-44h6qvp.tailb5d90b.ts.net"

def lat(url, n=30, verify=True):
    ts, codes = [], set()
    for _ in range(n):
        t = time.perf_counter()
        try:
            r = requests.get(url, timeout=10, verify=verify, allow_redirects=False); codes.add(r.status_code)
        except Exception as e:
            codes.add(type(e).__name__)
        ts.append((time.perf_counter() - t) * 1000)
    ts.sort()
    return {"n": n, "p50_ms": round(statistics.median(ts), 1), "p95_ms": round(ts[int(0.95 * (n - 1))], 1), "max_ms": round(ts[-1], 1), "codes": sorted(map(str, codes))}

print("== latency ==")
print("local  /auth/login      ", lat(LOCAL + "/auth/login"))
print("local  /openapi.json    ", lat(LOCAL + "/openapi.json"))
print("tshttps /auth/login (cert verified)", lat(TS + "/auth/login"))

print("\n== TLS certificate (Tailscale HTTPS) ==")
ctx = ssl.create_default_context()
with socket.create_connection(("desktop-44h6qvp.tailb5d90b.ts.net", 443), timeout=10) as s:
    with ctx.wrap_socket(s, server_hostname="desktop-44h6qvp.tailb5d90b.ts.net") as ss:
        c = ss.getpeercert()
        print("verified OK; issuer:", dict(x[0] for x in c["issuer"]).get("organizationName"), "| notAfter:", c["notAfter"], "| proto:", ss.version())

print("\n== unauthenticated API access (expect 401/403/redirect, never 200 with data) ==")
paths = [("POST", "/api/v1/worker/heartbeat", {"node_id": "x"}), ("POST", "/api/v1/worker/tasks/lease", {"worker_id": "x"}),
         ("POST", "/api/v1/worker/register", {}), ("GET", "/api/v1/worker/enrollment-tokens", None),
         ("GET", "/infrastructure", None), ("GET", "/machines", None), ("GET", "/inference", None), ("GET", "/tasks", None)]
for m, p, body in paths:
    for base in (LOCAL, TS):
        r = requests.request(m, base + p, json=body, timeout=10, allow_redirects=False)
        print(f"{m:4} {base[:22]:22} {p:34} -> {r.status_code}  {r.headers.get('location','')}")
r = requests.post(LOCAL + "/api/v1/worker/heartbeat", json={"node_id": "qwen-laptop"}, headers={"Authorization": "Bearer bogus"}, timeout=10)
print("bogus bearer heartbeat ->", r.status_code)

print("\n== Windows listening sockets not bound to loopback ==")
out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
seen = set()
for l in out.splitlines():
    p = l.split()
    if len(p) >= 5 and p[3] == "LISTENING" and not p[1].startswith(("127.", "[::1]")):
        seen.add(p[1])
print(sorted(seen))
print("port 8000 listeners:", [l.split()[1] for l in out.splitlines() if ":8000 " in l and "LISTENING" in l])
