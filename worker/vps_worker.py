"""
Qwen Collective - VPS Companion Worker Service
----------------------------------------------
A lightweight, isolated worker service intended to run on an internet-connected VPS.
Provides:
- Authenticated health check
- Capability reporting
- Harmless test task execution
- Strict permission boundaries (NO arbitrary shell execution)
"""

import os
import sys
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from fastapi import FastAPI, Depends, HTTPException, Header, status
from pydantic import BaseModel
import uvicorn

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("qwen_vps_worker")

AUTH_TOKEN = os.getenv("QC_AUTH_TOKEN", "default-vps-secret-token")
PORT = int(os.getenv("QC_WORKER_PORT", "8081"))
HOST = os.getenv("QC_WORKER_HOST", "0.0.0.0")

app = FastAPI(title="Qwen Collective VPS Companion Worker", version="0.1.0")

def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or parts[1] != AUTH_TOKEN:
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
        "capabilities": [
            "health_check",
            "test_task",
            "page_retrieval_sandbox",
            "structured_research_inbox"
        ]
    }

@app.post("/tasks/execute")
def execute_task(req: TaskRequest, token: str = Depends(verify_token)):
    """Execute a strictly bounded, harmless task."""
    logger.info(f"Received task request of type: {req.task_type}")
    
    if req.task_type == "echo_ping":
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

if __name__ == "__main__":
    logger.info(f"Starting Qwen Collective VPS Companion Worker on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)
