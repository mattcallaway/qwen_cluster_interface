from typing import Optional, List
from fastapi import APIRouter, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import get_current_user, get_csrf_token, verify_csrf
from app.services.inference import (
    get_all_backends,
    register_inference_backend,
    test_inference_endpoint,
    update_backend_status,
    update_backend_tier_priority,
    get_all_distributed_groups,
    get_distributed_group_by_id,
    create_distributed_group,
    generate_distributed_launch_command,
    get_benchmarks,
    add_benchmark_result
)
from app.services.machine_registry import get_all_machines, get_machine_by_id

router = APIRouter(tags=["inference"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/inference", response_class=HTMLResponse)
def inference_index(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    backends = get_all_backends()
    groups = get_all_distributed_groups()
    benchmarks = get_benchmarks()
    machines = get_all_machines()

    return templates.TemplateResponse(request=request, name="inference/index.html", context={
        "active_nav": "inference",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "backends": backends,
        "distributed_groups": groups,
        "benchmarks": benchmarks,
        "machines": machines
    })

@router.post("/inference/backends/add")
async def add_backend(
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
    backend_type: str = Form(...),
    endpoint_url: str = Form(...),
    api_key: Optional[str] = Form(None)
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/inference", status_code=status.HTTP_302_FOUND)

    backend = register_inference_backend(
        name=name,
        backend_type=backend_type,
        endpoint_url=endpoint_url,
        api_key=api_key
    )

    # Immediately verify endpoint
    is_ready, model_name, err = await test_inference_endpoint(backend_type, endpoint_url, api_key)
    if is_ready:
        update_backend_status(backend["id"], "ready", model_name=model_name)
    else:
        update_backend_status(backend["id"], "error", error_details=err)

    return RedirectResponse(url="/inference", status_code=status.HTTP_302_FOUND)

@router.post("/inference/backends/{backend_id}/test")
async def test_backend(request: Request, backend_id: str, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/inference", status_code=status.HTTP_302_FOUND)

    backends = get_all_backends()
    target = next((b for b in backends if b["id"] == backend_id), None)
    if target:
        is_ready, model_name, err = await test_inference_endpoint(
            target["backend_type"], target["endpoint_url"], target.get("api_key")
        )
        if is_ready:
            update_backend_status(backend_id, "ready", model_name=model_name)
        else:
            update_backend_status(backend_id, "error", error_details=err)

    return RedirectResponse(url="/inference", status_code=status.HTTP_302_FOUND)

@router.post("/inference/backends/{backend_id}/configure")
def configure_backend(
    request: Request,
    backend_id: str,
    csrf_token: str = Form(...),
    tier: str = Form(...),
    priority: int = Form(...),
    is_active: int = Form(1),
    timeout_seconds: float = Form(30.0),
    description: Optional[str] = Form("")
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/inference", status_code=status.HTTP_302_FOUND)

    update_backend_tier_priority(
        backend_id=backend_id,
        tier=tier,
        priority=priority,
        is_active=is_active,
        timeout_seconds=timeout_seconds,
        description=description or ""
    )
    return RedirectResponse(url="/inference", status_code=status.HTTP_302_FOUND)

@router.post("/inference/groups/create")
def create_group(
    request: Request,
    csrf_token: str = Form(...),
    name: str = Form(...),
    coordinator_machine_id: str = Form(...),
    model_file: str = Form(...),
    rpc_endpoints_raw: str = Form(...),
    context_window: int = Form(4096),
    runtime_parameters: Optional[str] = Form(""),
    notes: Optional[str] = Form("")
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/inference", status_code=status.HTTP_302_FOUND)

    rpc_list = [ep.strip() for ep in rpc_endpoints_raw.split(",") if ep.strip()]
    group_id = create_distributed_group(
        name=name,
        coordinator_machine_id=coordinator_machine_id,
        model_file=model_file,
        rpc_endpoints=rpc_list,
        context_window=context_window,
        runtime_parameters=runtime_parameters or "",
        notes=notes or ""
    )
    return RedirectResponse(url=f"/inference/groups/{group_id}", status_code=status.HTTP_302_FOUND)

@router.get("/inference/groups/{group_id}", response_class=HTMLResponse)
def view_group(request: Request, group_id: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)

    group = get_distributed_group_by_id(group_id)
    if not group:
        return RedirectResponse(url="/inference", status_code=status.HTTP_302_FOUND)

    coordinator = get_machine_by_id(group["coordinator_machine_id"])
    launch_command = generate_distributed_launch_command(group)

    return templates.TemplateResponse(request=request, name="inference/group_detail.html", context={
        "active_nav": "inference",
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "group": group,
        "coordinator": coordinator,
        "launch_command": launch_command
    })

@router.post("/inference/benchmarks/add")
def record_benchmark(
    request: Request,
    csrf_token: str = Form(...),
    model_name: str = Form(...),
    quantization: str = Form(...),
    machines_involved: str = Form(...),
    gen_tokens_sec: float = Form(...),
    prompt_tokens_sec: float = Form(...),
    memory_usage_mb: Optional[float] = Form(None),
    notes: Optional[str] = Form("")
):
    user = get_current_user(request)
    if not user or not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/inference", status_code=status.HTTP_302_FOUND)

    add_benchmark_result(
        group_or_backend_name="Manual Entry",
        model_name=model_name,
        quantization=quantization,
        context_length=4096,
        machines_involved=machines_involved,
        prompt_tokens_sec=prompt_tokens_sec,
        gen_tokens_sec=gen_tokens_sec,
        memory_usage_mb=memory_usage_mb,
        notes=notes or ""
    )
    return RedirectResponse(url="/inference", status_code=status.HTTP_302_FOUND)
