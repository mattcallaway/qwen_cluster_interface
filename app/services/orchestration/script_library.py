"""Fixed, reviewed script library. Model output may only *select* a script by id; it can never supply code.

Each script is read-only diagnostics: no environment dump, no network, no config files, no secrets.
"""
from __future__ import annotations

import textwrap
from typing import Any, Callable, Dict, List, Tuple

HEALTH_SCHEMA = "qc.health.v1"

_SYS_HEALTH_BASIC = textwrap.dedent('''
    import json, os, hashlib, datetime, platform, shutil

    def safe(fn, default=None):
        try:
            return fn()
        except Exception as e:
            return default

    def os_name():
        for line in open("/etc/os-release").read().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
        return None

    def meminfo():
        d = {}
        for line in open("/proc/meminfo").read().splitlines():
            k, _, v = line.partition(":")
            if k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                d[k] = int(v.split()[0]) // 1024  # MiB
        return d

    mem = safe(meminfo, {}) or {}
    disk = safe(lambda: shutil.disk_usage("/"), None)
    out = {
        "schema": "qc.health.v1",
        "kernel": safe(lambda: platform.release()),
        "os": safe(os_name),
        "arch": safe(lambda: platform.machine()),
        "cpu_count": safe(lambda: os.cpu_count()),
        "loadavg": safe(lambda: list(os.getloadavg())),
        "mem_total_mib": mem.get("MemTotal"),
        "mem_available_mib": mem.get("MemAvailable"),
        "swap_total_mib": mem.get("SwapTotal"),
        "swap_free_mib": mem.get("SwapFree"),
        "uptime_seconds": safe(lambda: float(open("/proc/uptime").read().split()[0])),
        "disk_root_total_gib": round(disk.total / 2**30, 2) if disk else None,
        "disk_root_free_gib": round(disk.free / 2**30, 2) if disk else None,
        "python": safe(lambda: platform.python_version()),
        "machine_id_sha16": safe(lambda: hashlib.sha256(open("/etc/machine-id").read().strip().encode()).hexdigest()[:16]),
        "collected_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    open("result.json", "w").write(json.dumps(out))
    print(json.dumps(out))
''').strip() + "\n"


def _validate_health(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    problems: List[str] = []
    if payload.get("schema") != HEALTH_SCHEMA:
        problems.append(f"schema must be {HEALTH_SCHEMA}")
    for field in ("kernel", "cpu_count", "mem_total_mib", "uptime_seconds", "collected_utc"):
        if payload.get(field) in (None, ""):
            problems.append(f"missing field: {field}")
    mt, ma = payload.get("mem_total_mib"), payload.get("mem_available_mib")
    if isinstance(mt, (int, float)) and isinstance(ma, (int, float)) and not (0 < ma <= mt):
        problems.append("memory figures are inconsistent (available must be within 0..total)")
    if isinstance(payload.get("cpu_count"), int) and payload["cpu_count"] < 1:
        problems.append("cpu_count must be >= 1")
    la = payload.get("loadavg")
    if la is not None and not (isinstance(la, list) and len(la) == 3):
        problems.append("loadavg must be a 3-element list")
    return (not problems, problems)


LIBRARY: Dict[str, Dict[str, Any]] = {
    "sys_health_basic": {
        "title": "Basic system health snapshot (read-only)",
        "script": _SYS_HEALTH_BASIC,
        "capabilities": ["python_execution"],
        "network_access": False,
        "risk": "low",
        "idempotent": True,
        "result_schema": HEALTH_SCHEMA,
        "validator": _validate_health,
        "timeout_seconds": 60,
    },
}


def get_script(script_id: str) -> Dict[str, Any]:
    if script_id not in LIBRARY:
        raise KeyError(script_id)
    return LIBRARY[script_id]


def validator_for(script_id: str) -> Callable[[Dict[str, Any]], Tuple[bool, List[str]]]:
    return LIBRARY[script_id]["validator"]
