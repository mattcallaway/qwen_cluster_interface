"""Deploy vps_agent/scripts/service from the dev branch to a Linux worker node. Backs up the old source first.
Usage: deploy_worker.py <host> <user>"""
import os, sys, tarfile, tempfile, paramiko
DEV = r"C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective-dev"
host, user = sys.argv[1], sys.argv[2]
tar_path = os.path.join(tempfile.gettempdir(), "qwen_worker_deploy2.tar.gz")
skip = lambda ti: None if "__pycache__" in ti.name or ti.name.endswith(".pyc") else ti
with tarfile.open(tar_path, "w:gz") as t:
    for d in ("vps_agent", "scripts", "service"):
        if os.path.exists(os.path.join(DEV, d)): t.add(os.path.join(DEV, d), arcname=d, filter=skip)
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, 22, user, key_filename=r"C:\Users\Matt C\.ssh\id_ed25519")
s = c.open_sftp(); s.put(tar_path, "/tmp/qwen_worker_deploy2.tar.gz"); s.close()
cmd = r"""
set -e
TS=$(date -u +%Y%m%dT%H%M%SZ)
sudo mkdir -p /var/backups/qc-deploy-$TS
sudo tar -czf /var/backups/qc-deploy-$TS/source.before.tar.gz -C /opt/qwen-collective source
ACTIVE=$(sudo sqlite3 /dev/null 'select 1' 2>/dev/null; true)
sudo tar -xzf /tmp/qwen_worker_deploy2.tar.gz -C /opt/qwen-collective/source/
sudo chown -R root:root /opt/qwen-collective/source
sudo find /opt/qwen-collective/source -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
echo "runner sha: $(sha256sum /usr/local/bin/qwen-sandbox-runner | cut -c1-16)"
grep -c "_run_local_inference" /opt/qwen-collective/source/vps_agent/executor.py
sudo systemctl restart qwen-worker
sleep 10
systemctl is-active qwen-worker
journalctl -u qwen-worker --no-pager -n 3 | cut -c1-180
echo "rollback: sudo tar -xzf /var/backups/qc-deploy-$TS/source.before.tar.gz -C /opt/qwen-collective && sudo systemctl restart qwen-worker"
"""
i, o, e = c.exec_command(cmd); rc = o.channel.recv_exit_status()
print(o.read().decode("ascii", "replace")); print("rc", rc, e.read().decode("ascii", "replace")[-500:]); c.close()
