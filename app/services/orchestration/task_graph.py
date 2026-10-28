"""Plan schema, plan-quality validation, DAG utilities and prompt-injection screening.

A plan is *data*, never instructions. Only whitelisted action types are accepted; unknown or risky
content is rejected before a plan can be shown for approval, and again before execution.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.services.deliverables import DELIVERABLE_TYPES
from app.services.orchestration import script_library

PLAN_SCHEMA = "qc.plan.v1"
MAX_TASKS = 30
MAX_STAGES = 8
MAX_PROMPT_CHARS = 2500
AGENT_ROLES = ("project_manager", "research", "analysis", "development", "execution", "review",
               "integration", "recovery")
ACTION_TYPES = ("library_script", "inference", "controller_step", "research_fetch")
CONTROLLER_STEPS = (
    "assemble_report",
    "research_search",
    "research_fetch",
    "research_browser_read",
    "research_extract",
    "research_verify",
    "research_coverage",
    "research_synthesize",
    "research_citation_audit",
    "research_full_cycle",
    "pipeline_kv_diagnostics",
)
ACCEPTANCE_KINDS = (
    "script_validator",
    "nonempty_text",
    "json_fields",
    "report_sections",
    "research_evidence",
    "citation_audit",
)
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-/]{0,63}$")

# Heuristic screening only (defence in depth: structure/allow-lists are the primary control).
INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|rules)",
    r"disregard (the )?(system|previous) (prompt|instructions)",
    r"you are now\b", r"system prompt", r"developer message",
    r"(disable|bypass|skip|override|turn off) (the )?(approval|safety|sandbox|security|authentication|checks?)",
    r"reveal .{0,30}(password|token|secret|credential|api key)",
    r"\bcat\s+/etc/(shadow|passwd)", r"\bcurl\b[^\n]{0,80}\|\s*(ba)?sh", r"\brm\s+-rf\b",
    r"(chmod|chown)\s+[0-7]{3,4}\b", r"\bsudo\b", r"authorized_keys", r"BEGIN [A-Z ]*PRIVATE KEY",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]
URL_RE = re.compile(r"https?://", re.I)


def scan_text_for_injection(text: str) -> List[str]:
    return [p.pattern for p in _INJ if p.search(text or "")]


def _walk_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)


# --- DAG ----------------------------------------------------------------------------------------------

def detect_cycle(deps: Dict[str, List[str]]) -> Optional[List[str]]:
    """Kahn's algorithm. Returns the keys involved in a cycle, or None if acyclic."""
    indeg = {k: 0 for k in deps}
    rev: Dict[str, List[str]] = {k: [] for k in deps}
    for k, ds in deps.items():
        for d in ds:
            if d in deps:
                indeg[k] += 1
                rev[d].append(k)
    queue = [k for k, n in indeg.items() if n == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for m in rev[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if seen == len(deps):
        return None
    return sorted(k for k, n in indeg.items() if n > 0)


def topo_order(deps: Dict[str, List[str]]) -> List[str]:
    if detect_cycle(deps):
        raise ValueError("cycle")
    done: List[str] = []
    placed: Set[str] = set()
    remaining = dict(deps)
    while remaining:
        layer = sorted(k for k, ds in remaining.items() if all(d in placed for d in ds))
        for k in layer:
            placed.add(k)
            done.append(k)
            remaining.pop(k)
    return done


def ready_keys(deps: Dict[str, List[str]], accepted: Set[str], active: Set[str]) -> List[str]:
    """Tasks whose prerequisites are all accepted and that are not already finished/in flight."""
    return sorted(k for k, ds in deps.items() if k not in accepted and k not in active
                  and all(d in accepted for d in ds))


# --- normalisation -------------------------------------------------------------------------------------

def _s(v: Any, limit: int = 2000) -> str:
    return str(v if v is not None else "").strip()[:limit]


def _list_s(v: Any, limit: int = 20, each: int = 300) -> List[str]:
    if not isinstance(v, list):
        return []
    return [_s(x, each) for x in v[:limit] if _s(x, each)]


DELIVERABLE_TYPE_ALIASES = {
    "doc": "document", "docs": "document",
    "response": "document", "text": "document", "summary": "document",
    "confirmation": "document", "file": "document", "plan": "document",
    "channel": "other", "service": "other", "deployment": "software_artifact",
    "app": "software_artifact", "code": "software_artifact", "data": "dataset",
    "research": "research_result", "analysis": "research_result",
}


def normalize_plan(plan: Dict[str, Any], nodes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Coerce a loosely-shaped plan dict into the canonical structure (does not judge validity)."""
    if not isinstance(plan, dict):
        return {}
    ov = plan.get("overview") if isinstance(plan.get("overview"), dict) else {}
    out: Dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "overview": {
            "objective": _s(ov.get("objective")), "scope": _s(ov.get("scope")),
            "assumptions": _list_s(ov.get("assumptions")), "constraints": _list_s(ov.get("constraints")),
            "expected_outcome": _s(ov.get("expected_outcome")),
        },
        "paths": [], "tasks": [], "deliverables": [],
        "risks": [], "approval_gates": [],
    }
    for p in (plan.get("paths") or []) if isinstance(plan.get("paths"), list) else []:
        if not isinstance(p, dict):
            continue
        stages = []
        for s in (p.get("stages") or []) if isinstance(p.get("stages"), list) else []:
            if isinstance(s, dict):
                stages.append({"key": _s(s.get("key"), 200), "name": _s(s.get("name"), 200),
                               "objective": _s(s.get("objective"))})
        out["paths"].append({"key": _s(p.get("key"), 200), "name": _s(p.get("name"), 200),
                             "rationale": _s(p.get("rationale")), "stages": stages})
    for t in (plan.get("tasks") or []) if isinstance(plan.get("tasks"), list) else []:
        if not isinstance(t, dict):
            continue
        task_key = _s(t.get("key"), 200)
        act = dict(t.get("action")) if isinstance(t.get("action"), dict) else {}
        if not act.get("type"):
            # Model may put action_type at task root, or as a string in 'action'
            cand_type = t.get("action_type") or t.get("type") or (t.get("action") if isinstance(t.get("action"), str) else None)
            if cand_type in ACTION_TYPES:
                act["type"] = cand_type
            elif t.get("script_id"):
                act["type"] = "library_script"
                act["script_id"] = t.get("script_id")
            elif t.get("step"):
                act["type"] = "controller_step"
                act["step"] = t.get("step")
            elif t.get("instruction"):
                act["type"] = "inference"
                act["instruction"] = _s(t.get("instruction"))

        # Preserve declared action inputs verbatim without truncation or discarding
        if isinstance(act.get("inputs"), list):
            act["inputs"] = [_s(inp, 200) for inp in act["inputs"] if _s(inp, 200)]

        # Preserve declared depends_on verbatim without truncation, slicing, or auto-injection
        if isinstance(t.get("depends_on"), list):
            deps_list = [_s(dep, 200) for dep in t.get("depends_on") if _s(dep, 200)]
        else:
            deps_list = []

        # Preserve agent_role verbatim without truncation or picking tokens
        role = _s(t.get("agent_role"), 200) or "execution"

        acc = t.get("acceptance") if isinstance(t.get("acceptance"), dict) else {}
        allowed = _list_s(t.get("allowed_nodes"), 8, 64)
        if act.get("type") == "inference":
            if nodes is not None:
                inf_nodes = {n["id"] for n in nodes if "inference" in n.get("capabilities", [])}
                allowed = [n for n in allowed if n in inf_nodes]
                if len(allowed) <= 1:
                    allowed = []
            else:
                allowed = []
        elif act.get("type") == "controller_step":
            allowed = []
        elif act.get("type") == "library_script" and nodes is not None and allowed:
            py_nodes = {n["id"] for n in nodes if "python_execution" in n.get("capabilities", [])}
            allowed = [n for n in allowed if n in py_nodes]

        out["tasks"].append({
            "key": task_key, "title": _s(t.get("title"), 200),
            "description": _s(t.get("description")), "stage": _s(t.get("stage"), 200),
            "agent_role": role,
            "action": act, "acceptance": acc,
            "depends_on": deps_list,
            "required_capabilities": _list_s(t.get("required_capabilities"), 10, 60),
            "preferred_models": _list_s(t.get("preferred_models"), 5, 64),
            "allowed_nodes": allowed,
            "priority": t.get("priority") if isinstance(t.get("priority"), int) else 3,
            "max_attempts": t.get("max_attempts") if isinstance(t.get("max_attempts"), int) else 2,
            "timeout_seconds": t.get("timeout_seconds") if isinstance(t.get("timeout_seconds"), int) else 300,
            "expected_outputs": _list_s(t.get("expected_outputs"), 10, 200),
            "approval": "required" if _s(t.get("approval")) == "required" else "auto",
            "risk": _s(t.get("risk"), 10) if _s(t.get("risk"), 10) in ("low", "medium", "high") else "low",
        })
    for d in (plan.get("deliverables") or []) if isinstance(plan.get("deliverables"), list) else []:
        if isinstance(d, dict):
            raw_type = _s(d.get("type"), 200).lower()
            if raw_type in DELIVERABLE_TYPE_ALIASES:
                raw_type = DELIVERABLE_TYPE_ALIASES[raw_type]
            # If raw_type is not recognized, preserve verbatim (do NOT default to document)
            raw_prod = _s(d.get("produced_by"), 200)
            if "," in raw_prod:
                parts = [x.strip() for x in raw_prod.split(",") if x.strip()]
                raw_prod = parts[-1] if parts else raw_prod
            out["deliverables"].append({
                "key": _s(d.get("key"), 200), "name": _s(d.get("name"), 200), "type": raw_type,
                "description": _s(d.get("description")), "acceptance_criteria": _s(d.get("acceptance_criteria")),
                "produced_by": raw_prod, "stage": _s(d.get("stage"), 200),
                "terminal": bool(d.get("terminal")),
            })
    for r in (plan.get("risks") or [])[:15] if isinstance(plan.get("risks"), list) else []:
        if isinstance(r, dict):
            out["risks"].append({"description": _s(r.get("description"), 400), "mitigation": _s(r.get("mitigation"), 400)})
    for g in (plan.get("approval_gates") or [])[:15] if isinstance(plan.get("approval_gates"), list) else []:
        if isinstance(g, dict):
            out["approval_gates"].append({"action": _s(g.get("action"), 200), "reason": _s(g.get("reason"), 300)})
    return out


def auto_repair_plan(plan: Dict[str, Any], errors: List[str], nodes: Optional[List[Dict[str, Any]]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """Deterministically repair provably safe syntactic defects in a candidate plan.

    CRITICAL STAGE 2 SAFETY INVARIANTS:
    - Never silently convert unknown library scripts or controller steps to 'inference' or 'assemble_report'.
    - Never delete edges or self-references to resolve dependency cycles or invalid dependencies.
    - Never delete dependencies on missing tasks or silently inject inputs into depends_on.
    - Never select one role from a pipe-delimited role expression or mutate execution roles.
    - Never silently convert unknown deliverable types to 'document' (unknown types must produce UNSUPPORTED_DELIVERABLE_TYPE).
    - Never insert generic acceptance criteria such as 'Validated output meets project requirements.' (missing criteria must produce MISSING_ACCEPTANCE_CRITERIA).
    - Never arbitrarily mark the last deliverable terminal (terminal status must follow validated requirements or fail with MISSING_TERMINAL_DELIVERABLE).
    - Deterministic repair is strictly limited to provably safe, explicit alias mappings:
      normalizing explicit deliverable aliases in DELIVERABLE_TYPE_ALIASES (e.g. 'doc' -> 'document', 'code' -> 'software_artifact').
    - Any defect in dependencies, inputs, actions, capabilities, roles, deliverable types, criteria, or terminals is preserved verbatim
      and caught as an explicit validation error for operator visibility.

    Returns (repaired_plan, remaining_errors) after full re-validation.
    """
    import copy
    repaired = copy.deepcopy(plan) if isinstance(plan, dict) else {}

    # Deliverables explicit alias normalization ONLY
    dels = repaired.get("deliverables", [])
    if isinstance(dels, list):
        for d in dels:
            if not isinstance(d, dict):
                continue
            dt = str(d.get("type") or "").strip().lower()
            if dt in DELIVERABLE_TYPE_ALIASES:
                d["type"] = DELIVERABLE_TYPE_ALIASES[dt]
            # Unknown types preserved verbatim so validate_plan catches UNSUPPORTED_DELIVERABLE_TYPE
            # Empty acceptance_criteria is NOT populated with generic strings
            # Terminal status is NOT arbitrarily forced

    norm = normalize_plan(repaired, nodes)
    v = validate_plan(norm, nodes)
    return norm, v["errors"]


# --- validation ------------------------------------------------------------------------------------------

def validate_plan(plan: Dict[str, Any], nodes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Return ``{valid, errors, warnings, capability_issues, high_risk_tasks, estimate}``."""
    errors: List[str] = []
    warnings: List[str] = []
    capability_issues: List[str] = []
    high_risk: List[str] = []

    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        return {"valid": False, "errors": ["Plan is missing or has the wrong schema."], "warnings": [],
                "capability_issues": [], "high_risk_tasks": [], "estimate": {}}

    for s in _walk_strings(plan):
        hits = scan_text_for_injection(s)
        if hits:
            errors.append(f"Possible prompt-injection / privileged-instruction content rejected: '{s[:80]}'")
            break

    ov = plan["overview"]
    if not ov.get("objective"):
        errors.append("overview.objective is required.")

    paths = plan.get("paths", [])
    if not paths:
        errors.append("At least one path is required.")
    stage_keys: Set[str] = set()
    if paths:
        main = paths[0]
        if not main["name"] or not main["rationale"]:
            errors.append("The primary path needs a name and rationale.")
        if not main["stages"]:
            errors.append("The primary path needs at least one stage.")
        if len(main["stages"]) > MAX_STAGES:
            errors.append(f"At most {MAX_STAGES} stages are allowed.")
        for s in main["stages"]:
            sk = s["key"]
            if len(sk) > 40 or not KEY_RE.match(sk):
                errors.append(f"Stage key '{sk}' violates schema limits (exceeds 40 characters or invalid format; length={len(sk)}).")
            elif sk in stage_keys:
                errors.append(f"Duplicate stage key '{sk}'.")
            stage_keys.add(sk)
            if not s["name"]:
                errors.append(f"Stage '{sk}' needs a name.")
        if len(paths) > 1:
            warnings.append("Only the first path is executable; other paths are recorded as proposed alternatives.")

    tasks = plan.get("tasks", [])
    if not tasks:
        errors.append("At least one task is required.")
    if len(tasks) > MAX_TASKS:
        errors.append(f"At most {MAX_TASKS} tasks are allowed.")
    keys: Set[str] = set()
    deps: Dict[str, List[str]] = {}
    by_key: Dict[str, Dict[str, Any]] = {}
    for t in tasks:
        k = t["key"]
        if len(k) > 40 or not KEY_RE.match(k):
            errors.append(f"Invalid task key '{k}' (violates schema limits: exceeds 40 characters or invalid format; length={len(k)}).")
            continue
        if k in keys:
            errors.append(f"Duplicate task key '{k}'.")
            continue
        keys.add(k)
        by_key[k] = t
        deps[k] = list(t["depends_on"])
        if len(t["depends_on"]) > 20:
            errors.append(f"Task '{k}' has {len(t['depends_on'])} dependencies, exceeding maximum limit of 20 (SCHEMA_LIMIT_EXCEEDED).")
        for dep in t["depends_on"]:
            if len(dep) > 40 or not KEY_RE.match(dep):
                errors.append(f"Task '{k}' dependency identifier '{dep}' violates schema limits (exceeds 40 characters or invalid format; length={len(dep)}).")
        if not t["title"]:
            errors.append(f"Task '{k}' needs a title.")
        if not t["description"]:
            errors.append(f"Task '{k}' needs a clear objective (description).")
        if t["stage"] not in stage_keys:
            errors.append(f"Task '{k}' references unknown stage '{t['stage']}'.")
        if len(t["agent_role"]) > 40 or t["agent_role"] not in AGENT_ROLES:
            errors.append(f"Task '{k}' has unknown agent role '{t['agent_role']}' (length={len(t['agent_role'])}). Allowed roles: {sorted(AGENT_ROLES)}.")
        if not 1 <= t["priority"] <= 5:
            errors.append(f"Task '{k}' priority must be 1..5.")
        if not 1 <= t["max_attempts"] <= 5:
            errors.append(f"Task '{k}' max_attempts must be 1..5.")
        if not 5 <= t["timeout_seconds"] <= 1800:
            errors.append(f"Task '{k}' timeout_seconds must be 5..1800.")
        for m in (t.get("preferred_models") or []):
            if not MODEL_RE.match(m):
                errors.append(f"Task '{k}' has an invalid model name.")
        _validate_action(t, errors)
        _validate_acceptance(t, errors)
        if t["risk"] == "high" or t["approval"] == "required":
            high_risk.append(k)
    for k, ds in deps.items():
        for d in ds:
            if d == k:
                errors.append(f"Task '{k}' depends on itself.")
            elif d not in keys:
                errors.append(f"Task '{k}' depends on unknown task '{d}'.")
    for k, t in by_key.items():
        for inp in (t["action"].get("inputs") or []):
            if inp == k:
                errors.append(f"Task '{k}' uses output of itself ('{inp}') as an input.")
            elif inp not in keys:
                errors.append(f"Task '{k}' uses output of unknown task '{inp}'.")
            elif inp not in deps.get(k, []):
                errors.append(f"Task '{k}' uses output of '{inp}' without declaring it as a dependency.")
    cyc = detect_cycle({k: [d for d in v if d in keys] for k, v in deps.items()}) if deps else None
    if cyc:
        errors.append(f"Dependency cycle among tasks: {', '.join(cyc)}.")

    dels = plan.get("deliverables", [])
    if not dels:
        errors.append("At least one deliverable is required.")
    dkeys: Set[str] = set()
    for d in dels:
        dk = d["key"]
        if len(dk) > 40 or not KEY_RE.match(dk):
            errors.append(f"Deliverable key '{dk}' violates schema limits (exceeds 40 characters or invalid format; length={len(dk)}).")
            continue
        if dk in dkeys:
            errors.append(f"Duplicate deliverable key '{dk}'.")
            continue
        dkeys.add(dk)
        if d["type"] not in DELIVERABLE_TYPES:
            errors.append(f"Deliverable '{dk}' has unsupported type '{d['type']}' (UNSUPPORTED_DELIVERABLE_TYPE). Allowed types: {sorted(DELIVERABLE_TYPES)}.")
        if not d["name"]:
            errors.append(f"Deliverable '{dk}' needs a name.")
        if not d["acceptance_criteria"]:
            errors.append(f"Deliverable '{dk}' has no acceptance criteria (MISSING_ACCEPTANCE_CRITERIA).")
        if len(d["produced_by"]) > 40 or not KEY_RE.match(d["produced_by"]):
            errors.append(f"Deliverable '{dk}' produced_by identifier '{d['produced_by']}' violates schema limits (exceeds 40 characters or invalid format; length={len(d['produced_by'])}).")
        elif d["produced_by"] not in keys:
            errors.append(f"Deliverable '{dk}' is produced by unknown task '{d['produced_by']}'.")
        if d["stage"] and d["stage"] not in stage_keys:
            errors.append(f"Deliverable '{dk}' references unknown stage '{d['stage']}'.")
        # Terminal deliverable validation:
        if d.get("terminal"):
            prod_task = d["produced_by"]
            if any(prod_task in other.get("depends_on", []) for other in tasks):
                errors.append(f"Deliverable '{dk}' marked terminal is produced by intermediate task '{prod_task}', which has downstream dependent tasks (INCORRECT_TERMINAL_DELIVERABLE).")

    if dels and not any(d.get("terminal") for d in dels):
        errors.append("No terminal (final) deliverable is marked (MISSING_TERMINAL_DELIVERABLE).")

    # Schema placeholder rejection (F-14): Model copying prompt schema example verbatim
    _schema_placeholders = {
        "task 1 name", "task 2 name", "task 3 name",
        "high-level goal description", "stage 1 title", "stage 2 title",
        "deliverable 1 name", "deliverable 2 name",
    }
    obj_str = str(ov.get("objective", "")).strip().lower()
    if obj_str in _schema_placeholders or "high-level goal description" in obj_str:
        errors.append("Plan overview.objective is an unadapted schema placeholder literal.")
    for s in main.get("stages", []):
        if str(s.get("name", "")).strip().lower() in _schema_placeholders:
            errors.append(f"Stage '{s.get('key')}' uses schema placeholder literal: '{s.get('name')}'.")
    for t in tasks:
        t_title = str(t.get("title", "")).strip().lower()
        if t_title in _schema_placeholders or "task 1 name" in t_title or "task 2 name" in t_title:
            errors.append(f"Task '{t['key']}' uses prompt schema example placeholder: '{t.get('title')}'.")
    for d in dels:
        if str(d.get("name", "")).strip().lower() in _schema_placeholders:
            errors.append(f"Deliverable '{d['key']}' uses schema placeholder literal: '{d.get('name')}'.")

    if nodes is not None and not errors:
        from app.services.orchestration import model_router
        for t in tasks:
            cands = model_router.eligible_nodes(t, nodes, enforce_verified=False)
            if not cands["eligible"]:
                err = f"Task '{t['key']}' has no compatible node: {'; '.join(cands['reasons']) or 'unknown'}"
                capability_issues.append(err)
                errors.append(err)

    n_inf = sum(1 for t in tasks if t["action"].get("type") == "inference")
    estimate = {
        "task_count": len(tasks), "stage_count": len(stage_keys), "inference_requests": n_inf,
        "max_timeout_seconds_total": sum(t["timeout_seconds"] for t in tasks),
        "approx_tokens": sum(int(t["action"].get("max_tokens") or 0) + 400 for t in tasks
                             if t["action"].get("type") == "inference"),
        "needs_nodes": sorted({n for t in tasks for n in (t.get("allowed_nodes") or [])}),
    }
    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "capability_issues": capability_issues, "high_risk_tasks": high_risk, "estimate": estimate}


def _validate_action(t: Dict[str, Any], errors: List[str]) -> None:
    k, a = t["key"], t["action"]
    typ = a.get("type")
    if typ not in ACTION_TYPES:
        errors.append(f"Task '{k}' has disallowed action type '{typ}'.")
        return
    allowed_keys = ({"type","url","feedback_path_id"} if typ=='research_fetch' else
        {"type", "script_id", "model", "instruction", "inputs", "max_tokens", "step"})
    extra = set(a) - allowed_keys
    if extra:
        errors.append(f"Task '{k}' action has disallowed fields: {sorted(extra)}.")
    if typ == "library_script":
        if a.get("script_id") not in script_library.LIBRARY:
            errors.append(f"Task '{k}' references unknown script '{a.get('script_id')}'. Allowed library scripts: {sorted(script_library.LIBRARY.keys())}.")
        if not t["allowed_nodes"]:
            errors.append(f"Task '{k}' (library_script) must pin allowed_nodes.")
    elif typ == 'research_fetch':
        from urllib.parse import urlparse
        url=a.get('url')
        parsed=urlparse(url) if isinstance(url,str) else None
        if (not parsed or parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password):
            errors.append(f"Task '{k}' needs an HTTPS URL without credentials.")
        if not re.fullmatch(r'[a-f0-9]{64}',str(a.get('feedback_path_id',''))):
            errors.append(f"Task '{k}' needs a durable feedback proposal ID.")
        if not t['allowed_nodes'] or t.get('approval')!='required':
            errors.append(f"Task '{k}' requires pinned nodes and explicit task approval.")
        if t.get('max_attempts')!=1:
            errors.append(f"Task '{k}' retrieval must use one attempt until failed-observation accounting is implemented.")
    elif typ == "inference":
        ins = _s(a.get("instruction"), MAX_PROMPT_CHARS + 1)
        if not ins:
            errors.append(f"Task '{k}' inference action needs an instruction.")
        if len(ins) > MAX_PROMPT_CHARS:
            errors.append(f"Task '{k}' instruction exceeds {MAX_PROMPT_CHARS} characters.")
        if URL_RE.search(ins):
            errors.append(f"Task '{k}' instruction contains an external URL (unapproved destination).")
        mt = a.get("max_tokens", 200)
        if not isinstance(mt, int) or not 16 <= mt <= 1024:
            errors.append(f"Task '{k}' max_tokens must be an integer 16..1024.")
        if a.get("model") and not MODEL_RE.match(str(a["model"])):
            errors.append(f"Task '{k}' has an invalid model name.")
    elif typ == "controller_step":
        if a.get("step") not in CONTROLLER_STEPS:
            errors.append(f"Task '{k}' has unknown controller step '{a.get('step')}'. Allowed controller steps: {sorted(CONTROLLER_STEPS)}.")
    if a.get("inputs") is not None:
        if not isinstance(a.get("inputs"), list):
            errors.append(f"Task '{k}' inputs must be a list of task keys.")
        else:
            if len(a["inputs"]) > 20:
                errors.append(f"Task '{k}' action has {len(a['inputs'])} inputs, exceeding maximum limit of 20 (SCHEMA_LIMIT_EXCEEDED).")
            for inp in a["inputs"]:
                if len(inp) > 40 or not KEY_RE.match(inp):
                    errors.append(f"Task '{k}' action input '{inp}' violates schema limits (exceeds 40 characters or invalid format; length={len(inp)}).")


def _validate_acceptance(t: Dict[str, Any], errors: List[str]) -> None:
    k, acc = t["key"], t["acceptance"]
    kind = acc.get("kind")
    if kind is None:
        return  # defaulted from action type by the evaluator
    if kind not in ACCEPTANCE_KINDS:
        errors.append(f"Task '{k}' has unknown acceptance kind '{kind}'.")
    if kind == "script_validator" and t["action"].get("type") != "library_script":
        errors.append(f"Task '{k}': script_validator acceptance requires a library_script action.")
    if kind in ("report_sections", "research_evidence", "citation_audit") and t["action"].get("type") != "controller_step":
        errors.append(f"Task '{k}': {kind} acceptance requires a controller_step action.")
    if kind == "nonempty_text" and t["action"].get("type") != "inference":
        errors.append(f"Task '{k}': nonempty_text acceptance requires an inference action.")


def summarize_graph(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Nodes/edges for UI rendering."""
    return {"nodes": [{"key": t["key"], "title": t["title"], "stage": t["stage"],
                       "action": t["action"].get("type"), "allowed_nodes": t["allowed_nodes"]} for t in plan.get("tasks", [])],
            "edges": [{"from": d, "to": t["key"]} for t in plan.get("tasks", []) for d in t["depends_on"]]}
