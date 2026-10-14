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
    worker_role: str = Field(default_factory=lambda: os.getenv("QC_WORKER_ROLE", "vps_companion"))
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


def load_config() -> AgentConfig:
    config = AgentConfig()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    return config
