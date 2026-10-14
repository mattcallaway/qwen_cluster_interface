import os
import re
import socket
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger("qwen_collective.hardware_probe")

from app.services.network_security import validate_probe_target

def test_tcp_reachability(host: str, port: int = 22, timeout: float = 3.0) -> Tuple[bool, str]:
    """Test if a TCP port (default SSH: 22) is reachable without sending credentials."""
    # Enforce network safety and SSRF boundary
    is_safe, reason = validate_probe_target(host, port)
    if not is_safe:
        return False, f"Probe rejected: {reason}"

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        if result == 0:
            return True, f"Port {port} is open and reachable."
        else:
            return False, f"Connection refused or port {port} closed (errno {result})."
    except socket.gaierror:
        return False, f"Hostname '{host}' could not be resolved."
    except socket.timeout:
        return False, f"Connection timed out reaching {host}:{port}."
    except Exception as e:
        return False, f"Reachability check failed: {str(e)}"

def parse_uname_output(output: str) -> Dict[str, str]:
    """Parse `uname -a` output safely."""
    line = output.strip()
    return {"os_kernel": line}

def parse_free_output(output: str) -> Dict[str, Optional[int]]:
    """
    Parse `free -m` output defensively.
    Expected format:
                  total        used        free      shared  buff/cache   available
    Mem:           3892        1200         800         120        1892        2400
    """
    total_mb = None
    avail_mb = None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower().startswith("mem"):
            try:
                total_mb = int(parts[1])
                # 'available' is often the last column (parts[6] or parts[3] in older free)
                if len(parts) >= 7:
                    avail_mb = int(parts[6])
                elif len(parts) >= 4:
                    avail_mb = int(parts[3])
            except (ValueError, IndexError):
                pass
    return {"ram_total_mb": total_mb, "ram_avail_mb": avail_mb}

def parse_lscpu_output(output: str) -> Dict[str, Any]:
    """
    Parse `lscpu` output defensively.
    Extract Architecture, Model name, CPU(s), CPU max MHz.
    """
    data = {
        "arch": None,
        "model": None,
        "cores": None
    }
    for line in output.splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip().lower()
            val = val.strip()
            if key == "architecture":
                data["arch"] = val
            elif key in ("model name", "model"):
                data["model"] = val
            elif key in ("cpu(s)", "cpus"):
                try:
                    data["cores"] = int(val)
                except ValueError:
                    pass
    return data

