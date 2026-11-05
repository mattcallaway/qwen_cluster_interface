"""
Live Multi-Node Pipeline Benchmark Runner
Runs live activation passing trials over Tailscale:
- Trial 1: Desktop (Stage 0) -> qwen-laptop (Stage 1)
- Trial 2: qwen-laptop (Stage 0) -> qwen-vps (Stage 1)
- Trial 3: Desktop (Stage 0) -> qwen-vps (Stage 1)
"""
import sys
import os
import time
import socket
import struct
import json
import statistics
import paramiko
from typing import List, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_trial_harness import (
    encode_activation_frame,
    simulate_stage_compute,
)

SECRET = b"qc-live-trial-key-tailscale-inc17"

def run_remote_server(ssh_client, port: int, tokens: int) -> paramiko.Channel:
    ssh_client.exec_command("pkill -f pipeline_trial_harness")
    time.sleep(0.5)
    cmd = f"python3 /tmp/pipeline_trial_harness.py server {port} {tokens} {SECRET.decode('utf-8')}"
    stdin, stdout, stderr = ssh_client.exec_command(cmd)
    # Wait for ready signal
    while True:
        line = stdout.readline()
        if not line:
            err = stderr.read().decode()
            raise RuntimeError(f"Server failed to start on port {port}: {err}")
        if "PIPELINE_SERVER_READY" in line:
            break
    return stdout, stderr

def benchmark_stage_link(
    target_ip: str,
    target_port: int,
    hidden_dim: int,
    dtype: str,
    num_tokens: int = 30
) -> Dict[str, Any]:
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.settimeout(10.0)
    client.connect((target_ip, target_port))

    latencies_ms = []
    encode_times_ms = []
    remote_decodes_ms = []
    remote_computes_ms = []
    net_rtts_ms = []
    total_bytes_sent = 0

    session_id = f"live-trial-{hidden_dim}-{dtype}"

    try:
        for i in range(num_tokens):
            # Stage 0: initial vector + 14 layers
            raw_vec = [0.05 * (j % 100) for j in range(hidden_dim)]
            stage0_out = simulate_stage_compute(raw_vec, 0, 14)

            # Measure frame serialization
            t_enc_0 = time.perf_counter()
            frame = encode_activation_frame(session_id, 0, i, stage0_out, SECRET, dtype)
            t_enc = (time.perf_counter() - t_enc_0) * 1000.0
            encode_times_ms.append(t_enc)
            total_bytes_sent += len(frame)

            # Measure network send + remote response
            t_net_0 = time.perf_counter()
            client.sendall(struct.pack("!I", len(frame)) + frame)
            raw_len = client.recv(4)
            resp_len = struct.unpack("!I", raw_len)[0]
            resp_data = client.recv(resp_len)
            total_step_time = (time.perf_counter() - t_net_0) * 1000.0

            resp = json.loads(resp_data.decode("utf-8"))
            latencies_ms.append(total_step_time)
            remote_decodes_ms.append(resp.get("t_decode_ms", 0.0))
            remote_computes_ms.append(resp.get("t_compute_ms", 0.0))

            # Net RTT = total step - remote processing
            remote_total = resp.get("t_decode_ms", 0.0) + resp.get("t_compute_ms", 0.0)
            net_rtt = max(0.01, total_step_time - remote_total)
            net_rtts_ms.append(net_rtt)

    finally:
        client.close()

    latencies_ms.sort()
    net_rtts_ms.sort()

    def p(arr, pct):
        idx = int(len(arr) * pct / 100)
        return arr[min(idx, len(arr) - 1)]

    tokens_per_sec = 1000.0 / statistics.mean(latencies_ms) if latencies_ms else 0.0

    return {
        "hidden_dim": hidden_dim,
        "dtype": dtype,
        "frame_size_bytes": len(frame),
        "tokens_evaluated": num_tokens,
        "total_bytes_transferred": total_bytes_sent,
        "bandwidth_kbps": round((total_bytes_sent / 1024.0) / (sum(latencies_ms) / 1000.0), 2),
        "mean_encode_ms": round(statistics.mean(encode_times_ms), 3),
        "mean_remote_decode_ms": round(statistics.mean(remote_decodes_ms), 3),
        "mean_remote_compute_ms": round(statistics.mean(remote_computes_ms), 3),
        "net_rtt_mean_ms": round(statistics.mean(net_rtts_ms), 3),
        "net_rtt_min_ms": round(min(net_rtts_ms), 3),
        "net_rtt_p50_ms": round(p(net_rtts_ms, 50), 3),
        "net_rtt_p95_ms": round(p(net_rtts_ms, 95), 3),
        "total_step_mean_ms": round(statistics.mean(latencies_ms), 3),
        "total_step_p50_ms": round(p(latencies_ms, 50), 3),
        "total_step_p95_ms": round(p(latencies_ms, 95), 3),
        "tokens_per_sec": round(tokens_per_sec, 2),
    }

