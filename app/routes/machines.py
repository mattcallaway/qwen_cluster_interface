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

router = APIRouter(tags=["machines"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/machines", response_class=HTMLResponse)
def machines_list(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    machines = get_all_machines()
    return templates.TemplateResponse(request=request, name="machines/list.html", context={
        "active_nav": "machines",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "machines": machines
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
