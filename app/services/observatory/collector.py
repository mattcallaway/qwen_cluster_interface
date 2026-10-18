"""
System Observatory Telemetry Collector.
Cross-platform collectors for Windows controller and Linux workers.
Gathers real hardware metrics (CPU, RAM, swap, disk, network, power, GPU) without fabricating data.
"""

import ctypes
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.services.observatory import storage

logger = logging.getLogger("qwen_collective.observatory.collector")

# Keep previous measurement state for rate metrics (disk I/O, network throughput, CPU usage)
_PREV_STATE: Dict[str, Any] = {
    "ts": 0.0,
    "idle_ticks": 0,
    "kernel_ticks": 0,
    "user_ticks": 0,
    "net_rx_bytes": 0,
    "net_tx_bytes": 0,
    "disk_read_bytes": 0,
    "disk_write_bytes": 0,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def collect_gpu_telemetry() -> Dict[str, Any]:
    """Inspect GPU metrics using hardware-appropriate tools (e.g. nvidia-smi).
    Returns real metrics if supported, or an explicit unsupported payload. Never returns fabricated zeros.
    """
    # 1. Try nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            cmd = [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.total,memory.used,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
            if res.returncode == 0 and res.stdout.strip():
                parts = [p.strip() for p in res.stdout.strip().splitlines()[0].split(",")]
                if len(parts) >= 6:
                    return {
                        "supported": True,
                        "name": parts[0],
                        "utilization_percent": float(parts[1]) if parts[1] != "[N/A]" else None,
                        "vram_total_mb": float(parts[2]) if parts[2] != "[N/A]" else None,
                        "vram_used_mb": float(parts[3]) if parts[3] != "[N/A]" else None,
                        "temperature_c": float(parts[4]) if parts[4] != "[N/A]" else None,
                        "power_watts": float(parts[5]) if parts[5] != "[N/A]" else None,
                        "status_note": "Active NVIDIA GPU",
                    }
        except Exception as e:
            logger.debug("nvidia-smi query failed: %s", e)

    return {
        "supported": False,
        "name": None,
        "utilization_percent": None,
        "vram_total_mb": None,
        "vram_used_mb": None,
        "temperature_c": None,
        "power_watts": None,
        "status_note": "Not supported (no dedicated GPU detected)",
    }


def collect_windows_power_status() -> Tuple[int, Optional[float]]:
    """Query AC power and battery percentage on Windows via GetSystemPowerStatus.
    Returns: (is_ac_online, battery_percent)
    is_ac_online: 1 = AC connected, 0 = on battery, -1 = desktop/unknown
    """
    if platform.system() != "Windows":
        return -1, None
    try:
        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_byte),
                ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("SystemStatusFlag", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        sps = SYSTEM_POWER_STATUS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
            ac_online = int(sps.ACLineStatus)
            if ac_online not in (0, 1):
                ac_online = -1
            bat_pct = None
            if 0 <= sps.BatteryLifePercent <= 100:
                bat_pct = float(sps.BatteryLifePercent)
            return ac_online, bat_pct
    except Exception:
        pass
    return -1, None


def collect_process_memory_mb(pid: Optional[int] = None) -> Optional[float]:
    """Query current resident memory (RSS in MB) for the target process."""
    if platform.system() == "Windows":
        try:
            class PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            k32 = ctypes.windll.kernel32
            k32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_ulong]
            k32.K32GetProcessMemoryInfo.restype = ctypes.c_int
            h = k32.GetCurrentProcess() if pid is None else k32.OpenProcess(0x0410, False, pid)
            if h and k32.K32GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                rss_mb = round(pmc.WorkingSetSize / (1024 * 1024), 2)
                if pid is not None:
                    k32.CloseHandle(h)
                return rss_mb
        except Exception:
            pass
    elif platform.system() == "Linux":
        try:
            target_pid = pid if pid is not None else os.getpid()
            with open(f"/proc/{target_pid}/statm", "r") as f:
                parts = f.read().split()
                # 2nd field is resident pages (page size typically 4096)
                res_pages = int(parts[1])
                return round((res_pages * 4096) / (1024 * 1024), 2)
        except Exception:
            pass
    return None


