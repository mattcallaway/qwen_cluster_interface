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
    for tool in ("sh", "bash", "curl", "git", "systemctl", "cron"):
        if shutil.which(tool):
            installed_runtimes.append(tool)

    # Discovered capability categories
    declared_capabilities = ["python_execution", "file_processing", "monitoring"]
    if shutil.which("bash") or shutil.which("sh"):
        declared_capabilities.append("approved_shell")
    if shutil.which("systemctl"):
        declared_capabilities.append("persistent_services")
        declared_capabilities.append("scheduled_execution")

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
        "agent_version": "1.0.0",
    }
