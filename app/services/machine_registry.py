import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from app.database import get_db_connection

logger = logging.getLogger("qwen_collective.machine_registry")

def generate_machine_id(prefix: str = "node") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"

def log_machine_audit(machine_id: str, action: str, details: str = ""):
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO machine_audits (machine_id, action, details, timestamp) VALUES (?, ?, ?, ?)",
        (machine_id, action, details, now)
    )
    conn.commit()
    conn.close()

def _overlay_worker_health(machines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """For machines that are authenticated daemon workers, derive status from heartbeat age.

    The machines table holds a copied snapshot; without this overlay a dead worker
    would stay 'online' forever on /machines and the dashboard counters.
    """
    if not machines:
        return machines
    from app.services.node_health import effective_health
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT id, role, health_status, last_contact_at, notes FROM infrastructure_nodes").fetchall()
    finally:
        conn.close()
    infra = {r["id"]: dict(r) for r in rows}
    for m in machines:
        if m.get("connection_status") == "retired" or str(m.get("notes", "")).startswith("[RETIRED"):
            continue
        n = infra.get(m["id"])
        if not n:
            continue
        if str(n.get("notes", "")).startswith("[RETIRED"):
            m["connection_status"] = "offline"
            continue
        eff = effective_health(n["role"], n["health_status"], n["last_contact_at"])
        if n["role"] in ("vps_worker", "inference_node", "compute_worker", "general_worker") and n["last_contact_at"]:
            m["connection_status"] = "online" if eff == "online" else "offline"
            m["last_checked_at"] = n["last_contact_at"]
    return machines

def get_all_machines(include_retired: bool = False) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    if include_retired:
        cursor.execute("SELECT * FROM machines ORDER BY created_at DESC")
    else:
        cursor.execute("SELECT * FROM machines WHERE notes NOT LIKE '[RETIRED%' AND connection_status != 'retired' ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return _overlay_worker_health([dict(row) for row in rows])

def get_machine_by_id(machine_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM machines WHERE id = ?", (machine_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return _overlay_worker_health([dict(row)])[0]
    return None

def get_machine_audits(machine_id: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM machine_audits WHERE machine_id = ? ORDER BY timestamp DESC LIMIT 50", (machine_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def register_machine(
    display_name: str,
    ip_address: str,
    hostname: Optional[str] = None,
    connection_type: str = "ssh",
    role: str = "unassigned",
    os_info: Optional[str] = None,
    cpu_info: Optional[str] = None,
    ram_total_mb: Optional[int] = None,
    ram_avail_mb: Optional[int] = None,
    disk_info: Optional[str] = None,
    notes: str = ""
) -> Dict[str, Any]:
    """Register a new machine in the collective."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if duplicate IP already exists
    cursor.execute("SELECT id, display_name FROM machines WHERE ip_address = ?", (ip_address,))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        raise ValueError(f"Machine with IP address '{ip_address}' is already registered as '{existing['display_name']}' (ID: {existing['id']}).")
        
    machine_id = generate_machine_id()
    now = datetime.now(timezone.utc).isoformat()
    
    cursor.execute("""
    INSERT INTO machines (
        id, display_name, hostname, ip_address, connection_type,
        os_info, cpu_info, ram_total_mb, ram_avail_mb, disk_info,
        role, connection_status, notes, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?, ?)
    """, (
        machine_id, display_name.strip(), (hostname or "").strip(), ip_address.strip(),
        connection_type, os_info, cpu_info, ram_total_mb, ram_avail_mb, disk_info,
        role, notes.strip(), now, now
    ))
    conn.commit()
    conn.close()
    
    log_machine_audit(machine_id, "REGISTERED", f"Registered as {role} with IP {ip_address}")
    return get_machine_by_id(machine_id) # type: ignore

def update_machine_specs(
    machine_id: str,
    os_info: Optional[str] = None,
    cpu_info: Optional[str] = None,
    ram_total_mb: Optional[int] = None,
    ram_avail_mb: Optional[int] = None,
    disk_info: Optional[str] = None,
    network_info: Optional[str] = None,
    status: str = "online"
):
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
    UPDATE machines SET
        os_info = COALESCE(?, os_info),
        cpu_info = COALESCE(?, cpu_info),
        ram_total_mb = COALESCE(?, ram_total_mb),
        ram_avail_mb = COALESCE(?, ram_avail_mb),
        disk_info = COALESCE(?, disk_info),
        network_info = COALESCE(?, network_info),
        connection_status = ?,
        last_checked_at = ?,
        last_successful_connection = CASE WHEN ? = 'online' THEN ? ELSE last_successful_connection END,
        updated_at = ?
    WHERE id = ?
    """, (
        os_info, cpu_info, ram_total_mb, ram_avail_mb, disk_info, network_info,
        status, now, status, now, now, machine_id
    ))
    conn.commit()
    conn.close()
    log_machine_audit(machine_id, "HARDWARE_PROBED", f"Status: {status}. Specs updated.")

def update_machine_role(machine_id: str, new_role: str, notes: Optional[str] = None):
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()
    if notes is not None:
        conn.execute(
            "UPDATE machines SET role = ?, notes = ?, updated_at = ? WHERE id = ?",
            (new_role, notes, now, machine_id)
        )
    else:
        conn.execute(
            "UPDATE machines SET role = ?, updated_at = ? WHERE id = ?",
            (new_role, now, machine_id)
        )
    conn.commit()
    conn.close()
    log_machine_audit(machine_id, "ROLE_CHANGED", f"Role updated to {new_role}")

def toggle_machine_status(machine_id: str, is_enabled: bool):
    """Atomically toggle administrative enablement across both machines and infrastructure_nodes."""
    conn = get_db_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        val = 1 if is_enabled else 0
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE machines SET is_enabled = ?, updated_at = ? WHERE id = ?",
            (val, now, machine_id),
        )
        conn.execute(
            "UPDATE infrastructure_nodes SET is_enabled = ?, updated_at = ? WHERE id = ?",
            (val, now, machine_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    log_machine_audit(
        machine_id,
        "TOGGLE_ENABLED",
        f"Administrative enablement set to {is_enabled} across machines and infrastructure_nodes",
    )


def update_machine_capability_policy(
    machine_id: str,
    policy: Dict[str, Any],
    operator: str = "operator",
) -> Dict[str, Any]:
    """
    Operator-approved update to a worker's capability restrictions and operational policy.
    Creates an immutable, auditable authorization event.
    """
    conn = get_db_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        policy_json = json.dumps(policy)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE infrastructure_nodes SET capability_restrictions_json = ?, updated_at = ? WHERE id = ?",
            (policy_json, now, machine_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    log_machine_audit(
        machine_id,
        "CAPABILITY_POLICY_UPDATED",
        f"Operator '{operator}' updated capability policy: {json.dumps(policy)}",
    )
    return policy

def generate_setup_instructions(machine: Dict[str, Any]) -> Dict[str, str]:
    """Generate explicit, non-destructive setup instructions and manual commands."""
    role = machine.get("role", "unassigned")
    ip = machine.get("ip_address", "192.168.1.X")
    name = machine.get("display_name", "node")
    
    instructions = {}
    
    if role == "distributed_worker":
        instructions["summary"] = "Setup as a llama.cpp RPC Inference Worker"
        instructions["description"] = (
            "This node provides its RAM and CPU to participate in distributed inference. "
            "It runs the lightweight llama.cpp `rpc-server` daemon and listens for tensors sent by the coordinator."
        )
        instructions["commands"] = (
            f"# 1. Install prerequisites (Debian/Ubuntu)\n"
            f"sudo apt update && sudo apt install -y build-essential cmake git\n\n"
            f"# 2. Clone and build llama.cpp with RPC support\n"
            f"git clone https://github.com/ggerganov/llama.cpp.git\n"
            f"cd llama.cpp && cmake -B build -DGGML_RPC=ON && cmake --build build --config Release -j$(nproc)\n\n"
            f"# 3. Start the RPC worker server (listening on port 50052)\n"
            f"./build/bin/rpc-server -H 0.0.0.0 -p 50052\n\n"
            f"# (Optional) Run as a systemd service:\n"
            f"# sudo nano /etc/systemd/system/llama-rpc.service\n"
            f"# sudo systemctl enable --now llama-rpc"
        )
        instructions["warning"] = "SECURITY NOTE: llama.cpp RPC is unauthenticated. Ensure this port is restricted to your private local network or Tailscale subnet."
        
    elif role == "inference_coordinator":
        instructions["summary"] = "Setup as Distributed Inference Coordinator"
        instructions["description"] = (
            "This machine orchestrates the distributed inference group, coordinates worker nodes via RPC, "
            "and serves the final OpenAI-compatible HTTP endpoint."
        )
        instructions["commands"] = (
            f"# 1. Install build tools\n"
            f"sudo apt update && sudo apt install -y build-essential cmake git curl\n\n"
            f"# 2. Build llama.cpp with RPC client support\n"
            f"git clone https://github.com/ggerganov/llama.cpp.git\n"
            f"cd llama.cpp && cmake -B build -DGGML_RPC=ON && cmake --build build --config Release -j$(nproc)\n\n"
            f"# 3. Download target quantized Qwen model (e.g. Qwen2.5-7B-Instruct-Q4_K_M.gguf)\n"
            f"mkdir -p models\n"
            f"curl -L -o models/qwen2.5-7b-instruct-q4_k_m.gguf <download-url>\n\n"
            f"# 4. Launch the coordinator server pointing to RPC worker nodes:\n"
            f"./build/bin/llama-server -m models/qwen2.5-7b-instruct-q4_k_m.gguf \\\n"
            f"  --rpc <worker1_ip>:50052,<worker2_ip>:50052 \\\n"
            f"  --host 0.0.0.0 --port 8080 -c 4096"
        )
        instructions["warning"] = "Ensure all worker nodes have identical llama.cpp build commits to guarantee binary tensor compatibility."
        
    elif role == "standalone_server":
        instructions["summary"] = "Setup Standalone Inference Server"
        instructions["description"] = "Run a standalone Ollama or llama-server API endpoint directly on this machine."
        instructions["commands"] = (
            f"# Option A: Using Ollama\n"
            f"curl -fsSL https://ollama.com/install.sh | sh\n"
            f"ollama pull qwen2.5:3b\n"
            f"OLLAMA_HOST=0.0.0.0:11434 ollama serve\n\n"
            f"# Option B: Using llama.cpp server\n"
            f"./build/bin/llama-server -m models/qwen2.5-3b-instruct-q4_k_m.gguf --host 0.0.0.0 --port 8080"
        )
        instructions["warning"] = "Verify RAM consumption matches the selected quantization to avoid swapping."
        
    elif role == "vps_worker":
        instructions["summary"] = "Setup Companion VPS Execution Worker"
        instructions["description"] = "Run the isolated Qwen Collective companion worker script on the VPS."
        instructions["commands"] = (
            f"# 1. Copy worker/vps_worker.py to your VPS\n"
            f"# 2. Install requirements\n"
            f"pip install fastapi uvicorn httpx pydantic\n\n"
            f"# 3. Start worker with authentication token:\n"
            f"QC_WORKER_PORT=8081 QC_AUTH_TOKEN='<generate-token>' python vps_worker.py"
        )
        instructions["warning"] = "Do not expose worker to public internet without firewall or Tailscale."
        
    else:
        instructions["summary"] = "Unassigned Machine"
        instructions["description"] = "Assign a role in the machine settings to view tailored setup instructions."
        instructions["commands"] = "# Assign a role to generate configuration commands."
        instructions["warning"] = ""
        
    return instructions
