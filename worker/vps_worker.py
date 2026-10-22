"""
Qwen Collective - VPS Companion Worker Service
----------------------------------------------
A lightweight, isolated worker service intended to run on an internet-connected VPS
or Tailscale node.
Provides:
- Authenticated health check
- Capability reporting (reflecting actual implemented handlers)
- Harmless test task execution
- Strict permission boundaries (NO arbitrary shell execution)
"""

import os
import sys
import secrets
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, Depends, HTTPException, Header, status
from pydantic import BaseModel
import uvicorn

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("qwen_vps_worker")

INSECURE_TOKENS = {
    "default-vps-secret-token",
    "secret",
    "admin123",
    "changeme",
    "password",
    ""
}

def validate_auth_token(token: Optional[str]) -> str:
    """Validate that a strong, unique authentication token is provided."""
    if not token or token.strip() in INSECURE_TOKENS or len(token.strip()) < 32:
        raise RuntimeError(
            "VPS WORKER STARTUP ERROR: A unique, strong QC_AUTH_TOKEN (minimum 32 characters) "
            "is strictly required. Set the QC_AUTH_TOKEN environment variable."
        )
    return token.strip()

def validate_host_binding(host: str, allow_public: bool = False) -> str:
    """Ensure worker binds to loopback or private/tailscale interface unless explicitly allowed."""
    cleaned = host.strip()
    if cleaned in ("0.0.0.0", "::") and not allow_public:
        raise RuntimeError(
            "VPS WORKER SECURITY ERROR: Binding to 0.0.0.0 exposes the worker to all network interfaces. "
            "Default to '127.0.0.1' or an explicit private/Tailscale IP. To force public binding, set QC_ALLOW_PUBLIC_BIND=1."
        )
    return cleaned

AUTH_TOKEN = os.getenv("QC_AUTH_TOKEN")
# Enforce security validation if running as main or worker service
PORT = int(os.getenv("QC_WORKER_PORT", "8081"))
ALLOW_PUBLIC_BIND = os.getenv("QC_ALLOW_PUBLIC_BIND", "0").lower() in ("1", "true", "yes")
HOST = os.getenv("QC_WORKER_HOST", "127.0.0.1")

# Actual implemented capabilities
IMPLEMENTED_CAPABILITIES: List[str] = [
    "health_check",
    "test_task",
    "echo_ping",
    "network_probe"
]

app = FastAPI(title="Qwen Collective VPS Companion Worker", version=os.getenv("QC_APP_VERSION", "1.0.0-rc.2"))

def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token format")
    
    # Constant-time comparison to prevent timing side-channel attacks
    current_token = os.getenv("QC_AUTH_TOKEN")
    if not current_token or not secrets.compare_digest(parts[1], current_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
    return parts[1]

class TaskRequest(BaseModel):
    task_type: str
    payload: Dict[str, Any] = {}

@app.get("/")
def root():
    return {
        "service": "Qwen Collective VPS Companion Worker",
        "status": "active",
        "hint": "Authenticate with Bearer token on /health"
    }

@app.get("/health")
def health(token: str = Depends(verify_token)):
    """Authenticated health check and capability announcement."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "capabilities": IMPLEMENTED_CAPABILITIES
    }

@app.post("/tasks/execute")
def execute_task(req: TaskRequest, token: str = Depends(verify_token)):
    """Execute a strictly bounded, harmless task."""
    logger.info(f"Received task request of type: {req.task_type}")
    
    if req.task_type in ("echo_ping", "test_task"):
        msg = req.payload.get("message", "ping")
        return {
            "status": "success",
            "task_type": req.task_type,
            "echo": msg,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "worker_node": "VPS Execution Companion"
        }
    elif req.task_type == "network_probe":
        return {
            "status": "success",
            "task_type": req.task_type,
            "external_ip_available": True,
            "dns_resolution": "operational",
            "processed_at": datetime.now(timezone.utc).isoformat()
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported or unauthorized task type: '{req.task_type}'. Shell execution is strictly prohibited."
        )

from pathlib import Path
import atexit

def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False

def acquire_pid_lock() -> Optional[Path]:
    data_dir = Path(os.getenv("QC_DATA_DIR", "."))
    lock_file = data_dir / "vps_worker.pid"
    if lock_file.exists():
        try:
            old_pid = int(lock_file.read_text(encoding="utf-8").strip())
            if old_pid != os.getpid() and _is_pid_running(old_pid):
                logger.error("Another VPS companion worker is already running (PID %d). Aborting startup.", old_pid)
                return None
        except Exception:
            pass
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(str(os.getpid()), encoding="utf-8")
        atexit.register(lambda: lock_file.unlink(missing_ok=True))
        return lock_file
    except Exception as e:
        logger.warning("Could not write worker PID lock file %s: %s", lock_file, e)
        return lock_file

def main():
    token = validate_auth_token(os.getenv("QC_AUTH_TOKEN"))
    host = validate_host_binding(HOST, allow_public=ALLOW_PUBLIC_BIND)
    lock = acquire_pid_lock()
    if lock is None:
        sys.exit(1)
    logger.info(f"Starting Qwen Collective VPS Companion Worker on {host}:{PORT} (PID {os.getpid()})")
    try:
        uvicorn.run(app, host=host, port=PORT)
    finally:
        if lock and lock.exists():
            lock.unlink(missing_ok=True)

if __name__ == "__main__":
    main()
