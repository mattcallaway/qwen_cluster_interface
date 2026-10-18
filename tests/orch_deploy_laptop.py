"""Deploy updated executor to qwen-laptop and restart qwen-worker.service."""
import sys
from pathlib import Path
import paramiko

LAPTOP_IP = "100.101.187.115"
USER = "emceemcee"
KEY_PATH = r"C:\Users\Matt C\.ssh\id_ed25519"
LOCAL_EXECUTOR = Path(r"C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective-dev\vps_agent\executor.py")

def main():
    print(f"Connecting to {LAPTOP_IP} as {USER}...")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(LAPTOP_IP, 22, USER, key_filename=KEY_PATH, timeout=10)

    # Put in /tmp first, then sudo mv to /opt/qwen-collective/source/vps_agent/executor.py
    sftp = c.open_sftp()
    sftp.put(str(LOCAL_EXECUTOR), "/tmp/executor.py")
    sftp.close()
    print("Uploaded executor to /tmp/executor.py")

    cmd = "sudo cp /tmp/executor.py /opt/qwen-collective/source/vps_agent/executor.py && sudo systemctl restart qwen-worker.service && systemctl status qwen-worker.service | head -n 8"
    _, out, err = c.exec_command(cmd)
    res = out.read().decode("utf-8", errors="replace")
    err_res = err.read().decode("utf-8", errors="replace")
    print(res)
    if err_res.strip():
        print("ERR:", err_res)
    c.close()
    print("Done.")

if __name__ == "__main__":
    main()
