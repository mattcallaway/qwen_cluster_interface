import json
import logging
import uuid
import httpx
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from app.database import get_db_connection

logger = logging.getLogger("qwen_collective.vps")

def get_vps_nodes() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vps_nodes ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["capabilities"] = json.loads(d.get("capabilities_json", "[]"))
        except Exception:
            d["capabilities"] = []
        results.append(d)
    return results

def get_vps_node_by_id(vps_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vps_nodes WHERE id = ?", (vps_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["capabilities"] = json.loads(d.get("capabilities_json", "[]"))
    except Exception:
        d["capabilities"] = []
    return d

from app.services.network_security import validate_endpoint_url

def register_vps_node(
    name: str,
    endpoint_url: str,
    auth_token: str,
    permission_policy: str = "approval_required"
) -> str:
    is_safe, reason = validate_endpoint_url(endpoint_url, allow_localhost=True)
    if not is_safe:
        raise ValueError(f"Invalid VPS endpoint: {reason}")

    vps_id = f"vps-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO vps_nodes (
        id, name, endpoint_url, auth_token,
        status, capabilities_json, permission_policy,
        created_at, updated_at
    ) VALUES (?, ?, ?, ?, 'unknown', '["health_check", "echo_ping", "network_probe"]', ?, ?, ?)
    """, (vps_id, name.strip(), endpoint_url.strip(), auth_token.strip(), permission_policy, now, now))
    conn.commit()
    conn.close()
    return vps_id

async def probe_vps_health(vps_id: str) -> Tuple[bool, List[str], str]:
    """
    Test VPS companion worker health and retrieve reported capabilities.
    Returns: (is_online, capabilities, status_message)
    """
    vps = get_vps_node_by_id(vps_id)
    if not vps:
        return False, [], "VPS node not found"
        
    url = vps["endpoint_url"].rstrip("/")
    token = vps["auth_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    conn = get_db_connection()
    now = datetime.now(timezone.utc).isoformat()
    
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            resp = await client.get(f"{url}/health", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                caps = data.get("capabilities", ["health_check", "echo_ping", "network_probe"])
                conn.execute("""
                UPDATE vps_nodes SET
                    status = 'online',
                    capabilities_json = ?,
                    last_checked_at = ?,
                    updated_at = ?
                WHERE id = ?
                """, (json.dumps(caps), now, now, vps_id))
                conn.commit()
                conn.close()
                return True, caps, "Online and healthy."
            elif resp.status_code == 401 or resp.status_code == 403:
                conn.execute("UPDATE vps_nodes SET status = 'error', last_checked_at = ?, updated_at = ? WHERE id = ?", (now, now, vps_id))
                conn.commit()
                conn.close()
                return False, [], "Authentication rejected. Invalid auth token."
            else:
                conn.execute("UPDATE vps_nodes SET status = 'error', last_checked_at = ?, updated_at = ? WHERE id = ?", (now, now, vps_id))
                conn.commit()
                conn.close()
                return False, [], f"VPS worker returned HTTP {resp.status_code}."
    except httpx.ConnectError:
        conn.execute("UPDATE vps_nodes SET status = 'offline', last_checked_at = ?, updated_at = ? WHERE id = ?", (now, now, vps_id))
        conn.commit()
        conn.close()
        return False, [], "Connection refused. VPS companion worker is offline."
    except httpx.TimeoutException:
        conn.execute("UPDATE vps_nodes SET status = 'offline', last_checked_at = ?, updated_at = ? WHERE id = ?", (now, now, vps_id))
        conn.commit()
        conn.close()
        return False, [], "Health check timed out."
    except Exception as e:
        conn.execute("UPDATE vps_nodes SET status = 'error', last_checked_at = ?, updated_at = ? WHERE id = ?", (now, now, vps_id))
        conn.commit()
        conn.close()
        return False, [], f"Health check failed: {str(e)}"

async def execute_vps_test_task(vps_id: str) -> Dict[str, Any]:
    """Execute a safe, harmless test task on the VPS companion worker."""
    vps = get_vps_node_by_id(vps_id)
    if not vps:
        return {"success": False, "error": "VPS node not found"}
        
    url = vps["endpoint_url"].rstrip("/")
    token = vps["auth_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            resp = await client.post(
                f"{url}/tasks/execute",
                json={"task_type": "echo_ping", "payload": {"message": "Qwen Collective probe"}},
                headers=headers
            )
            if resp.status_code == 200:
                data = resp.json()
                return {"success": True, "result": data}
            else:
                return {"success": False, "error": f"VPS responded with status {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"success": False, "error": f"Failed to execute VPS test task: {str(e)}"}
