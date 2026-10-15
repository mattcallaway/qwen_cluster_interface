"""Helper to push the updated executor to qwen-laptop and restart its worker service once online."""
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
    try:
        c.connect(LAPTOP_IP, 22, USER, key_filename=KEY_PATH, timeout=10)
    except Exception as e:
        print(f"FAILED to connect to laptop: {e}")
        sys.exit(1)

    print("Locating remote executor path...")
    _, out, _ = c.exec_command("find /home/emceemcee -name executor.py 2>/dev/null")
    paths = [p.strip() for p in out.read().decode().splitlines() if p.strip()]
    if not paths:
        print("Could not find executor.py in /home/emceemcee")
        sys.exit(1)
    remote_path = paths[0]
    print(f"Target remote path: {remote_path}")

    sftp = c.open_sftp()
    sftp.put(str(LOCAL_EXECUTOR), remote_path)
    sftp.close()
    print("Uploaded updated executor.py successfully.")

    print("Restarting worker service...")
    _, out, err = c.exec_command("sudo systemctl restart qwen-vps-agent.service 2>/dev/null || sudo systemctl restart qwen-worker.service 2>/dev/null")
    _, out, _ = c.exec_command("systemctl status qwen-vps-agent.service 2>/dev/null || systemctl status qwen-worker.service 2>/dev/null")
    print(out.read().decode("utf-8", errors="replace"))
    c.close()
    print("Done.")

if __name__ == "__main__":
    main()
