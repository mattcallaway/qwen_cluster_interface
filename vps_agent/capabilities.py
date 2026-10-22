"""
Local Hardware & Runtime Discovery for VPS Worker Agent.
Strictly read-only inspection. Never modifies system state or downloads files.
"""

import os
import platform
import shutil
import socket
import sys
from typing import Any, Dict, List, Optional


def discover_local_hardware() -> Dict[str, Any]:
    """Inspect local hardware resources in a cross-platform manner."""
    cpu_count = os.cpu_count() or 1
    hostname = socket.gethostname()
    os_name = platform.system()
    os_version = platform.release()
    arch = platform.machine()

    # Memory inspection
    ram_total_mb = 1024
    ram_available_mb = 512
    if os_name == "Linux" and os.path.exists("/proc/meminfo"):
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                meminfo = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        meminfo[parts[0].strip()] = parts[1].strip()
                if "MemTotal" in meminfo:
                    ram_total_mb = int(meminfo["MemTotal"].split()[0]) // 1024
                if "MemAvailable" in meminfo:
                    ram_available_mb = int(meminfo["MemAvailable"].split()[0]) // 1024
        except Exception:
            pass
    elif os_name == "Windows":
        try:
            import ctypes
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
                ram_available_mb = int(stat.ullAvailPhys // (1024 * 1024))
        except Exception:
            pass

    # Disk inspection
    disk_total_gb = 20.0
    disk_free_gb = 10.0
    try:
        usage = shutil.disk_usage("/")
        disk_total_gb = round(usage.total / (1024**3), 2)
        disk_free_gb = round(usage.free / (1024**3), 2)
    except Exception:
        try:
            usage = shutil.disk_usage("C:\\")
            disk_total_gb = round(usage.total / (1024**3), 2)
            disk_free_gb = round(usage.free / (1024**3), 2)
        except Exception:
            pass

    # Check installed tools & runtimes
    installed_runtimes = [f"python-{sys.version_info.major}.{sys.version_info.minor}"]
    for tool in ("sh", "bash", "curl", "git", "systemctl", "cron", "ollama"):
        if shutil.which(tool):
            installed_runtimes.append(tool)

    # Discovered capability categories
    declared_capabilities = ["python_execution", "file_processing", "monitoring"]
    if shutil.which("bash") or shutil.which("sh"):
        declared_capabilities.append("approved_shell")
    if shutil.which("systemctl"):
        declared_capabilities.append("persistent_services")
        declared_capabilities.append("scheduled_execution")

    # Discover local AI models & runtimes
    available_models = discover_local_models()
    if shutil.which("ollama") or available_models:
        if "ollama" not in installed_runtimes:
            installed_runtimes.append("ollama")
        declared_capabilities.append("model_inference")
        declared_capabilities.append("ollama_serving")
        for m in available_models:
            m_lower = m.lower()
            if "qwen" in m_lower and "qwen_inference" not in declared_capabilities:
                declared_capabilities.append("qwen_inference")
            if "llama" in m_lower and "llama_inference" not in declared_capabilities:
                declared_capabilities.append("llama_inference")

    return {
        "hostname": hostname,
        "os_name": os_name,
        "os_version": os_version,
        "architecture": arch,
        "cpu_count": cpu_count,
        "ram_total_mb": ram_total_mb,
        "ram_available_mb": ram_available_mb,
        "disk_total_gb": disk_total_gb,
        "disk_free_gb": disk_free_gb,
        "installed_runtimes": installed_runtimes,
        "declared_capabilities": declared_capabilities,
        "available_models": available_models,
        "agent_version": "1.0.0-rc.2",
    }


def discover_local_models() -> List[str]:
    """Inspect local Ollama runtime to discover installed models."""
    models: List[str] = []
    # 1. Try querying local Ollama HTTP API (fast, cross-platform)
    try:
        import urllib.request
        import json
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags", headers={"User-Agent": "qwen-collective-agent"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data.get("models", []):
                    name = item.get("name")
                    if name and name not in models:
                        models.append(name)
    except Exception:
        pass

    # 2. Fallback to CLI inspection if API did not return
    if not models and shutil.which("ollama"):
        try:
            import subprocess
            out = subprocess.check_output(["ollama", "list"], text=True, timeout=2.0)
            for line in out.strip().splitlines()[1:]:
                parts = line.split()
                if parts and parts[0] not in models:
                    models.append(parts[0])
        except Exception:
            pass

    return models


def collect_live_telemetry() -> Dict[str, Any]:
    """Collect real-time hardware telemetry without modifying system state or making external network calls."""
    os_name = platform.system()
    hardware = discover_local_hardware()

    swap_total_mb = 0
    swap_used_mb = 0
    swap_pct = 0.0
    cpu_temp_c = None
    battery_pct = None
    is_ac_online = -1
    load_1m = None
    load_5m = None
    load_15m = None
    worker_res_mb = None
    cpu_percent = 0.0

    # 1. Linux /proc and /sys inspection
    if os_name == "Linux":
        # Memory & Swap from /proc/meminfo
        if os.path.exists("/proc/meminfo"):
            try:
                with open("/proc/meminfo", "r", encoding="utf-8") as f:
                    meminfo = {}
                    for line in f:
                        parts = line.split(":")
                        if len(parts) == 2:
                            meminfo[parts[0].strip()] = parts[1].strip()
                    if "SwapTotal" in meminfo:
                        swap_total_mb = int(meminfo["SwapTotal"].split()[0]) // 1024
                    if "SwapFree" in meminfo:
                        swap_free_mb = int(meminfo["SwapFree"].split()[0]) // 1024
                        swap_used_mb = max(0, swap_total_mb - swap_free_mb)
                        if swap_total_mb > 0:
                            swap_pct = round((swap_used_mb / swap_total_mb) * 100.0, 1)
            except Exception:
                pass

        # Load average
        try:
            loads = os.getloadavg()
            load_1m, load_5m, load_15m = round(loads[0], 2), round(loads[1], 2), round(loads[2], 2)
            cpu_count = hardware.get("cpu_count", 1) or 1
            cpu_percent = min(100.0, round((loads[0] / cpu_count) * 100.0, 1))
        except Exception:
            pass

        # CPU Temperature from /sys/class/thermal/
        thermal_dir = "/sys/class/thermal"
        if os.path.exists(thermal_dir):
            try:
                for entry in sorted(os.listdir(thermal_dir)):
                    if entry.startswith("thermal_zone"):
                        temp_file = os.path.join(thermal_dir, entry, "temp")
                        if os.path.exists(temp_file):
                            with open(temp_file, "r") as tf:
                                raw_val = int(tf.read().strip())
                                # Millidegrees C
                                cpu_temp_c = round(raw_val / 1000.0, 1)
                                break
            except Exception:
                pass

        # Power supply & Battery from /sys/class/power_supply/
        ps_dir = "/sys/class/power_supply"
        if os.path.exists(ps_dir):
            try:
                for entry in os.listdir(ps_dir):
                    entry_path = os.path.join(ps_dir, entry)
                    # Check battery
                    if entry.startswith("BAT") or "battery" in entry.lower():
                        cap_file = os.path.join(entry_path, "capacity")
                        if os.path.exists(cap_file):
                            with open(cap_file, "r") as bf:
                                battery_pct = float(bf.read().strip())
                    # Check AC adapter
                    if entry.startswith("AC") or entry.startswith("ADP") or "online" in os.listdir(entry_path):
                        on_file = os.path.join(entry_path, "online")
                        if os.path.exists(on_file):
                            with open(on_file, "r") as af:
                                is_ac_online = int(af.read().strip())
            except Exception:
                pass

        # Process resident memory
        try:
            with open("/proc/self/statm", "r") as f:
                res_pages = int(f.read().split()[1])
                worker_res_mb = round((res_pages * 4096) / (1024 * 1024), 2)
        except Exception:
            pass

    elif os_name == "Windows":
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                ram_total = stat.ullTotalPhys // (1024 * 1024)
                ram_avail = stat.ullAvailPhys // (1024 * 1024)
                page_total = stat.ullTotalPageFile // (1024 * 1024)
                page_avail = stat.ullAvailPageFile // (1024 * 1024)
                swap_total_mb = max(0, page_total - ram_total)
                ram_used = ram_total - ram_avail
                swap_used_mb = max(0, (page_total - page_avail) - ram_used)
                swap_pct = round((swap_used_mb / swap_total_mb) * 100.0, 1) if swap_total_mb > 0 else 0.0

            class SYSTEM_POWER_STATUS(ctypes.Structure):
                _fields_ = [
                    ("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong),
                ]
            sps = SYSTEM_POWER_STATUS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
                is_ac_online = int(sps.ACLineStatus)
                if 0 <= sps.BatteryLifePercent <= 100:
                    battery_pct = float(sps.BatteryLifePercent)
        except Exception:
            pass

    # GPU discovery
    gpu_data = {"supported": False, "status_note": "Not supported (no discrete GPU detected)"}
    if shutil.which("nvidia-smi"):
        try:
            import subprocess
            cmd = ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.total,memory.used,temperature.gpu,power.draw", "--format=csv,noheader,nounits"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5)
            if res.returncode == 0 and res.stdout.strip():
                parts = [p.strip() for p in res.stdout.strip().splitlines()[0].split(",")]
                if len(parts) >= 6:
                    gpu_data = {
                        "supported": True,
                        "name": parts[0],
                        "utilization_percent": float(parts[1]) if parts[1] != "[N/A]" else None,
                        "vram_total_mb": float(parts[2]) if parts[2] != "[N/A]" else None,
                        "vram_used_mb": float(parts[3]) if parts[3] != "[N/A]" else None,
                        "temperature_c": float(parts[4]) if parts[4] != "[N/A]" else None,
                        "power_watts": float(parts[5]) if parts[5] != "[N/A]" else None,
                        "status_note": "Active GPU",
                    }
        except Exception:
            pass

    ram_total = hardware.get("ram_total_mb", 1024)
    ram_avail = hardware.get("ram_available_mb", 512)
    ram_used = max(0, ram_total - ram_avail)
    ram_pct = round((ram_used / ram_total) * 100.0, 1) if ram_total > 0 else 0.0

    disk_total = hardware.get("disk_total_gb", 20.0)
    disk_free = hardware.get("disk_free_gb", 10.0)
    disk_used = round(max(0.0, disk_total - disk_free), 2)

    return {
        "cpu_percent": cpu_percent,
        "load_1m": load_1m,
        "load_5m": load_5m,
        "load_15m": load_15m,
        "ram_total_mb": ram_total,
        "ram_used_mb": ram_used,
        "ram_available_mb": ram_avail,
        "ram_percent": ram_pct,
        "swap_total_mb": swap_total_mb,
        "swap_used_mb": swap_used_mb,
        "swap_percent": swap_pct,
        "disk_total_gb": disk_total,
        "disk_used_gb": disk_used,
        "disk_free_gb": disk_free,
        "cpu_temp_c": cpu_temp_c,
        "battery_percent": battery_pct,
        "is_ac_online": is_ac_online,
        "worker_res_mb": worker_res_mb,
        "gpu": gpu_data,
        "available_models": hardware.get("available_models", []),
    }
