"""
System Observatory Data Models and Schemas.
Strict schemas for real telemetry samples, rollups, inference accounting, alerts, and analytics.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class GpuTelemetry(BaseModel):
    supported: bool = False
    name: Optional[str] = None
    utilization_percent: Optional[float] = None
    vram_total_mb: Optional[float] = None
    vram_used_mb: Optional[float] = None
    temperature_c: Optional[float] = None
    power_watts: Optional[float] = None
    model_residency: Optional[str] = None
    status_note: str = "Not supported"


class TelemetrySamplePayload(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=100)
    timestamp: Optional[str] = None
    cpu_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    per_core: List[float] = Field(default_factory=list)
    cpu_freq_mhz: Optional[float] = None
    load_1m: Optional[float] = None
    load_5m: Optional[float] = None
    load_15m: Optional[float] = None
    ram_total_mb: Optional[int] = Field(None, ge=0)
    ram_used_mb: Optional[int] = Field(None, ge=0)
    ram_available_mb: Optional[int] = Field(None, ge=0)
    ram_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    swap_total_mb: Optional[int] = Field(None, ge=0)
    swap_used_mb: Optional[int] = Field(None, ge=0)
    swap_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    disk_total_gb: Optional[float] = Field(None, ge=0.0)
    disk_used_gb: Optional[float] = Field(None, ge=0.0)
    disk_free_gb: Optional[float] = Field(None, ge=0.0)
    disk_read_mbs: Optional[float] = Field(None, ge=0.0)
    disk_write_mbs: Optional[float] = Field(None, ge=0.0)
    disk_iops: Optional[float] = Field(None, ge=0.0)
    net_rx_kbs: Optional[float] = Field(None, ge=0.0)
    net_tx_kbs: Optional[float] = Field(None, ge=0.0)
    cpu_temp_c: Optional[float] = None
    battery_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    is_ac_online: int = Field(default=-1, ge=-1, le=1)  # 1=AC plugged in, 0=Battery, -1=Desktop/N/A
    worker_res_mb: Optional[float] = Field(None, ge=0.0)
    ollama_res_mb: Optional[float] = Field(None, ge=0.0)
    worker_cpu_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    ollama_cpu_percent: Optional[float] = Field(None, ge=0.0, le=100.0)
    gpu: Optional[GpuTelemetry] = None
    latency_to_controller_ms: Optional[float] = Field(None, ge=0.0)
    os_uptime_s: Optional[int] = Field(None, ge=0)
    worker_service_status: str = "running"
    ollama_service_status: str = "unknown"


class InferenceTelemetryPayload(BaseModel):
    request_id: str = Field(..., min_length=1)
    run_id: Optional[str] = None
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    node_id: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    provider: str = "ollama"
    status: str = "success"  # success, error, timeout
    prompt_tokens: int = Field(default=0, ge=0)
    gen_tokens: int = Field(default=0, ge=0)
    prompt_eval_duration_ms: float = Field(default=0.0, ge=0.0)
    eval_duration_ms: float = Field(default=0.0, ge=0.0)
    load_duration_ms: float = Field(default=0.0, ge=0.0)
    total_duration_ms: float = Field(default=0.0, ge=0.0)
    tokens_per_second: Optional[float] = Field(default=None, ge=0.0)
    time_to_first_token_ms: Optional[float] = Field(default=None, ge=0.0)
    error_category: Optional[str] = None
    retry_count: int = Field(default=0, ge=0)
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class AlertAcknowledgeRequest(BaseModel):
    notes: Optional[str] = ""


class AlertRulePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    category: str = Field(..., pattern="^(cpu|memory|swap|temperature|storage|worker|model|controller)$")
    metric_name: str
    threshold: float
    operator: str = Field(..., pattern="^(>|>=|<|<=|==|!=)$")
    duration_seconds: int = Field(default=30, ge=5, le=3600)
    severity: str = Field(default="warning", pattern="^(info|warning|critical)$")
    is_enabled: bool = True