def main():
    print("=== LIVE MULTI-NODE PIPELINE PARALLELISM TAILSCALE BENCHMARK ===")
    
    with open("scratch/pipeline_trial_harness.py", "r", encoding="utf-8") as f:
        harness_code = f.read()

    # 1. Connect to Laptop
    print("\n--- Connecting to qwen-laptop (100.101.187.115) ---")
    ssh_lap = paramiko.SSHClient()
    ssh_lap.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh_lap.connect("100.101.187.115", username="emceemcee", timeout=5)
    sftp_lap = ssh_lap.open_sftp()
    with sftp_lap.file("/tmp/pipeline_trial_harness.py", "w") as f:
        f.write(harness_code)
    sftp_lap.close()

    # 2. Connect to VPS
    print("--- Connecting to qwen-vps (100.71.194.68) ---")
    ssh_vps = paramiko.SSHClient()
    ssh_vps.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh_vps.connect("100.71.194.68", username="root", timeout=5)
    sftp_vps = ssh_vps.open_sftp()
    with sftp_vps.file("/tmp/pipeline_trial_harness.py", "w") as f:
        f.write(harness_code)
    sftp_vps.close()

    results = {}

    # TEST A: Desktop (Stage 0) -> qwen-laptop (Stage 1)
    print("\n[TRIAL A] Desktop (100.122.228.19: Stage 0) -> qwen-laptop (100.101.187.115:9190: Stage 1)")
    lap_out, lap_err = run_remote_server(ssh_lap, port=9190, tokens=90)
    print("Remote server active on qwen-laptop:9190")

    bench_a_1 = benchmark_stage_link("100.101.187.115", 9190, hidden_dim=1536, dtype="float32", num_tokens=30)
    print("  Dim 1536 (float32):", json.dumps(bench_a_1))
    bench_a_2 = benchmark_stage_link("100.101.187.115", 9190, hidden_dim=3584, dtype="float16", num_tokens=30)
    print("  Dim 3584 (float16):", json.dumps(bench_a_2))
    bench_a_3 = benchmark_stage_link("100.101.187.115", 9190, hidden_dim=4096, dtype="float16", num_tokens=30)
    print("  Dim 4096 (float16):", json.dumps(bench_a_3))

    results["desktop_to_laptop"] = [bench_a_1, bench_a_2, bench_a_3]

    # TEST B: Desktop (Stage 0) -> qwen-vps (Stage 1)
    print("\n[TRIAL B] Desktop (100.122.228.19: Stage 0) -> qwen-vps (100.71.194.68:9192: Stage 1)")
    vps_out, vps_err = run_remote_server(ssh_vps, port=9192, tokens=90)
    print("Remote server active on qwen-vps:9192")

    bench_b_1 = benchmark_stage_link("100.71.194.68", 9192, hidden_dim=1536, dtype="float32", num_tokens=30)
    print("  Dim 1536 (float32):", json.dumps(bench_b_1))
    bench_b_2 = benchmark_stage_link("100.71.194.68", 9192, hidden_dim=3584, dtype="float16", num_tokens=30)
    print("  Dim 3584 (float16):", json.dumps(bench_b_2))
    bench_b_3 = benchmark_stage_link("100.71.194.68", 9192, hidden_dim=4096, dtype="float16", num_tokens=30)
    print("  Dim 4096 (float16):", json.dumps(bench_b_3))

    results["desktop_to_vps"] = [bench_b_1, bench_b_2, bench_b_3]

    # TEST C: qwen-laptop (Stage 0) -> qwen-vps (Stage 1)
    print("\n[TRIAL C] qwen-laptop (Stage 0) -> qwen-vps (100.71.194.68:9194: Stage 1)")
    vps_out2, vps_err2 = run_remote_server(ssh_vps, port=9194, tokens=30)
    print("Remote server active on qwen-vps:9194")

    # Write client script to laptop
    client_code = """
import socket, struct, time, json
import sys
sys.path.insert(0, '/tmp')
from pipeline_trial_harness import encode_activation_frame, simulate_stage_compute

c = socket.socket()
c.connect(('100.71.194.68', 9194))
times = []
raw = [0.05 * (j % 100) for j in range(3584)]
s0 = simulate_stage_compute(raw, 0, 14)
frame = encode_activation_frame('sess', 0, 0, s0, b'qc-live-trial-key-tailscale-inc17', 'float16')

for i in range(25):
    t0 = time.perf_counter()
    c.sendall(struct.pack('!I', len(frame)) + frame)
    raw_l = c.recv(4)
    if not raw_l: break
    l = struct.unpack('!I', raw_l)[0]
    r = c.recv(l)
    times.append((time.perf_counter() - t0) * 1000.0)

c.close()
times.sort()
p50 = times[len(times)//2]
print(json.dumps({
    'hidden_dim': 3584,
    'dtype': 'float16',
    'frame_size_bytes': len(frame),
    'tokens_evaluated': len(times),
    'total_step_mean_ms': round(sum(times)/len(times), 3),
    'total_step_min_ms': round(min(times), 3),
    'total_step_p50_ms': round(p50, 3),
    'total_step_max_ms': round(max(times), 3),
    'tokens_per_sec': round(1000.0 / (sum(times)/len(times)), 2)
}))
"""
    sftp_lap = ssh_lap.open_sftp()
    with sftp_lap.file("/tmp/pipeline_client.py", "w") as f:
        f.write(client_code)
    sftp_lap.close()

    stdin, stdout, stderr = ssh_lap.exec_command("python3 /tmp/pipeline_client.py")
    lap_res = stdout.read().decode().strip()
    lap_err_str = stderr.read().decode().strip()
    if lap_err_str:
        print("  Laptop stderr:", lap_err_str)
    print("  Laptop -> VPS result:", lap_res)
    results["laptop_to_vps"] = json.loads(lap_res) if lap_res.startswith("{") else lap_res

    # Cleanup remote files
    ssh_lap.exec_command("rm -f /tmp/pipeline_trial_harness.py /tmp/pipeline_client.py")
    ssh_vps.exec_command("rm -f /tmp/pipeline_trial_harness.py")
    ssh_lap.close()
    ssh_vps.close()

    # Save benchmark artifact
    os.makedirs("docs/verified-work", exist_ok=True)
    with open("docs/verified-work/INCREMENT_17_LIVE_PIPELINE_BENCHMARK.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("\n=== BENCHMARK COMPLETE ===")
    print("Results saved to docs/verified-work/INCREMENT_17_LIVE_PIPELINE_BENCHMARK.json")

if __name__ == "__main__":
    main()