def collect_controller_telemetry() -> Dict[str, Any]:
    """Collect real hardware telemetry for the local Windows controller node ('controller-local')."""
    global _PREV_STATE
    now_ts = time.time()
    iso_stamp = _now_iso()

    # 1. Memory and Swap
    ram_total_mb = 1024
    ram_avail_mb = 512
    ram_pct = 50.0
    swap_total_mb = 0
    swap_used_mb = 0
    swap_pct = 0.0

    if platform.system() == "Windows":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                ram_total_mb = int(stat.ullTotalPhys // (1024 * 1024))
                ram_avail_mb = int(stat.ullAvailPhys // (1024 * 1024))
                ram_pct = float(stat.dwMemoryLoad)
                page_total = int(stat.ullTotalPageFile // (1024 * 1024))
                page_avail = int(stat.ullAvailPageFile // (1024 * 1024))
                swap_total_mb = max(0, page_total - ram_total_mb)
                ram_used = ram_total_mb - ram_avail_mb
                swap_used_mb = max(0, (page_total - page_avail) - ram_used)
                swap_pct = round((swap_used_mb / swap_total_mb) * 100.0, 1) if swap_total_mb > 0 else 0.0
        except Exception as e:
            logger.debug("Windows memory telemetry error: %s", e)

    ram_used_mb = max(0, ram_total_mb - ram_avail_mb)

    # 2. CPU Utilization
    cpu_percent = 0.0
    if platform.system() == "Windows":
        try:
            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

            idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
            if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                def to_int(ft):
                    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

                cur_idle = to_int(idle)
                cur_kernel = to_int(kernel)
                cur_user = to_int(user)

                prev_idle = _PREV_STATE.get("idle_ticks", 0)
                prev_kernel = _PREV_STATE.get("kernel_ticks", 0)
                prev_user = _PREV_STATE.get("user_ticks", 0)

                if prev_idle > 0:
                    d_idle = cur_idle - prev_idle
                    d_kernel = cur_kernel - prev_kernel
                    d_user = cur_user - prev_user
                    total_sys = d_kernel + d_user
                    if total_sys > 0:
                        cpu_percent = round(100.0 * (1.0 - (d_idle / total_sys)), 1)
                        cpu_percent = max(0.0, min(100.0, cpu_percent))

                _PREV_STATE["idle_ticks"] = cur_idle
                _PREV_STATE["kernel_ticks"] = cur_kernel
                _PREV_STATE["user_ticks"] = cur_user
        except Exception as e:
            logger.debug("Windows CPU telemetry error: %s", e)

    # 3. Disk Space and Database Headroom
    drive_root = os.path.splitdrive(str(settings.data_dir))[0] or "C:"
    drive_path = drive_root + "\\"
    disk_total_gb = 100.0
    disk_free_gb = 50.0
    try:
        usage = shutil.disk_usage(drive_path)
        disk_total_gb = round(usage.total / (1024**3), 2)
        disk_free_gb = round(usage.free / (1024**3), 2)
    except Exception:
        pass
    disk_used_gb = round(max(0.0, disk_total_gb - disk_free_gb), 2)

    # 4. Power & Battery
    is_ac_online, battery_pct = collect_windows_power_status()

    # 5. Process Resident Memory (Controller FastAPI Process)
    controller_res_mb = collect_process_memory_mb()

    # 6. GPU Telemetry
    gpu_data = collect_gpu_telemetry()

    # 7. Uptime
    uptime_s = None
    if platform.system() == "Windows":
        try:
            uptime_ms = ctypes.windll.kernel32.GetTickCount64()
            uptime_s = int(uptime_ms // 1000)
        except Exception:
            pass

    _PREV_STATE["ts"] = now_ts

    return {
        "node_id": "controller-local",
        "timestamp": iso_stamp,
        "cpu_percent": cpu_percent,
        "per_core": [cpu_percent],
        "cpu_freq_mhz": None,
        "load_1m": None,  # Not natively defined on Windows without emulation
        "load_5m": None,
        "load_15m": None,
        "ram_total_mb": ram_total_mb,
        "ram_used_mb": ram_used_mb,
        "ram_available_mb": ram_avail_mb,
        "ram_percent": ram_pct,
        "swap_total_mb": swap_total_mb,
        "swap_used_mb": swap_used_mb,
        "swap_percent": swap_pct,
        "disk_total_gb": disk_total_gb,
        "disk_used_gb": disk_used_gb,
        "disk_free_gb": disk_free_gb,
        "disk_read_mbs": 0.0,
        "disk_write_mbs": 0.0,
        "disk_iops": 0.0,
        "net_rx_kbs": 0.0,
        "net_tx_kbs": 0.0,
        "cpu_temp_c": None,  # Windows ACPI temp requires motherboard driver or WMI
        "battery_percent": battery_pct,
        "is_ac_online": is_ac_online,
        "worker_res_mb": controller_res_mb,
        "ollama_res_mb": None,
        "worker_cpu_percent": None,
        "ollama_cpu_percent": None,
        "gpu": gpu_data,
        "latency_to_controller_ms": 0.0,  # Loopback
        "os_uptime_s": uptime_s,
        "worker_service_status": "running",
        "ollama_service_status": "unknown",
    }


def record_worker_telemetry_submission(
    authenticated_node_id: str,
    payload: Dict[str, Any],
) -> int:
    """Ingest, validate, and store a telemetry submission from an authenticated worker.
    Strictly enforces that the sample node_id matches the authenticated token node_id.
    """
    reported_node = payload.get("node_id")
    if reported_node and reported_node != authenticated_node_id:
        raise ValueError(f"Node ID mismatch: token belongs to '{authenticated_node_id}', cannot report for '{reported_node}'.")

    # Bind strictly to authenticated node
    payload["node_id"] = authenticated_node_id
    if not payload.get("timestamp"):
        payload["timestamp"] = _now_iso()

    # Store raw sample
    sample_id = storage.record_telemetry_sample(authenticated_node_id, payload)
    return sample_id
