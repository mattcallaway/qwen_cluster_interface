"""
Configuration for Qwen Collective VPS Worker Agent (Milestone 2).
Loads settings from environment variables or a local configuration file.
"""

import os
from pathlib import Path
from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    # Controller Connection
    controller_url: str = Field(default_factory=lambda: os.getenv("QC_CONTROLLER_URL", "http://127.0.0.1:8000"))
    worker_id: str = Field(default_factory=lambda: os.getenv("QC_WORKER_ID", "vps-companion-01"))
    worker_name: str = Field(default_factory=lambda: os.getenv("QC_WORKER_NAME", "VPS Operational Companion"))
    worker_role: str = Field(default_factory=lambda: os.getenv("QC_WORKER_ROLE", "vps_worker"))
    worker_token: str = Field(default_factory=lambda: os.getenv("QC_WORKER_TOKEN", ""))
    enrollment_token: str = Field(default_factory=lambda: os.getenv("QC_ENROLLMENT_TOKEN", ""))

    # Polling & Heartbeat Intervals (Seconds)
    poll_interval: int = Field(default_factory=lambda: int(os.getenv("QC_POLL_INTERVAL", "5")))
    heartbeat_interval: int = Field(default_factory=lambda: int(os.getenv("QC_HEARTBEAT_INTERVAL", "15")))
    lease_duration: int = Field(default_factory=lambda: int(os.getenv("QC_LEASE_DURATION", "300")))

    # Execution Bounds
    default_task_timeout: int = Field(default_factory=lambda: int(os.getenv("QC_TASK_TIMEOUT", "600")))
    max_concurrent_jobs: int = Field(default_factory=lambda: int(os.getenv("QC_MAX_CONCURRENT_JOBS", "1")))
    
    # Offline Persistence
    data_dir: Path = Field(default_factory=lambda: Path(os.getenv("QC_WORKER_DATA_DIR", "./worker_data")).resolve())

    @property
    def buffer_db_path(self) -> Path:
        return self.data_dir / "worker_buffer.db"

    @property
    def token_file_path(self) -> Path:
        custom_path = os.getenv("QC_WORKER_TOKEN_FILE")
        if custom_path:
            return Path(custom_path).resolve()
        return self.data_dir / "worker_token.json"


def save_worker_token(config: AgentConfig, token: str) -> Path:
    """
    Securely persist worker token to disk with restrictive 0600 permissions.
    Ensures parent directory has restrictive 0700 permissions.
    Never logs or leaks credentials.
    """
    import json
    token_path = config.token_file_path
    token_dir = token_path.parent
    token_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(token_dir, 0o700)
        except OSError:
            pass

    payload = {
        "worker_id": config.worker_id,
        "worker_token": token,
    }
    content = json.dumps(payload, indent=2)

    tmp_path = token_path.with_suffix(".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    mode = 0o600

    fd = os.open(str(tmp_path), flags, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

    if os.name != "nt":
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass

    tmp_path.replace(token_path)
    if os.name != "nt":
        try:
            os.chmod(token_path, 0o600)
        except OSError:
            pass

    return token_path


def load_config() -> AgentConfig:
    config = AgentConfig()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(config.data_dir, 0o700)
        except OSError:
            pass
    # If worker_token not in env, load from secure token persistence file
    if not config.worker_token and config.token_file_path.exists():
        try:
            import json
            data = json.loads(config.token_file_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("worker_token"):
                config.worker_token = data["worker_token"]
        except Exception:
            pass
    return config
