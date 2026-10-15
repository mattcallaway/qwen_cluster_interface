"""
Configuration for Qwen Collective VPS Worker Agent (Milestone 2).
Loads settings from environment variables or a local configuration file.
"""

import os
from pathlib import Path
from typing import Optional, Tuple
from pydantic import BaseModel, Field


def get_default_data_dir() -> Path:
    """
    Determine safe default data directory.
    - If QC_WORKER_DATA_DIR is set, use it.
    - If on Linux or /opt/qwen-collective exists, use /opt/qwen-collective/data.
    - Otherwise (Windows development), use ~/.qwen-collective/data.
    Never resolves inside the repository checkout by default.
    """
    env_val = os.getenv("QC_WORKER_DATA_DIR")
    if env_val:
        return Path(env_val).resolve()
    if os.name != "nt" or Path("/opt/qwen-collective/data").exists() or Path("/opt/qwen-collective").exists():
        return Path("/opt/qwen-collective/data").resolve()
    return (Path.home() / ".qwen-collective" / "data").resolve()


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
    data_dir: Path = Field(default_factory=get_default_data_dir)

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


def parse_env_file(path: Path) -> dict[str, str]:
    """
    Parse a standard KEY=VALUE environment file without external dependencies.
    Supports comments (#), empty lines, quoted values, and export prefixes.
    Does NOT modify os.environ automatically.
    """
    if not path or not path.exists():
        return {}

    env_vars: dict[str, str] = {}
    try:
        content = path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            if k:
                env_vars[k] = v
    except Exception:
        pass
    return env_vars


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


def validate_config(config: AgentConfig, allow_pending_credentials: bool = False) -> tuple[bool, Optional[str]]:
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

    # 4. Storage directory validation
    # Guard: Worker must never write runtime state inside the source repository
    resolved_data = config.data_dir.resolve()
    try:
        source_dir = Path(__file__).resolve().parent.parent
        if resolved_data == source_dir or source_dir in resolved_data.parents:
            return False, (
                f"Insecure data directory: QC_WORKER_DATA_DIR '{resolved_data}' "
                "cannot be inside the source repository."
            )
    except Exception:
        pass

    # 5. Credential presence check
    has_token = bool(config.worker_token) or config.token_file_path.exists()
    has_enrollment = bool(config.enrollment_token)
    if not has_token and not has_enrollment:
        if allow_pending_credentials:
            return True, None
        return False, (
            "No worker authentication token or enrollment token found. "
            "Please provide QC_WORKER_TOKEN, QC_ENROLLMENT_TOKEN, or place a valid "
            f"worker_token.json in '{config.token_file_path}'."
        )

    return True, None


def ensure_runtime_directories(config: AgentConfig) -> None:
    """
    Ensure required runtime directories exist with restrictive permissions.
    Called during daemon execution startup, NEVER during static configuration validation.
    """
    config.data_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(config.data_dir, 0o700)
        except OSError:
            pass


def load_config(config_file: Optional[Path] = None, override_env: bool = True) -> AgentConfig:
    """
    Load AgentConfig with deterministic precedence:
    1. Explicit config file (--config or QC_CONFIG_FILE or /etc/qwen-collective/worker.env)
    2. Process environment variables
    3. Production standard paths (/opt/qwen-collective/data)
    4. Secure token file (/opt/qwen-collective/data/worker_token.json)

    IMPORTANT: load_config() performs NO filesystem mutations (no mkdir/chmod).
    """
    target_env_file = config_file
    if target_env_file is None:
        custom_file = os.getenv("QC_CONFIG_FILE")
        if custom_file and Path(custom_file).exists():
            target_env_file = Path(custom_file)
        elif Path("/etc/qwen-collective/worker.env").exists():
            target_env_file = Path("/etc/qwen-collective/worker.env")

    file_vars: dict[str, str] = {}
    if target_env_file and target_env_file.exists():
        # 1. First parse using built-in zero-dependency parser
        file_vars = parse_env_file(target_env_file)
        # 2. Also attempt load_dotenv if available for advanced syntax
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path=target_env_file, override=override_env)
        except ImportError:
            pass
        # 3. Apply file_vars to environment if override requested
        for k, v in file_vars.items():
            if override_env or k not in os.environ:
                os.environ[k] = v

    # Resolve data_dir safely:
    # 1. Config file or env var QC_WORKER_DATA_DIR
    # 2. get_default_data_dir()
    data_dir_val = file_vars.get("QC_WORKER_DATA_DIR") or os.getenv("QC_WORKER_DATA_DIR")
    if data_dir_val:
        resolved_data_dir = Path(data_dir_val).resolve()
    else:
        resolved_data_dir = get_default_data_dir()

    config = AgentConfig(
        controller_url=file_vars.get("QC_CONTROLLER_URL") or os.getenv("QC_CONTROLLER_URL", "http://127.0.0.1:8000"),
        worker_id=file_vars.get("QC_WORKER_ID") or os.getenv("QC_WORKER_ID", "vps-companion-01"),
        worker_name=file_vars.get("QC_WORKER_NAME") or os.getenv("QC_WORKER_NAME", "VPS Operational Companion"),
        worker_role=file_vars.get("QC_WORKER_ROLE") or os.getenv("QC_WORKER_ROLE", "vps_worker"),
        worker_token=file_vars.get("QC_WORKER_TOKEN") or os.getenv("QC_WORKER_TOKEN", ""),
        enrollment_token=file_vars.get("QC_ENROLLMENT_TOKEN") or os.getenv("QC_ENROLLMENT_TOKEN", ""),
        poll_interval=int(file_vars.get("QC_POLL_INTERVAL") or os.getenv("QC_POLL_INTERVAL", "5")),
        heartbeat_interval=int(file_vars.get("QC_HEARTBEAT_INTERVAL") or os.getenv("QC_HEARTBEAT_INTERVAL", "15")),
        lease_duration=int(file_vars.get("QC_LEASE_DURATION") or os.getenv("QC_LEASE_DURATION", "300")),
        default_task_timeout=int(file_vars.get("QC_TASK_TIMEOUT") or os.getenv("QC_TASK_TIMEOUT", "600")),
        max_concurrent_jobs=int(file_vars.get("QC_MAX_CONCURRENT_JOBS") or os.getenv("QC_MAX_CONCURRENT_JOBS", "1")),
        data_dir=resolved_data_dir,
    )

    # If worker_token not in env or file, load from secure token persistence file
    if not config.worker_token and config.token_file_path.exists():
        try:
            import json
            data = json.loads(config.token_file_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("worker_token"):
                config.worker_token = data["worker_token"]
        except Exception:
            pass

    return config