def parse_lsblk_output(output: str) -> str:
    """Parse lsblk output into a concise summary."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return " | ".join(lines[:4])

def parse_ip_output(output: str) -> str:
    """Parse `ip -brief addr` or `ip addr` defensively."""
    ips = []
    for line in output.splitlines():
        match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', line)
        if match:
            ip = match.group(1)
            if not ip.startswith("127."):
                ips.append(ip)
        else:
            parts = line.split()
            if len(parts) >= 3 and "/" in parts[2]:
                ip = parts[2].split("/")[0]
                if not ip.startswith("127."):
                    ips.append(ip)
    return ", ".join(list(dict.fromkeys(ips)))

def recommend_role_and_model(ram_total_mb: Optional[int], cpu_arch: Optional[str], is_vps: bool = False) -> Dict[str, Any]:
    """
    Produce transparent, advisory recommendations based on observed hardware.
    Never promises or guarantees performance.
    """
    if is_vps:
        return {
            "recommended_role": "vps_worker",
            "explanation": "Internet-connected VPS detected. Recommended for isolated web research and external execution tasks under strict approval policies.",
            "suitable_models": []
        }
        
    if ram_total_mb is None:
        return {
            "recommended_role": "unassigned",
            "explanation": "RAM specifications are unknown. Please inspect hardware or assign a role manually.",
            "suitable_models": []
        }
        
    if ram_total_mb < 3000:
        return {
            "recommended_role": "distributed_worker",
            "explanation": f"Detected ~{ram_total_mb} MB RAM. Limited for standalone inference; recommended as an RPC worker in a llama.cpp distributed group or lightweight task runner.",
            "suitable_models": ["Qwen2.5-0.5B-Instruct-Q4_K_M (via RPC or standalone)", "Distributed RPC shard participation"]
        }
    elif ram_total_mb <= 5500:
        return {
            "recommended_role": "distributed_worker",
            "explanation": f"Detected ~{ram_total_mb} MB RAM (typical ~4 GB Linux laptop). Can run Qwen 1.5B quantized standalone, or participate as an RPC worker pooling memory with another laptop for Qwen 7B.",
            "suitable_models": ["Qwen2.5-1.5B-Instruct-Q4_K_M (standalone)", "Qwen2.5-3B-Instruct-Q4_K_M (tight)", "Distributed RPC worker for Qwen2.5-7B"]
        }
    elif ram_total_mb <= 12000:
        return {
            "recommended_role": "standalone_server",
            "explanation": f"Detected ~{ram_total_mb} MB RAM (8-12 GB). Ample capacity for standalone Qwen 7B Q4_K_M or acting as a distributed inference coordinator.",
            "suitable_models": ["Qwen2.5-7B-Instruct-Q4_K_M", "Qwen2.5-3B-Instruct-Q8_0", "Distributed inference coordinator"]
        }
    else:
        return {
            "recommended_role": "standalone_server",
            "explanation": f"Detected ~{ram_total_mb} MB RAM (16 GB+). Capable of running 14B+ models or serving as a high-performance inference coordinator.",
            "suitable_models": ["Qwen2.5-14B-Instruct-Q4_K_M", "Qwen2.5-7B-Instruct-Q8_0"]
        }

def probe_remote_ssh(host: str, username: str, key_path: Optional[str] = None, port: int = 22, timeout: int = 10) -> Dict[str, Any]:
    """
    Perform read-only inspection using paramiko over SSH.
    Executes defensive commands without storing credentials.
    """
    try:
        import paramiko
    except ImportError:
        return {"success": False, "error": "Paramiko library is not installed."}
        
    client = paramiko.SSHClient()
    # Reject unknown host keys by default as mandated by security specification
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    
    # Load known_hosts from system and standard Windows/user locations
    try:
        client.load_system_host_keys()
    except Exception:
        pass
    
    custom_known_hosts = os.getenv("QC_KNOWN_HOSTS_PATH")
    if custom_known_hosts and os.path.exists(custom_known_hosts):
        try:
            client.load_host_keys(custom_known_hosts)
        except Exception:
            pass
    else:
        user_known_hosts = Path.home() / ".ssh" / "known_hosts"
        if user_known_hosts.exists():
            try:
                client.load_host_keys(str(user_known_hosts))
            except Exception:
                pass
    
    try:
        kwargs: Dict[str, Any] = {
            "hostname": host,
            "port": port,
            "username": username,
            "timeout": timeout,
            "look_for_keys": True,
            "allow_agent": True
        }
        if key_path and key_path.strip():
            # Robust Windows path expansion (~ and environment vars)
            expanded_key = Path(os.path.expandvars(os.path.expanduser(key_path.strip()))).resolve()
            if not expanded_key.exists():
                return {"success": False, "error": f"SSH private key file not found at path: {expanded_key}"}
            kwargs["key_filename"] = str(expanded_key)
            
        client.connect(**kwargs)
        
        commands = {
            "uname": "uname -srm",
            "lscpu": "lscpu",
            "free": "free -m",
            "lsblk": "lsblk -o NAME,SIZE,TYPE,MOUNTPOINT",
            "ip": "ip -brief addr || ifconfig"
        }
        
        outputs = {}
        for key, cmd in commands.items():
            stdin, stdout, stderr = client.exec_command(cmd, timeout=5)
            outputs[key] = stdout.read().decode('utf-8', errors='replace')
            
        client.close()
        
        # Parse outputs defensively
        free_data = parse_free_output(outputs.get("free", ""))
        lscpu_data = parse_lscpu_output(outputs.get("lscpu", ""))
        uname_data = parse_uname_output(outputs.get("uname", ""))
        disk_str = parse_lsblk_output(outputs.get("lsblk", ""))
        ip_str = parse_ip_output(outputs.get("ip", ""))
        
        cpu_display = ""
        if lscpu_data["model"]:
            cpu_display = f"{lscpu_data['model']} ({lscpu_data['cores'] or '?'} cores, {lscpu_data['arch'] or '?'})"
        elif lscpu_data["arch"]:
            cpu_display = f"Architecture: {lscpu_data['arch']}"
            
        recommendation = recommend_role_and_model(free_data["ram_total_mb"], lscpu_data["arch"])
        
        return {
            "success": True,
            "os_info": uname_data["os_kernel"],
            "cpu_info": cpu_display,
            "ram_total_mb": free_data["ram_total_mb"],
            "ram_avail_mb": free_data["ram_avail_mb"],
            "disk_info": disk_str,
            "network_info": ip_str or host,
            "recommendation": recommendation
        }
    except paramiko.AuthenticationException:
        return {"success": False, "error": "SSH Authentication failed. Check authorized_keys on target machine."}
    except paramiko.SSHException as e:
        err_msg = str(e)
        if "not found in known_hosts" in err_msg.lower() or "reject" in err_msg.lower():
            return {"success": False, "error": f"Unknown SSH host key rejected for {host}. Host key must be explicitly added to known_hosts before probing."}
        return {"success": False, "error": f"SSH protocol error: {err_msg}"}
    except socket.timeout:
        return {"success": False, "error": f"SSH connection to {host}:{port} timed out."}
    except Exception as e:
        return {"success": False, "error": f"Inspection failed: {str(e)}"}
