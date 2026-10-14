"""
Configuration for Qwen Collective VPS Worker Agent (Milestone 2).
Loads settings from environment variables or a local configuration file.
"""

import os
from pathlib import Path
from typing import Optional, Tuple
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


def scrub_enrollment_token(config_path: Optional[Path] = None) -> bool:
    """
    Remove or comment out QC_ENROLLMENT_TOKEN from the configuration file
    after successful registration so single-use credentials do not linger on disk.
    Preserves file permissions (0600 on Linux).
    """
    if config_path is None:
        custom_file = os.getenv("QC_CONFIG_FILE")
        if custom_file:
            config_path = Path(custom_file)
        elif Path("/etc/qwen-collective/worker.env").exists():
            config_path = Path("/etc/qwen-collective/worker.env")

    if not config_path or not config_path.exists():
        return False

    try:
        lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
        modified = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("QC_ENROLLMENT_TOKEN=") or stripped.startswith("export QC_ENROLLMENT_TOKEN="):
                new_lines.append("# QC_ENROLLMENT_TOKEN consumed and scrubbed after successful registration\n")
                modified = True
            else:
                new_lines.append(line)

        if modified:
            tmp_path = config_path.with_suffix(".tmp")
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            mode = 0o600
            fd = os.open(str(tmp_path), flags, mode)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
                f.flush()
                os.fsync(f.fileno())
            if os.name != "nt":
                try:
                    os.chmod(tmp_path, 0o600)
                except OSError:
                    pass
            tmp_path.replace(config_path)
            if os.name != "nt":
                try:
                    os.chmod(config_path, 0o600)
                except OSError:
                    pass
            return True
    except Exception:
        pass
    return False


def validate_config(config: AgentConfig) -> tuple[bool, Optional[str]]:
    """
    Validate agent configuration for production readiness.
    Returns (True, None) if valid, or (False, error_message) on failure.
    Never exposes token values in error messages.
    """
    # 1. Controller URL validation
    url = (config.controller_url or "").strip().rstrip("/")
    if not url:
        return False, "QC_CONTROLLER_URL is empty. A valid HTTP(S) URL is required."
    if not (url.startswith("http://") or url.startswith("https://")):
        return False, f"QC_CONTROLLER_URL '{url}' is invalid. Must begin with http:// or https://"

    # 2. Worker ID validation
    worker_id = (config.worker_id or "").strip()
    if not worker_id:
        return False, "QC_WORKER_ID is empty. A valid worker identifier is required."
    if any(c.isspace() for c in worker_id):
        return False, f"QC_WORKER_ID '{worker_id}' contains invalid whitespace characters."

    # 3. Intervals validation
    if config.poll_interval < 1:
        return False, f"QC_POLL_INTERVAL ({config.poll_interval}) must be at least 1 second."
    if config.heartbeat_interval < 1:
        return False, f"QC_HEARTBEAT_INTERVAL ({config.heartbeat_interval}) must be at least 1 second."
    if config.lease_duration < 10:
        return False, f"QC_LEASE_DURATION ({config.lease_duration}) must be at least 10 seconds."

    # 4. Credential presence check
    has_token = bool(config.worker_token) or config.token_file_path.exists()
    has_enrollment = bool(config.enrollment_token)
    if not has_token and not has_enrollment:
        return False, (
            "No worker authentication token or enrollment token found. "
            "Please provide QC_WORKER_TOKEN, QC_ENROLLMENT_TOKEN, or place a valid "
            f"worker_token.json in '{config.token_file_path}'."
        )

    return True, None


def load_config(config_file: Optional[Path] = None) -> AgentConfig:
    # 1. Load from explicit config file or well-known location if present
    target_env_file = config_file
    if target_env_file is None:
        custom_file = os.getenv("QC_CONFIG_FILE")
        if custom_file and Path(custom_file).exists():
            target_env_file = Path(custom_file)
        elif Path("/etc/qwen-collective/worker.env").exists():
            target_env_file = Path("/etc/qwen-collective/worker.env")

    if target_env_file and target_env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path=target_env_file)
        except ImportError:
            pass

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
