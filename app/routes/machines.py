import html
from typing import Optional
from fastapi import APIRouter, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user, get_csrf_token, verify_csrf
from app.services.machine_registry import (
    get_all_machines,
    get_machine_by_id,
    register_machine,
    update_machine_specs,
    update_machine_role,
    toggle_machine_status,
    get_machine_audits,
    generate_setup_instructions,
    log_machine_audit
)
from app.services.hardware_probe import test_tcp_reachability, probe_remote_ssh

import json
from app.database import get_db_connection

router = APIRouter(tags=["machines"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/machines", response_class=HTMLResponse)
def machines_list(request: Request, view: str = "active"):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    conn = get_db_connection()
    try:
        # 1. Fetch controller-local identity
        ctrl_row = conn.execute("SELECT * FROM infrastructure_nodes WHERE id = 'controller-local'").fetchone()
        controller_node = dict(ctrl_row) if ctrl_row else None
        if controller_node:
            controller_node["installed_runtimes"] = json.loads(controller_node.get("installed_runtimes_json") or "[]")
            controller_node["available_models"] = json.loads(controller_node.get("available_models_json") or "[]")

        # 2. Fetch active tasks per worker
        active_tasks = {}
        for r in conn.execute(
            """SELECT l.worker_id, t.id as task_id, t.title as task_title, t.task_type, l.status as lease_status
               FROM worker_task_leases l
               JOIN tasks t ON t.id = l.task_id
               WHERE l.status IN ('leased', 'active')"""
        ).fetchall():
            active_tasks[r["worker_id"]] = dict(r)

        # 3. Fetch models, runtimes, and enablement from infrastructure_nodes for machines
        infra_map = {}
        for r in conn.execute(
            """SELECT id, name, available_models_json, installed_runtimes_json,
                      capability_restrictions_json, last_contact_at, notes,
                      ip_address, hostname, role, is_enabled, health_status
               FROM infrastructure_nodes"""
        ).fetchall():
            infra_map[r["id"]] = {
                "name": r["name"],
                "models": json.loads(r["available_models_json"] or "[]"),
                "runtimes": json.loads(r["installed_runtimes_json"] or "[]"),
                "restrictions": json.loads(r["capability_restrictions_json"] or "{}"),
                "last_contact_at": r["last_contact_at"],
                "notes": r["notes"] or "",
                "ip_address": r["ip_address"] or "",
                "hostname": r["hostname"] or "",
                "role": r["role"] or "",
                "is_enabled": bool(r["is_enabled"]),
                "health_status": r["health_status"] or "unknown",
            }

        # 4. Fetch valid token set
        tokens = {r["node_id"] for r in conn.execute("SELECT DISTINCT node_id FROM worker_auth_tokens WHERE revoked = 0").fetchall()}
    finally:
        conn.close()

    all_active = get_all_machines(include_retired=False)
    active_machines = [m for m in all_active if m["id"] != "controller-local"]

    for m in active_machines:
        m_id = m["id"]
        inf = infra_map.get(m_id, {})
        m["available_models"] = inf.get("models", [])
        m["installed_runtimes"] = inf.get("runtimes", [])
        m["current_task"] = active_tasks.get(m_id)
        m["is_authenticated"] = m_id in tokens

        # Administrative enablement strictly requires consistency across both registries
        infra_enabled = bool(inf.get("is_enabled", False))
        mach_enabled = bool(m.get("is_enabled", False))
        m["is_admin_enabled"] = mach_enabled and infra_enabled

        # Actually scheduler-eligible requires:
        # 1. Consistent administrative enablement across both machines and infrastructure_nodes
        # 2. Currently reachable (online)
        # 3. Authenticated with unrevoked credential
        # 4. Non-retired canonical record
        m["is_scheduler_eligible"] = (
            m["is_admin_enabled"]
            and m["connection_status"] == "online"
            and (m_id in tokens)
            and not str(m.get("notes", "")).startswith("[RETIRED")
        )

    all_records = get_all_machines(include_retired=True)
    retired_machines = [m for m in all_records if str(m.get("notes", "")).startswith("[RETIRED") or m.get("connection_status") == "retired"]
    retired_ids = {m["id"] for m in retired_machines}
    for inf_id, inf_data in infra_map.items():
        if inf_id not in retired_ids and str(inf_data.get("notes", "")).startswith("[RETIRED"):
            retired_machines.append({
                "id": inf_id,
                "display_name": inf_data.get("name") or inf_id,
                "hostname": inf_data.get("hostname", ""),
                "ip_address": inf_data.get("ip_address", ""),
                "role": inf_data.get("role", "fixture"),
                "connection_status": "retired",
                "is_enabled": 0,
                "notes": inf_data.get("notes", ""),
                "last_checked_at": inf_data.get("last_contact_at"),
            })

    # Fleet KPI metrics: 5 distinct operational and authorization dimensions
    metrics = {
        "registered_physical": len(active_machines),
        "currently_reachable": sum(1 for m in active_machines if m["connection_status"] == "online"),
        "administratively_enabled": sum(1 for m in active_machines if m.get("is_admin_enabled")),
        "scheduler_eligible": sum(1 for m in active_machines if m.get("is_scheduler_eligible")),
        "executing_work": len(active_tasks),
        "retired_count": len(retired_machines),
    }

    return templates.TemplateResponse(request=request, name="machines/list.html", context={
        "active_nav": "machines",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "view": view,
        "machines": active_machines if view == "active" else retired_machines,
        "controller_node": controller_node,
        "metrics": metrics,
    })

@router.get("/machines/add", response_class=HTMLResponse)
def add_machine_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(request=request, name="machines/add_wizard.html", context={
        "active_nav": "machines",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
    })

@router.post("/machines/add")
def add_machine_submit(
    request: Request,
    csrf_token: str = Form(...),
    display_name: str = Form(...),
    ip_address: str = Form(...),
    hostname: Optional[str] = Form(None),
    connection_type: str = Form("ssh"),
    role: str = Form("unassigned"),
    ram_total_mb: Optional[int] = Form(None),
    cpu_info: Optional[str] = Form(None),
    notes: Optional[str] = Form("")
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/machines", status_code=status.HTTP_302_FOUND)

    try:
        new_node = register_machine(
            display_name=display_name,
            ip_address=ip_address,
            hostname=hostname,
            connection_type=connection_type,
            role=role,
            ram_total_mb=ram_total_mb,
            cpu_info=cpu_info,
            notes=notes or ""
        )
        return RedirectResponse(url=f"/machines/{new_node['id']}", status_code=status.HTTP_302_FOUND)
    except ValueError as e:
        machines = get_all_machines()
        return templates.TemplateResponse(request=request, name="machines/list.html", context={
            "active_nav": "machines",
            "current_user": user,
            "csrf_token": get_csrf_token(request),
            "machines": machines,
            "alert": {"type": "error", "message": str(e)}
        })

@router.get("/machines/{machine_id}", response_class=HTMLResponse)
def machine_detail(request: Request, machine_id: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    machine = get_machine_by_id(machine_id)
    if not machine:
        return RedirectResponse(url="/machines", status_code=status.HTTP_302_FOUND)

    audits = get_machine_audits(machine_id)
    instructions = generate_setup_instructions(machine)

    return templates.TemplateResponse(request=request, name="machines/detail.html", context={
        "active_nav": "machines",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "machine": machine,
        "audits": audits,
        "instructions": instructions
    })

@router.post("/machines/{machine_id}/update-role")
def machine_update_role(
    request: Request,
    machine_id: str,
    csrf_token: str = Form(...),
    role: str = Form(...),
    notes: Optional[str] = Form("")
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url=f"/machines/{machine_id}", status_code=status.HTTP_302_FOUND)

    update_machine_role(machine_id, role, notes)
    return RedirectResponse(url=f"/machines/{machine_id}", status_code=status.HTTP_302_FOUND)

@router.post("/machines/{machine_id}/toggle-status")
def machine_toggle(request: Request, machine_id: str, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url=f"/machines/{machine_id}", status_code=status.HTTP_302_FOUND)

    machine = get_machine_by_id(machine_id)
    if machine:
        toggle_machine_status(machine_id, not bool(machine["is_enabled"]))
    return RedirectResponse(url=f"/machines/{machine_id}", status_code=status.HTTP_302_FOUND)

@router.post("/machines/{machine_id}/test-connection")
def machine_ping(request: Request, machine_id: str, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url=f"/machines/{machine_id}", status_code=status.HTTP_302_FOUND)

    machine = get_machine_by_id(machine_id)
    if machine:
        is_up, msg = test_tcp_reachability(machine["ip_address"], port=22, timeout=2.5)
        status_val = "online" if is_up else "offline"
        update_machine_specs(machine_id, status=status_val)
        log_machine_audit(machine_id, "CONNECTIVITY_TEST", f"Result: {status_val}. {msg}")
    return RedirectResponse(url=f"/machines/{machine_id}", status_code=status.HTTP_302_FOUND)

@router.post("/machines/{machine_id}/inspect-ssh")
def machine_inspect_ssh(
    request: Request,
    machine_id: str,
    csrf_token: str = Form(...),
    ssh_user: str = Form(...),
    ssh_port: int = Form(22)
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url=f"/machines/{machine_id}", status_code=status.HTTP_302_FOUND)

    machine = get_machine_by_id(machine_id)
    if machine:
        res = probe_remote_ssh(machine["ip_address"], username=ssh_user, port=ssh_port)
        if res.get("success"):
            update_machine_specs(
                machine_id=machine_id,
                os_info=res.get("os_info"),
                cpu_info=res.get("cpu_info"),
                ram_total_mb=res.get("ram_total_mb"),
                ram_avail_mb=res.get("ram_avail_mb"),
                disk_info=res.get("disk_info"),
                network_info=res.get("network_info"),
                status="online"
            )
        else:
            log_machine_audit(machine_id, "INSPECTION_FAILED", res.get("error", "Unknown error"))
    return RedirectResponse(url=f"/machines/{machine_id}", status_code=status.HTTP_302_FOUND)

# HTMX Probe Endpoints (Authenticated & CSRF Protected)
@router.post("/api/machines/probe/ping", response_class=HTMLResponse)
def htmx_probe_ping(request: Request, csrf_token: str = Form(""), ip_address: str = Form(""), ssh_port: int = Form(22)):
    user = get_current_user(request)
    if not user:
        return "<div class='alert alert-error'>Authentication required to perform network probes.</div>"
    if not verify_csrf(request, csrf_token):
        return "<div class='alert alert-error'>Invalid or missing CSRF token.</div>"

    if not ip_address.strip():
        return "<div class='alert alert-warning'>Please enter a network address first.</div>"
    is_up, msg = test_tcp_reachability(ip_address.strip(), port=ssh_port, timeout=2.5)
    escaped_msg = html.escape(str(msg))
    if is_up:
        return f"<div class='alert alert-success'><strong>Reachable:</strong> {escaped_msg}</div>"
    else:
        return f"<div class='alert alert-error'><strong>Unreachable:</strong> {escaped_msg}</div>"

@router.post("/api/machines/probe/ssh", response_class=HTMLResponse)
def htmx_probe_ssh(
    request: Request,
    csrf_token: str = Form(""),
    ip_address: str = Form(""),
    ssh_user: str = Form(""),
    ssh_port: int = Form(22),
    ssh_key_path: Optional[str] = Form(None)
):
    user = get_current_user(request)
    if not user:
        return "<div class='alert alert-error'>Authentication required to perform hardware probes.</div>"
    if not verify_csrf(request, csrf_token):
        return "<div class='alert alert-error'>Invalid or missing CSRF token.</div>"

    if not ip_address.strip() or not ssh_user.strip():
        return "<div class='alert alert-warning'>Please provide both network address and SSH username.</div>"
    res = probe_remote_ssh(ip_address.strip(), username=ssh_user.strip(), key_path=ssh_key_path, port=ssh_port)
    if res.get("success"):
        rec = res.get("recommendation", {})
        ram_total = html.escape(str(res.get("ram_total_mb", "Unknown")))
        ram_avail = html.escape(str(res.get("ram_avail_mb", "?")))
        os_info = html.escape(str(res.get("os_info", "")))
        cpu_info = html.escape(str(res.get("cpu_info", "")))
        disk_info = html.escape(str(res.get("disk_info", "")))
        explanation = html.escape(str(rec.get("explanation", "")))
        recommended_role = html.escape(str(rec.get("recommended_role", "")))
        return f"""
        <div class='alert alert-success'>
          <strong>SSH Inspection Successful:</strong><br>
          OS: {os_info}<br>
          CPU: {cpu_info}<br>
          RAM: {ram_total} MB (~{ram_avail} MB available)<br>
          Disks: {disk_info}
        </div>
        <div style='background: #e0f2fe; padding: 12px; border-radius: 6px; border: 1px solid #bae6fd; font-size: 0.85rem;'>
          <strong>Advisory Recommendation:</strong> {explanation}<br>
          <em>Suggested Role:</em> <strong>{recommended_role}</strong>
        </div>
        """
    else:
        err = html.escape(str(res.get("error", "Unknown error")))
        return f"<div class='alert alert-error'><strong>SSH Inspection Failed:</strong> {err}</div>"
