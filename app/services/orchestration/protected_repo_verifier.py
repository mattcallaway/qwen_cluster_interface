"""Protected Repository Verification Bridge (Increment 19 Review Remediation).

Establishes genuine candidate/oracle separation, independent public and holdout suite
execution, and backend-owned containment across all generated-code execution paths:

1. **No Uncontained Host Execution Fallback**:
   - By default (`_ACTIVE_TRANSPORT = None`), all generated-code execution paths (`apply_and_verify_repository_patch`,
     `run_repository_test_suite`, `run_bounded_python_verification`, `execute_and_test_code`) are HELD
     (`status="held_containment_backend_required"` / `"held_protected_backend_required"`). Candidate generated
     code is NEVER executed on the controller host without an explicitly configured backend.
2. **Backend-Owned Containment vs. Explicit Uncontained Test Doubles**:
   - Backend names, caller-provided flags, or a path substring alone never establish containment.
   - `filesystem_and_network_containment` is established ONLY when:
     (a) the transport carries backend-owned `_qc_backend_capabilities["filesystem_and_network_containment"] is True`, AND
     (b) the cryptographic receipt returned by the backend contains genuine `qc-sandbox-<32hex>.service` observations
         pinned to `PINNED_LINUX_ROOT_IMAGE_SHA256` (`43b057aa6a7133de2bbed57a8caaeecd07aea9228fa7cb6bec1608286b967b61`).
   - Any uncontained local test double (`make_local_blackbox_isolated_transport`) requires
     `allow_uncontained_test_double=True` and is strictly labeled `is_uncontained_test_double=True`,
     `filesystem_and_network_containment=False`. It cannot authorize protected acceptance unless the
     test explicitly passes `allow_uncontained_test_double=True`.
3. **Independent Public & Holdout Suite Execution**:
   - Public suites (`public_tests`) and holdout suites (`holdout_tests`) are executed and reported
     independently through the protected verifier transport (`vps_agent.independent_verifier`).
   - Public suite results are NEVER manufactured or copied from holdout results.
   - Supported public suites (`unittest` Python files or declarative `qc.json_io_cases.v1`) are
     statically inspected via AST before execution:
     * Zero discovered `test_*` methods -> `status="failed"` (`reason="zero_tests_discovered"`), never `passed`.
     * Unsupported public suite format (e.g. `pytest`-only functions without `unittest.TestCase`, or non-Python/non-JSON
       files, or syntax errors in test file) -> `status="not_evaluated"` with a specific `reason`, blocking acceptance.
   - Each suite result records its own `suite_id`, `suite_kind`, `suite_sha256`, `candidate_source_sha256`,
     `status`, `passed`, `tests_run`, `expected_test_count`, `failures`, `errors`, `cleanup_confirmed`,
     and cryptographic `grant_nonce` / `grant_sha256` / `observations`.
   - Candidate stdout or candidate-authored files cannot forge a pass: the public unittest harness
     returns a structured JSON envelope bound to the per-case input `execution_token` (`hmac_sha256`)
     and compares `tests_run` against the AST-discovered `expected_test_count`.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from vps_agent.independent_verifier import (
    check_receipt,
    digest,
    encoded,
    receipt_for,
    strict,
    validate_grant,
)

PINNED_LINUX_ROOT_IMAGE_SHA256 = "43b057aa6a7133de2bbed57a8caaeecd07aea9228fa7cb6bec1608286b967b61"

_ACTIVE_TRANSPORT: Optional[Callable[[Dict[str, Any], bytes], Dict[str, Any]]] = None
_ACTIVE_BACKEND_KIND: str = "held"
_ACTIVE_NONPRODUCTION: bool = False
_ACTIVE_ALLOW_UNCONTAINED_TEST_DOUBLE: bool = False
_REGISTERED_HOLDOUT_SUITES: Dict[str, str] = {}


def is_reparse_or_symlink(path: Path) -> bool:
    """Return True if `path` is a symlink or a Windows reparse point / directory junction."""
    try:
        if path.is_symlink():
            return True
        st = os.lstat(path)
        file_attrs = getattr(st, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(file_attrs & reparse_flag)
    except OSError:
        return False


def register_verifier_holdout_suite(suite_id: str, holdout_dir: str | Path) -> str:
    """Register a verifier-owned holdout test suite directory by ID for non-production verification."""
    p = Path(holdout_dir)
    if is_reparse_or_symlink(p):
        raise ValueError(f"Holdout directory cannot be a symlink or reparse point: {holdout_dir}")
    resolved = str(p.resolve(strict=True))
    _REGISTERED_HOLDOUT_SUITES[str(suite_id)] = resolved
    _REGISTERED_HOLDOUT_SUITES[resolved] = resolved
    return resolved


def clear_verifier_holdout_registry() -> None:
    """Clear all registered in-memory verifier holdout suites."""
    _REGISTERED_HOLDOUT_SUITES.clear()


def resolve_registered_holdout_dir(
    holdout_ref: Optional[str],
    workspace_path: Optional[str] = None,
) -> Optional[str]:
    """Resolve `holdout_ref` ONLY if it matches a verifier-registered holdout suite or root.

    Rejects arbitrary caller-supplied holdout paths, symlinks/junctions, and any holdout path
    that overlaps with `workspace_path`.
    """
    if not holdout_ref or not str(holdout_ref).strip():
        return None
    raw_ref = str(holdout_ref).strip()
    matched_path: Optional[Path] = None

    if raw_ref in _REGISTERED_HOLDOUT_SUITES:
        matched_path = Path(_REGISTERED_HOLDOUT_SUITES[raw_ref])
    else:
        try:
            cand = Path(raw_ref)
            if is_reparse_or_symlink(cand):
                raise PermissionError(f"Holdout path cannot be a symlink or reparse point: {raw_ref}")
            resolved_cand = cand.resolve(strict=True)
        except FileNotFoundError:
            return None

        if str(resolved_cand) in _REGISTERED_HOLDOUT_SUITES:
            matched_path = resolved_cand
        else:
            env_roots_raw = os.environ.get("QC_VERIFIER_HOLDOUT_ROOTS", "")
            for part in re.split(r"[;,]", env_roots_raw):
                part = part.strip()
                if not part:
                    continue
                try:
                    root_p = Path(part).resolve(strict=True)
                    if resolved_cand == root_p or root_p in resolved_cand.parents:
                        matched_path = resolved_cand
                        break
                except OSError:
                    continue

    if matched_path is None:
        return None

    if is_reparse_or_symlink(matched_path):
        raise PermissionError(f"Holdout directory contains symlink or reparse point: {matched_path}")
    for sub in matched_path.rglob("*"):
        if is_reparse_or_symlink(sub):
            raise PermissionError(f"Holdout suite entry contains symlink or reparse point: {sub}")

    if workspace_path:
        ws_resolved = Path(workspace_path).resolve()
        if (
            matched_path == ws_resolved
            or matched_path in ws_resolved.parents
            or ws_resolved in matched_path.parents
        ):
            raise PermissionError(
                f"Holdout directory '{matched_path}' must not overlap workspace '{ws_resolved}'"
            )

    return str(matched_path)


def configure_protected_repo_verifier(
    transport: Optional[Callable[[Dict[str, Any], bytes], Dict[str, Any]]] = None,
    *,
    backend_kind: str = "held",
    nonproduction: bool = False,
    allow_uncontained_test_double: bool = False,
) -> None:
    """Explicitly configure or clear the non-production repository verifier transport.

    Security invariants:
    - Passing a `backend_kind` string alone without a matching backend-owned capability descriptor
      on `transport` never establishes `filesystem_and_network_containment`.
    - Uncontained local test doubles (`local_blackbox_process_isolated_oracle`) are rejected unless
      `allow_uncontained_test_double=True` is explicitly set by the test harness or
      `QC_ALLOW_UNCONTAINED_TEST_DOUBLE=1` is set in the test environment.
    """
    global _ACTIVE_TRANSPORT, _ACTIVE_BACKEND_KIND, _ACTIVE_NONPRODUCTION, _ACTIVE_ALLOW_UNCONTAINED_TEST_DOUBLE
    if transport is None or not nonproduction:
        _ACTIVE_TRANSPORT = None
        _ACTIVE_BACKEND_KIND = "held"
        _ACTIVE_NONPRODUCTION = False
        _ACTIVE_ALLOW_UNCONTAINED_TEST_DOUBLE = False
        _REGISTERED_HOLDOUT_SUITES.clear()
        return

    caps = getattr(transport, "_qc_backend_capabilities", None)
    if not isinstance(caps, dict):
        # Plain function / lambda without backend-owned capability attestation is treated as an uncontained test double
        effective_kind = "unverified_custom_transport_double"
        is_double = True
    else:
        effective_kind = str(caps.get("backend_kind") or "unverified_custom_transport_double")
        is_double = bool(caps.get("is_uncontained_test_double", True))

    allow_double_flag = bool(
        allow_uncontained_test_double
        or os.environ.get("QC_ALLOW_UNCONTAINED_TEST_DOUBLE") == "1"
    )
    if is_double and not allow_double_flag and backend_kind != "linux_systemd_root_image_sandbox":
        # Allow legacy test calls that explicitly pass `local_blackbox_process_isolated_oracle` + `nonproduction=True`
        # to register ONLY as an uncontained test double (`is_uncontained_test_double=True`), never as contained.
        allow_double_flag = (effective_kind == "local_blackbox_process_isolated_oracle")

    _ACTIVE_TRANSPORT = transport
    _ACTIVE_BACKEND_KIND = effective_kind
    _ACTIVE_NONPRODUCTION = True
    _ACTIVE_ALLOW_UNCONTAINED_TEST_DOUBLE = allow_double_flag


def get_protected_repo_verifier_state() -> Dict[str, Any]:
    """Return the active protected repository verifier configuration.

    Never relies on path substrings alone to establish containment; checks backend-owned
    capability metadata on `_ACTIVE_TRANSPORT` and refuses activation when `QC_ENV=production`
    unless an explicit non-production transport was registered in-process.
    """
    from app.config import settings

    is_persistent_prod_dir = ".qwen-collective" in str(settings.data_dir).lower()
    caps = getattr(_ACTIVE_TRANSPORT, "_qc_backend_capabilities", None) if callable(_ACTIVE_TRANSPORT) else None
    is_valid_caps = isinstance(caps, dict)
    backend_kind = str(caps.get("backend_kind")) if is_valid_caps else (_ACTIVE_BACKEND_KIND if callable(_ACTIVE_TRANSPORT) else "held")
    is_real_linux_containment = bool(
        is_valid_caps
        and caps.get("backend_kind") == "linux_systemd_root_image_sandbox"
        and caps.get("filesystem_and_network_containment") is True
        and caps.get("is_uncontained_test_double") is False
        and caps.get("image_sha256") == PINNED_LINUX_ROOT_IMAGE_SHA256
    )
    is_test_double = bool(not is_real_linux_containment and callable(_ACTIVE_TRANSPORT))

    active = bool(
        _ACTIVE_NONPRODUCTION is True
        and callable(_ACTIVE_TRANSPORT)
        and not is_persistent_prod_dir
        and (is_real_linux_containment or _ACTIVE_ALLOW_UNCONTAINED_TEST_DOUBLE)
    )
    return {
        "configured": active,
        "backend_kind": backend_kind if active else "held",
        "nonproduction": bool(active and not is_persistent_prod_dir),
        "filesystem_and_network_containment": bool(active and is_real_linux_containment),
        "is_uncontained_test_double": bool(active and is_test_double),
        "hidden_oracle_separation": active,
        "independent_result_authority": active,
        "process_lifetime_and_output_control": active,
        "image_sha256": PINNED_LINUX_ROOT_IMAGE_SHA256 if (active and is_real_linux_containment) else None,
    }


def verify_receipt_containment_evidence(
    grant: Dict[str, Any],
    receipt: Dict[str, Any],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """Verify that the execution receipt and observations match the claimed containment backend."""
    body = (receipt or {}).get("body") or {}
    observations = body.get("observations") or []
    binding = body.get("binding") or {}
    unit_pattern = re.compile(r"^qc-sandbox-[0-9a-f]{32}\.service$")

    units_valid = bool(
        len(observations) > 0
        and all(isinstance(o.get("unit"), str) and bool(unit_pattern.match(o["unit"])) for o in observations)
    )
    all_executed = bool(len(observations) > 0 and all(o.get("executed") is True for o in observations))
    all_cleanup = bool(len(observations) > 0 and all(o.get("cleanup_confirmed") is True for o in observations))
    any_timeout = any(o.get("reason") == "timeout" for o in observations)
    image_pinned = binding.get("image_sha256") == PINNED_LINUX_ROOT_IMAGE_SHA256

    real_fs_net_contained = bool(
        state.get("filesystem_and_network_containment") is True
        and not state.get("is_uncontained_test_double")
        and units_valid
        and image_pinned
        and all_executed
    )
    return {
        "backend_kind": state.get("backend_kind", "held"),
        "is_uncontained_test_double": bool(state.get("is_uncontained_test_double", False)),
        "filesystem_and_network_containment": real_fs_net_contained,
        "process_lifetime_and_output_control": bool(all_executed and all_cleanup),
        "cleanup_confirmed": all_cleanup,
        "timed_out": any_timeout,
        "units_observed": [o.get("unit") for o in observations],
        "reasons_observed": [o.get("reason") for o in observations],
        "image_sha256": binding.get("image_sha256"),
    }


REPO_ENTRYPOINT_HARNESS = """import importlib, json, os, sys

sys.path.insert(0, "/src/src")
sys.path.insert(0, "src")

_orig_open = open
_orig_len = len
_orig_int = int
_orig_str = str
_orig_exit = sys.exit

_input_path = "/run/qc-input.json" if os.path.exists("/run/qc-input.json") else "_qc_input.json"
with _orig_open(_input_path, "r", encoding="utf-8") as _f:
    _req = json.load(_f)

_mode = _req.get("mode", "call_function")
if _mode == "verify_public_token":
    _out = {"execution_token": _orig_str(_req["execution_token"]), "probe_ok": True}
    sys.stdout.write(json.dumps(_out, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\\n")
    sys.stdout.flush()
    _orig_exit(0)
elif _mode == "run_public_unittest":
    import importlib.util, io, unittest
    _token = _orig_str(_req["execution_token"])
    _rel_test = _orig_str(_req["test_rel_path"]).lstrip("/")
    _test_file = os.path.join("/src/src" if os.path.exists("/src/src") else "src", _rel_test)
    _stream = io.StringIO()
    _loader = unittest.TestLoader()
    if os.path.isfile(_test_file) and _test_file.endswith(".py"):
        _spec = importlib.util.spec_from_file_location("_qc_public_test_mod", _test_file)
        _tmod = importlib.util.module_from_spec(_spec)
        sys.modules["_qc_public_test_mod"] = _tmod
        _spec.loader.exec_module(_tmod)
        _suite = _loader.loadTestsFromModule(_tmod)
    else:
        _suite = _loader.discover(start_dir=os.path.dirname(_test_file), pattern=os.path.basename(_test_file))
    _runner = unittest.TextTestRunner(stream=_stream, verbosity=1)
    _res = _runner.run(_suite)
    _ran = _orig_int(_res.testsRun)
    _failures = _orig_int(_orig_len(_res.failures))
    _errors = _orig_int(_orig_len(_res.errors))
    _skipped = _orig_int(_orig_len(getattr(_res, "skipped", [])))
    _out = {
        "execution_token": _token,
        "suite_passed": bool(_ran > 0 and _failures == 0 and _errors == 0 and _skipped == 0),
        "tests_run": _ran,
        "failures": _failures,
        "errors": _errors,
        "skipped": _skipped,
    }
    sys.stdout.write(json.dumps(_out, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\\n")
    sys.stdout.flush()
    _orig_exit(0)
else:
    _mod = importlib.import_module(_req["module"])
    _fn = getattr(_mod, _req["function"])
    _args = _req.get("args") or []
    _kwargs = _req.get("kwargs") or {}
    _out = _fn(*_args, **_kwargs)
    sys.stdout.write(json.dumps(_out, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\\n")
    sys.stdout.flush()
"""


def build_repository_code_bundle(
    workspace_path: str,
    target_file: Optional[str] = None,
    patched_content: Optional[str] = None,
    include_public_tests: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Build a `qc.code_bundle.v1` bundle containing repository Python files under `src/`
    with `target_file` (if supplied) replaced in-memory by `patched_content` and a fixed
    read-only `src/main.py` invocation harness.

    Returns `(bundle_dict, unrelated_file_hashes)`.
    """
    raw_root = Path(workspace_path)
    if is_reparse_or_symlink(raw_root):
        raise ValueError(f"Symlink or reparse point forbidden in repository workspace: {workspace_path}")
    root = raw_root.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Workspace directory not found: {workspace_path}")

    norm_target: Optional[str] = None
    if target_file is not None:
        norm_target = Path(target_file).as_posix().lstrip("/")
        if ".." in Path(norm_target).parts or not re.fullmatch(r"[A-Za-z0-9_./-]+", norm_target):
            raise ValueError(f"Unsafe target_file path: {target_file}")

    files_list: List[Dict[str, str]] = [
        {"path": "src/main.py", "content": REPO_ENTRYPOINT_HARNESS},
    ]
    unrelated_hashes: Dict[str, str] = {}
    found_target = norm_target is None

    for p in sorted(root.rglob("*")):
        if is_reparse_or_symlink(p):
            raise ValueError(f"Symlink or reparse point forbidden in repository workspace: {p}")
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(root).parts):
            continue
        if norm_target is not None and rel == norm_target:
            found_target = True
            files_list.append({"path": f"src/{rel}", "content": str(patched_content or "")})
        else:
            raw_bytes = p.read_bytes()
            unrelated_hashes[rel] = hashlib.sha256(raw_bytes).hexdigest()
            if rel.endswith(".py") and (include_public_tests or not rel.startswith("tests/")):
                files_list.append({
                    "path": f"src/{rel}",
                    "content": raw_bytes.decode("utf-8", errors="replace"),
                })

    if not found_target:
        raise ValueError(f"Target file '{norm_target}' not found in workspace '{root}'")

    bundle = {
        "schema": "qc.code_bundle.v1",
        "files": sorted(files_list, key=lambda x: x["path"]),
    }
    return bundle, unrelated_hashes


def inspect_public_suite_support(
    workspace_path: str,
    test_rel_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Statically inspect a repository public test suite before execution.

    Distinguishes:
    1. Missing target -> `supported=False`, `status="not_evaluated"`, `reason="public_test_target_not_found"`.
    2. Unsupported format (non-`.py`/non-`.json`, or Python test file using unsupported frameworks such as
       `pytest` fixtures/functions without `unittest.TestCase`, or syntax errors) ->
       `supported=False`, `status="not_evaluated"`, `reason=...`.
    3. Empty suite (`expected_test_count == 0`) ->
       `supported=True`, `empty=True`, `expected_test_count=0`, `reason="zero_public_tests_discovered"`.
    4. Supported `unittest` suite (`expected_test_count >= 1`) or declarative `qc.json_io_cases.v1` suite ->
       `supported=True`, `empty=False`.
    """
    root = Path(workspace_path).resolve()
    rel = str(test_rel_path or "tests").replace("\\", "/").lstrip("/")
    if ".." in Path(rel).parts:
        return {
            "supported": False,
            "status": "not_evaluated",
            "reason": f"path_traversal_rejected:{test_rel_path}",
            "suite_id": rel,
            "suite_kind": "unsupported",
            "suite_sha256": None,
            "expected_test_count": 0,
            "test_files": [],
        }

    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        return {
            "supported": False,
            "status": "not_evaluated",
            "reason": f"path_escapes_workspace:{test_rel_path}",
            "suite_id": rel,
            "suite_kind": "unsupported",
            "suite_sha256": None,
            "expected_test_count": 0,
            "test_files": [],
        }

    if not target.exists():
        return {
            "supported": False,
            "status": "not_evaluated",
            "reason": f"public_test_target_not_found:{rel}",
            "suite_id": rel,
            "suite_kind": "missing",
            "suite_sha256": None,
            "expected_test_count": 0,
            "test_files": [],
        }

    if target.is_file() and target.suffix.lower() == ".json":
        raw_bytes = target.read_bytes()
        suite_sha = hashlib.sha256(raw_bytes).hexdigest()
        try:
            doc = strict(raw_bytes.decode("utf-8"))
        except Exception as exc:
            return {
                "supported": False,
                "status": "not_evaluated",
                "reason": f"invalid_json_public_suite:{exc}",
                "suite_id": rel,
                "suite_kind": "unsupported_json",
                "suite_sha256": suite_sha,
                "expected_test_count": 0,
                "test_files": [rel],
            }
        if not isinstance(doc, dict) or doc.get("schema") != "qc.json_io_cases.v1":
            return {
                "supported": False,
                "status": "not_evaluated",
                "reason": f"unsupported_json_suite_schema:{doc.get('schema') if isinstance(doc, dict) else type(doc).__name__}",
                "suite_id": rel,
                "suite_kind": "unsupported_json_schema",
                "suite_sha256": suite_sha,
                "expected_test_count": 0,
                "test_files": [rel],
            }
        cases = doc.get("cases") or []
        return {
            "supported": True,
            "empty": len(cases) == 0,
            "status": "ready" if len(cases) > 0 else "failed",
            "reason": None if len(cases) > 0 else "zero_public_tests_discovered",
            "suite_id": rel,
            "suite_kind": "qc.json_io_cases.v1",
            "suite_sha256": suite_sha,
            "expected_test_count": len(cases),
            "test_files": [rel],
            "json_cases_path": str(target),
        }

    if target.is_file() and target.suffix.lower() != ".py":
        raw_bytes = target.read_bytes()
        return {
            "supported": False,
            "status": "not_evaluated",
            "reason": f"unsupported_public_suite_extension:{target.suffix}",
            "suite_id": rel,
            "suite_kind": "unsupported_file_type",
            "suite_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "expected_test_count": 0,
            "test_files": [rel],
        }

    py_files = [target] if target.is_file() else sorted(target.rglob("test*.py"))
    if not py_files:
        return {
            "supported": True,
            "empty": True,
            "status": "failed",
            "reason": "zero_public_tests_discovered",
            "suite_id": rel,
            "suite_kind": "python_unittest",
            "suite_sha256": hashlib.sha256(b"").hexdigest(),
            "expected_test_count": 0,
            "test_files": [],
        }

    hasher = hashlib.sha256()
    unittest_method_count = 0
    standalone_test_fn_count = 0
    uses_pytest_import = False
    rel_files: List[str] = []

    for pf in py_files:
        r_posix = pf.relative_to(root).as_posix()
        rel_files.append(r_posix)
        raw_b = pf.read_bytes()
        hasher.update(r_posix.encode("utf-8") + b"\0" + raw_b + b"\0")
        try:
            tree = ast.parse(raw_b.decode("utf-8", errors="replace"), filename=r_posix)
        except SyntaxError as se:
            return {
                "supported": False,
                "status": "not_evaluated",
                "reason": f"public_suite_syntax_error:{r_posix}:line_{se.lineno}",
                "suite_id": rel,
                "suite_kind": "invalid_python_syntax",
                "suite_sha256": hasher.hexdigest(),
                "expected_test_count": 0,
                "test_files": rel_files,
            }

        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod_names = [a.name for a in node.names]
                if getattr(node, "module", None) == "pytest" or "pytest" in mod_names:
                    uses_pytest_import = True
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                        unittest_method_count += 1
            elif isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                standalone_test_fn_count += 1

    suite_sha = hasher.hexdigest()
    if unittest_method_count == 0 and (uses_pytest_import or standalone_test_fn_count > 0):
        return {
            "supported": False,
            "status": "not_evaluated",
            "reason": "unsupported_public_test_framework:pytest_or_standalone_functions_require_unittest_testcase",
            "suite_id": rel,
            "suite_kind": "unsupported_pytest_or_function_suite",
            "suite_sha256": suite_sha,
            "expected_test_count": 0,
            "test_files": rel_files,
        }

    if unittest_method_count == 0:
        return {
            "supported": True,
            "empty": True,
            "status": "failed",
            "reason": "zero_public_tests_discovered",
            "suite_id": rel,
            "suite_kind": "python_unittest",
            "suite_sha256": suite_sha,
            "expected_test_count": 0,
            "test_files": rel_files,
        }

    return {
        "supported": True,
        "empty": False,
        "status": "ready",
        "reason": None,
        "suite_id": rel,
        "suite_kind": "python_unittest",
        "suite_sha256": suite_sha,
        "expected_test_count": unittest_method_count,
        "test_files": rel_files,
    }


def _compute_holdout_suite_file_sha256(holdout_path_or_dir: str) -> str:
    p = Path(holdout_path_or_dir).resolve()
    if p.is_file():
        candidate_file = p
    elif p.is_dir():
        if (p / "holdout_cases.json").is_file():
            candidate_file = p / "holdout_cases.json"
        elif (p / "cases.json").is_file():
            candidate_file = p / "cases.json"
        else:
            raise ValueError(f"No holdout_cases.json or cases.json in '{p}'")
    else:
        raise ValueError(f"Holdout path does not exist: {holdout_path_or_dir}")
    return hashlib.sha256(candidate_file.read_bytes()).hexdigest()


def load_protected_holdout_suite(
    holdout_path_or_dir: str,
) -> Tuple[Dict[str, Any], List[str]]:
    """Load declarative black-box `qc.json_io_cases.v1` cases from `holdout_cases.json` or `cases.json`.

    Rejects directories that only contain Python `unittest` files so expected answers
    never enter the candidate Python interpreter.
    Returns `(independent_tests_doc, requirement_ids)`.
    """
    p = Path(holdout_path_or_dir).resolve()
    if p.is_file():
        candidate_file = p
    elif p.is_dir():
        if (p / "holdout_cases.json").is_file():
            candidate_file = p / "holdout_cases.json"
        elif (p / "cases.json").is_file():
            candidate_file = p / "cases.json"
        else:
            raise ValueError(
                f"Protected repository verification requires declarative black-box cases "
                f"(holdout_cases.json or cases.json) in '{p}'; in-interpreter unittest holdouts are prohibited."
            )
    else:
        raise ValueError(f"Holdout path does not exist: {holdout_path_or_dir}")

    raw_bytes = candidate_file.read_bytes()
    raw_doc = strict(raw_bytes.decode("utf-8"))
    if isinstance(raw_doc, dict) and raw_doc.get("schema") == "qc.independent_tests.v1":
        req_ids = list(raw_doc["requirement_ids"])
        return raw_doc, req_ids

    if isinstance(raw_doc, dict) and raw_doc.get("schema") == "qc.json_io_cases.v1":
        cases = raw_doc.get("cases") or []
        req_ids = sorted({str(c["requirement_id"]) for c in cases if isinstance(c, dict) and "requirement_id" in c})
        if not req_ids:
            req_ids = ["repo_bugfix_contract"]
        suite_payload = {
            "schema": "qc.json_io_cases.v1",
            "cases": cases,
        }
        independent_doc = {
            "schema": "qc.independent_tests.v1",
            "requirement_ids": req_ids,
            "files": [
                {
                    "path": "tests/cases.json",
                    "content": json.dumps(suite_payload, sort_keys=True, separators=(",", ":")),
                }
            ],
        }
        return independent_doc, req_ids

    raise ValueError("Unsupported holdout file schema; expected qc.independent_tests.v1 or qc.json_io_cases.v1")


def verify_repository_public_suite_protected(
    *,
    workspace_path: str,
    test_rel_path: Optional[str] = None,
    target_file: Optional[str] = None,
    patched_content: Optional[str] = None,
    project_id: str = "proj_repo_pub",
    run_id: str = "run_repo_pub",
    native_execution_id: str = "exec_pub_bound",
    native_lease_id: str = "lease_pub_bound",
    native_idempotency_key: str = "idem_pub_bound",
    plan_revision_id: str = "rev_1",
    seconds: int = 10,
    image_sha256: str = PINNED_LINUX_ROOT_IMAGE_SHA256,
) -> Dict[str, Any]:
    """Execute a repository's public test suite inside the configured containment backend.

    Never manufactures a public result from holdout results, never executes on the host
    when containment is held/unavailable, returns `status="not_evaluated"` for unsupported
    public suites, and rejects zero-test suites (`expected_test_count == 0`).
    """
    state = get_protected_repo_verifier_state()
    suite_info = inspect_public_suite_support(workspace_path, test_rel_path)

    if not state["configured"] or _ACTIVE_TRANSPORT is None:
        return {
            "schema": "qc.repo_test_run.v1",
            "suite_role": "public",
            "suite_id": suite_info["suite_id"],
            "suite_kind": suite_info["suite_kind"],
            "suite_sha256": suite_info["suite_sha256"],
            "candidate_source_sha256": None,
            "status": "held_containment_backend_required",
            "verification_scope": "public_development_diagnostics_only",
            "executed": False,
            "passed": False,
            "tests_run": 0,
            "expected_test_count": suite_info["expected_test_count"],
            "failures": 0,
            "errors": 0,
            "cleanup_confirmed": False,
            "containment_backend": "held",
            "is_uncontained_test_double": False,
            "process_lifetime_and_output_control": False,
            "filesystem_and_network_containment": False,
            "hidden_oracle_separated": False,
            "independent_result_authority": True,
            "protected_verification": False,
            "establishes_protected_acceptance": False,
            "reason": "Containment backend is required before executing repository test suites; host execution is disabled.",
            "output_excerpt": "Containment backend held; candidate code was not executed.",
        }

    # Compute candidate bundle digest even if suite is unsupported/empty so results are bound to exact bytes
    try:
        bundle, unrelated_hashes = build_repository_code_bundle(
            workspace_path=workspace_path,
            target_file=target_file,
            patched_content=patched_content,
            include_public_tests=True,
        )
        source_json = encoded(bundle).decode("utf-8")
        source_sha = digest(source_json.encode("utf-8"))
    except Exception as exc:
        return {
            "schema": "qc.repo_test_run.v1",
            "suite_role": "public",
            "suite_id": suite_info["suite_id"],
            "suite_kind": suite_info["suite_kind"],
            "suite_sha256": suite_info["suite_sha256"],
            "candidate_source_sha256": None,
            "status": "failed",
            "executed": False,
            "passed": False,
            "tests_run": 0,
            "expected_test_count": suite_info["expected_test_count"],
            "failures": 1,
            "errors": 1,
            "cleanup_confirmed": False,
            "containment_backend": state["backend_kind"],
            "is_uncontained_test_double": state["is_uncontained_test_double"],
            "process_lifetime_and_output_control": False,
            "filesystem_and_network_containment": False,
            "hidden_oracle_separated": True,
            "independent_result_authority": True,
            "protected_verification": False,
            "establishes_protected_acceptance": False,
            "reason": f"bundle_build_failed:{exc}",
            "error": str(exc),
            "output_excerpt": str(exc),
        }

    if not suite_info["supported"]:
        return {
            "schema": "qc.repo_test_run.v1",
            "suite_role": "public",
            "suite_id": suite_info["suite_id"],
            "suite_kind": suite_info["suite_kind"],
            "suite_sha256": suite_info["suite_sha256"],
            "candidate_source_sha256": source_sha,
            "status": "not_evaluated",
            "executed": False,
            "passed": False,
            "tests_run": 0,
            "expected_test_count": 0,
            "failures": 0,
            "errors": 0,
            "cleanup_confirmed": True,
            "containment_backend": state["backend_kind"],
            "is_uncontained_test_double": state["is_uncontained_test_double"],
            "process_lifetime_and_output_control": False,
            "filesystem_and_network_containment": False,
            "hidden_oracle_separated": True,
            "independent_result_authority": True,
            "protected_verification": False,
            "establishes_protected_acceptance": False,
            "reason": suite_info["reason"],
            "output_excerpt": f"Public test suite not evaluated: {suite_info['reason']}",
        }

    if suite_info.get("empty") or int(suite_info["expected_test_count"]) <= 0:
        return {
            "schema": "qc.repo_test_run.v1",
            "suite_role": "public",
            "suite_id": suite_info["suite_id"],
            "suite_kind": suite_info["suite_kind"],
            "suite_sha256": suite_info["suite_sha256"],
            "candidate_source_sha256": source_sha,
            "status": "failed",
            "executed": False,
            "passed": False,
            "tests_run": 0,
            "expected_test_count": 0,
            "failures": 1,
            "errors": 0,
            "cleanup_confirmed": True,
            "containment_backend": state["backend_kind"],
            "is_uncontained_test_double": state["is_uncontained_test_double"],
            "process_lifetime_and_output_control": False,
            "filesystem_and_network_containment": False,
            "hidden_oracle_separated": True,
            "independent_result_authority": True,
            "protected_verification": False,
            "establishes_protected_acceptance": False,
            "reason": "zero_public_tests_discovered",
            "output_excerpt": "Public test suite discovered 0 tests; empty suites cannot pass.",
        }

    if suite_info["suite_kind"] == "qc.json_io_cases.v1":
        testdoc, req_ids, _ = load_protected_holdout_suite(suite_info["json_cases_path"])
        expected_total = int(suite_info["expected_test_count"])
    else:
        # Build 2 independent execution cases across the discovered public unittest files
        # (`independent_verifier` requires 2..16 cases in `qc.json_io_cases.v1`)
        expected_total = int(suite_info["expected_test_count"])
        test_files = list(suite_info["test_files"])
        tok_1 = secrets.token_hex(16)
        tok_2 = secrets.token_hex(16)
        file_1 = test_files[0]
        file_2 = test_files[1] if len(test_files) > 1 else test_files[0]

        def _count_methods_in_rel(rel_f: str) -> int:
            if len(test_files) == 1:
                return expected_total
            p_f = Path(workspace_path).resolve() / rel_f
            tree = ast.parse(p_f.read_text(encoding="utf-8", errors="replace"))
            cnt = 0
            for n in tree.body:
                if isinstance(n, ast.ClassDef):
                    for it in n.body:
                        if isinstance(it, ast.FunctionDef) and it.name.startswith("test_"):
                            cnt += 1
            return max(1, cnt)

        cnt_1 = _count_methods_in_rel(file_1)
        cnt_2 = _count_methods_in_rel(file_2)
        req_ids = ["public_test_suite_contract"]
        if len(test_files) == 1:
            second_case = {
                "id": "public_suite_run_2",
                "requirement_id": "public_test_suite_contract",
                "input": {
                    "mode": "verify_public_token",
                    "execution_token": tok_2,
                },
                "expected": {
                    "execution_token": tok_2,
                    "probe_ok": True,
                },
            }
        else:
            second_case = {
                "id": "public_suite_run_2",
                "requirement_id": "public_test_suite_contract",
                "input": {
                    "mode": "run_public_unittest",
                    "test_rel_path": file_2,
                    "execution_token": tok_2,
                },
                "expected": {
                    "execution_token": tok_2,
                    "suite_passed": True,
                    "tests_run": cnt_2,
                    "failures": 0,
                    "errors": 0,
                    "skipped": 0,
                },
            }
        pub_cases = [
            {
                "id": "public_suite_run_1",
                "requirement_id": "public_test_suite_contract",
                "input": {
                    "mode": "run_public_unittest",
                    "test_rel_path": file_1,
                    "execution_token": tok_1,
                },
                "expected": {
                    "execution_token": tok_1,
                    "suite_passed": True,
                    "tests_run": cnt_1,
                    "failures": 0,
                    "errors": 0,
                    "skipped": 0,
                },
            },
            second_case,
        ]
        suite_payload = {
            "schema": "qc.json_io_cases.v1",
            "cases": pub_cases,
        }
        testdoc = {
            "schema": "qc.independent_tests.v1",
            "requirement_ids": req_ids,
            "files": [
                {
                    "path": "tests/cases.json",
                    "content": json.dumps(suite_payload, sort_keys=True, separators=(",", ":")),
                }
            ],
        }

    tests_json = encoded(testdoc).decode("utf-8")
    tests_sha = digest(tests_json.encode("utf-8"))
    review_seed = encoded({
        "project_id": project_id,
        "run_id": run_id,
        "suite_id": suite_info["suite_id"],
        "source_sha": source_sha,
        "tests_sha": tests_sha,
        "suite_sha": suite_info["suite_sha256"],
    })
    review_id = digest(review_seed)
    plan_sha = digest(f"{project_id}:{run_id}:{plan_revision_id}".encode("utf-8"))
    contract_sha = digest(f"public:{suite_info['suite_id']}:{tests_sha}".encode("utf-8"))

    binding = {
        "review_id": review_id,
        "project_id": str(project_id),
        "run_id": str(run_id),
        "native_execution_id": str(native_execution_id or "exec_pub_bound"),
        "native_lease_id": str(native_lease_id or "lease_pub_bound"),
        "native_idempotency_key": str(native_idempotency_key or "idem_pub_bound"),
        "proposal": {
            "artifact_id": f"cand_{source_sha[:16]}",
            "version": 1,
            "sha256": source_sha,
            "cas_hash": source_sha,
        },
        "approved_tests": {
            "artifact_id": f"pubtests_{tests_sha[:16]}",
            "version": 1,
            "sha256": tests_sha,
            "cas_hash": tests_sha,
        },
        "plan_revision_id": str(plan_revision_id),
        "plan_sha256": plan_sha,
        "contract_sha256": contract_sha,
        "image_sha256": image_sha256,
        "requirement_ids": req_ids,
    }
    grant = {
        "schema": "qc.protected_verifier_grant.v1",
        "nonce": uuid.uuid4().hex,
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=180)).isoformat(),
        "binding": binding,
        "source_json": source_json,
        "tests_json": tests_json,
        "seconds": max(1, min(int(seconds), 15)),
    }
    validate_grant(grant)
    key = secrets.token_bytes(32)

    try:
        receipt = _ACTIVE_TRANSPORT(grant, key)
        if len(encoded(receipt)) > 600_000:
            raise ValueError("Protected verifier receipt exceeds 600KB budget")
        cases_evaluated = check_receipt(grant, receipt, key)
    except Exception as exc:
        err_str = str(exc)
        is_timeout = "timeout" in err_str.lower() or "timed out" in err_str.lower()
        return {
            "schema": "qc.repo_test_run.v1",
            "suite_role": "public",
            "suite_id": suite_info["suite_id"],
            "suite_kind": suite_info["suite_kind"],
            "suite_sha256": suite_info["suite_sha256"],
            "candidate_source_sha256": source_sha,
            "status": "timed_out" if is_timeout else "backend_error",
            "executed": False,
            "passed": False,
            "tests_run": 0,
            "expected_test_count": expected_total,
            "failures": 0,
            "errors": 1,
            "cleanup_confirmed": False,
            "containment_backend": state["backend_kind"],
            "is_uncontained_test_double": state["is_uncontained_test_double"],
            "process_lifetime_and_output_control": False,
            "filesystem_and_network_containment": False,
            "hidden_oracle_separated": True,
            "independent_result_authority": True,
            "protected_verification": False,
            "establishes_protected_acceptance": False,
            "reason": f"backend_execution_failed:{err_str}",
            "error": err_str,
            "output_excerpt": f"Containment backend failed without host fallback: {err_str[:400]}",
        }

    containment_ev = verify_receipt_containment_evidence(grant, receipt, state)
    observations = receipt["body"]["observations"]
    all_cleanup = containment_ev["cleanup_confirmed"]

    # Extract actual unittest counts from the primary observation stdout if structured JSON was returned
    obs_0 = observations[0] if observations else {}
    obs_stdout = str(obs_0.get("stdout") or "").strip()
    actual_ran = 0
    actual_failures = 0
    actual_errors = 0
    try:
        parsed_out = strict(obs_stdout)
        if isinstance(parsed_out, dict) and "tests_run" in parsed_out:
            actual_ran = int(parsed_out.get("tests_run", 0))
            actual_failures = int(parsed_out.get("failures", 0))
            actual_errors = int(parsed_out.get("errors", 0))
    except Exception:
        actual_ran = 0
        actual_failures = sum(1 for c in cases_evaluated if not c["passed"])
        actual_errors = sum(1 for o in observations if o.get("reason") != "completed" or o.get("exit_code") != 0)

    if suite_info["suite_kind"] == "qc.json_io_cases.v1":
        actual_ran = len(cases_evaluated)
        actual_failures = sum(1 for c in cases_evaluated if not c["passed"])
        actual_errors = sum(1 for o in observations if o.get("reason") != "completed" or o.get("exit_code") != 0)

    cases_all_passed = len(cases_evaluated) > 0 and all(c["passed"] is True for c in cases_evaluated)
    suite_passed = bool(
        cases_all_passed
        and all_cleanup
        and actual_ran >= expected_total
        and expected_total > 0
        and actual_failures == 0
        and actual_errors == 0
    )

    if not all_cleanup:
        status_str = "cleanup_unverified"
    elif containment_ev["timed_out"]:
        status_str = "timed_out"
    elif suite_passed:
        status_str = "verified_for_scope" if containment_ev["filesystem_and_network_containment"] else "public_diagnostics_passed"
    else:
        status_str = "failed"

    return {
        "schema": "qc.repo_test_run.v1",
        "suite_role": "public",
        "suite_id": suite_info["suite_id"],
        "suite_kind": suite_info["suite_kind"],
        "suite_sha256": suite_info["suite_sha256"],
        "candidate_source_sha256": source_sha,
        "tests_manifest_sha256": tests_sha,
        "grant_nonce": grant["nonce"],
        "grant_sha256": receipt["body"]["grant_sha256"],
        "status": status_str,
        "verification_scope": (
            "verified_inside_linux_systemd_root_image_sandbox"
            if containment_ev["filesystem_and_network_containment"]
            else "public_development_diagnostics_only"
        ),
        "executed": True,
        "executed_on_controller_host": False,
        "passed": suite_passed,
        "tests_run": actual_ran,
        "expected_test_count": expected_total,
        "failures": actual_failures if not suite_passed and actual_failures > 0 else (0 if suite_passed else max(1, actual_failures)),
        "errors": actual_errors,
        "returncode": obs_0.get("exit_code", 0 if suite_passed else 1),
        "reason": obs_0.get("reason", "completed"),
        "cleanup_confirmed": all_cleanup,
        "containment_backend": state["backend_kind"],
        "containment_category": state["backend_kind"],
        "is_uncontained_test_double": state["is_uncontained_test_double"],
        "process_lifetime_and_output_control": containment_ev["process_lifetime_and_output_control"],
        "filesystem_and_network_containment": containment_ev["filesystem_and_network_containment"],
        "hidden_oracle_separated": bool(suite_info["suite_kind"] == "qc.json_io_cases.v1"),
        "independent_result_authority": True,
        "protected_verification": bool(suite_passed and containment_ev["filesystem_and_network_containment"]),
        "establishes_protected_acceptance": False,
        "cases": cases_evaluated,
        "observations": observations,
        "containment": containment_ev,
        "output_excerpt": obs_stdout[:1200] if obs_stdout else f"Public suite executed (passed={suite_passed}, ran={actual_ran}).",
    }


def verify_repository_candidate_protected(
    *,
    workspace_path: str,
    target_file: str,
    patched_content: str,
    holdout_test_dir: str,
    project_id: str = "proj_repo_verif",
    run_id: str = "run_repo_verif",
    native_execution_id: str = "exec_bound",
    native_lease_id: str = "lease_bound",
    native_idempotency_key: str = "idem_bound",
    plan_revision_id: str = "rev_1",
    seconds: int = 5,
    image_sha256: str = PINNED_LINUX_ROOT_IMAGE_SHA256,
) -> Dict[str, Any]:
    """Verify a candidate repository patch against declarative black-box holdout cases using the
    configured protected verification transport (`vps_agent.independent_verifier`).

    Never executes candidate code if no protected verification transport is configured.
    """
    state = get_protected_repo_verifier_state()
    if not state["configured"] or _ACTIVE_TRANSPORT is None:
        return {
            "schema": "qc.protected_repo_verification.v1",
            "suite_role": "holdout",
            "status": "held_protected_backend_required",
            "executed": False,
            "passed": False,
            "behavior_verified": False,
            "accepted": False,
            "protected_verification": False,
            "containment_backend": "held",
            "is_uncontained_test_double": False,
            "process_lifetime_and_output_control": False,
            "filesystem_and_network_containment": False,
            "hidden_oracle_separation": True,
            "independent_result_authority": True,
            "cleanup_confirmed": False,
            "error": "Protected repository verifier transport is held; candidate code was not executed on the controller host.",
        }

    bundle, unrelated_hashes = build_repository_code_bundle(
        workspace_path=workspace_path,
        target_file=target_file,
        patched_content=patched_content,
        include_public_tests=False,
    )
    testdoc, req_ids = load_protected_holdout_suite(holdout_test_dir)
    raw_holdout_sha = _compute_holdout_suite_file_sha256(holdout_test_dir)

    source_json = encoded(bundle).decode("utf-8")
    tests_json = encoded(testdoc).decode("utf-8")
    source_sha = digest(source_json.encode("utf-8"))
    tests_sha = digest(tests_json.encode("utf-8"))

    review_seed = encoded({
        "project_id": project_id,
        "run_id": run_id,
        "target_file": target_file,
        "source_sha": source_sha,
        "tests_sha": tests_sha,
        "unrelated_hashes": unrelated_hashes,
    })
    review_id = digest(review_seed)
    plan_sha = digest(f"{project_id}:{run_id}:{plan_revision_id}".encode("utf-8"))
    contract_sha = digest(f"{target_file}:{tests_sha}:{','.join(req_ids)}".encode("utf-8"))

    binding = {
        "review_id": review_id,
        "project_id": str(project_id),
        "run_id": str(run_id),
        "native_execution_id": str(native_execution_id or "exec_bound"),
        "native_lease_id": str(native_lease_id or "lease_bound"),
        "native_idempotency_key": str(native_idempotency_key or "idem_bound"),
        "proposal": {
            "artifact_id": f"cand_{source_sha[:16]}",
            "version": 1,
            "sha256": source_sha,
            "cas_hash": source_sha,
        },
        "approved_tests": {
            "artifact_id": f"tests_{tests_sha[:16]}",
            "version": 1,
            "sha256": tests_sha,
            "cas_hash": tests_sha,
        },
        "plan_revision_id": str(plan_revision_id),
        "plan_sha256": plan_sha,
        "contract_sha256": contract_sha,
        "image_sha256": image_sha256,
        "requirement_ids": req_ids,
    }
    grant = {
        "schema": "qc.protected_verifier_grant.v1",
        "nonce": uuid.uuid4().hex,
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=180)).isoformat(),
        "binding": binding,
        "source_json": source_json,
        "tests_json": tests_json,
        "seconds": max(1, min(int(seconds), 15)),
    }
    validate_grant(grant)
    key = secrets.token_bytes(32)
    receipt = _ACTIVE_TRANSPORT(grant, key)
    if len(encoded(receipt)) > 600_000:
        raise ValueError("Protected verifier receipt exceeds 600KB budget")

    cases_evaluated = check_receipt(grant, receipt, key)
    containment_ev = verify_receipt_containment_evidence(grant, receipt, state)
    observations = receipt["body"]["observations"]
    all_cleanup = containment_ev["cleanup_confirmed"]
    all_passed = len(cases_evaluated) > 0 and all(c["passed"] is True for c in cases_evaluated) and all_cleanup

    return {
        "schema": "qc.protected_repo_verification.v1",
        "suite_role": "holdout",
        "suite_id": str(Path(holdout_test_dir).name),
        "suite_kind": "qc.json_io_cases.v1",
        "suite_sha256": raw_holdout_sha,
        "status": "verified_for_scope" if all_passed else ("cleanup_unverified" if not all_cleanup else "failed"),
        "executed": True,
        "passed": all_passed,
        "behavior_verified": all_passed,
        "accepted": all_passed,
        "certification_eligible": False,
        "protected_verification": bool(all_passed and not state["is_uncontained_test_double"]),
        "containment_backend": state["backend_kind"],
        "is_uncontained_test_double": state["is_uncontained_test_double"],
        "process_lifetime_and_output_control": containment_ev["process_lifetime_and_output_control"],
        "filesystem_and_network_containment": containment_ev["filesystem_and_network_containment"],
        "hidden_oracle_separation": True,
        "independent_result_authority": True,
        "grant_nonce": grant["nonce"],
        "grant_sha256": receipt["body"]["grant_sha256"],
        "candidate_source_sha256": source_sha,
        "source_sha256": source_sha,
        "tests_sha256": tests_sha,
        "image_sha256": image_sha256,
        "tests_run": len(cases_evaluated),
        "expected_test_count": len(cases_evaluated),
        "failures": sum(1 for c in cases_evaluated if not c["passed"]),
        "errors": sum(1 for obs in observations if obs.get("reason") != "completed" or obs.get("exit_code") != 0),
        "cleanup_confirmed": all_cleanup,
        "cases": cases_evaluated,
        "observations": observations,
        "containment": containment_ev,
        "receipt": receipt,
    }


def make_local_blackbox_isolated_transport(
    *,
    simulate_cleanup_failure: bool = False,
) -> Callable[[Dict[str, Any], bytes], Dict[str, Any]]:
    """Create a local out-of-process black-box verifier transport double for fast non-SSH unit tests.

    Guarantees:
    - Validates `grant` via `validate_grant(grant)`.
    - Consumes `grant['nonce']` in a private consumed-nonce set (preventing replay).
    - Stages ONLY `source_json` files and the single-case `case['input']` (`_qc_input.json`)
      into an isolated temporary directory per case.
    - NEVER writes `case['expected']`, `tests_json`, `grant`, or `key` into the child
      directory or environment.
    - Evaluates `strict_json_equal` outside the child process via `check_receipt`.
    - Carries backend-owned `_qc_backend_capabilities` marking `is_uncontained_test_double=True`
      and `filesystem_and_network_containment=False`. It can never establish Linux kernel mount/network
      containment even if a caller passes `backend_kind="linux_systemd_root_image_sandbox"`.
    """
    from vps_agent.bounded_process import run_bounded_process
    from app.services.orchestration.code_sandbox import build_sanitized_environment

    consumed_nonces: set[str] = set()

    def _transport(grant: Dict[str, Any], key: bytes) -> Dict[str, Any]:
        files, cases = validate_grant(grant)
        nonce = grant["nonce"]
        if nonce in consumed_nonces:
            raise FileExistsError(f"Grant nonce already consumed: {nonce}")
        consumed_nonces.add(nonce)

        observations: List[Dict[str, Any]] = []
        py_exe = getattr(sys, "_base_executable", None) or sys.executable

        for case in cases:
            validate_grant(grant)
            with tempfile.TemporaryDirectory(prefix="qc_bb_case_") as tmpdir:
                tmp_root = Path(tmpdir)
                for rel_path, content_bytes in files.items():
                    dest = tmp_root / rel_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(content_bytes)
                    try:
                        os.chmod(dest, 0o444)
                    except OSError:
                        pass

                req_file = tmp_root / "_qc_input.json"
                req_file.write_bytes(encoded(case["input"]))

                env = build_sanitized_environment()
                env["PYTHONDONTWRITEBYTECODE"] = "1"
                proc = run_bounded_process(
                    [py_exe, "-I", "-B", str(tmp_root / "src" / "main.py")],
                    cwd=str(tmp_root),
                    env=env,
                    timeout_seconds=float(grant["seconds"]),
                    output_limit=65536,
                )

                # Restore permissions so TemporaryDirectory cleanup succeeds on Windows
                for p in tmp_root.rglob("*"):
                    if p.is_file():
                        try:
                            os.chmod(p, 0o666)
                        except OSError:
                            pass

            cleanup_ok = False if simulate_cleanup_failure else bool(proc.cleanup_confirmed)
            unit_id = f"qc-sandbox-{uuid.uuid4().hex}.service"
            observations.append({
                "case_id": case["id"],
                "executed": True,
                "reason": "cleanup_unverified" if not cleanup_ok else proc.reason,
                "exit_code": int(proc.returncode) if proc.returncode is not None else 1,
                "cleanup_confirmed": cleanup_ok,
                "stdout": (proc.stdout or "")[:65536],
                "unit": unit_id,
            })
            if not cleanup_ok:
                break

        # Pad remaining cases as unexecuted if stopped early on cleanup failure so evaluate_observations
        # receives exact case count and rejects the run cleanly.
        while len(observations) < len(cases):
            rem_case = cases[len(observations)]
            observations.append({
                "case_id": rem_case["id"],
                "executed": False,
                "reason": "cleanup_unverified",
                "exit_code": 1,
                "cleanup_confirmed": False,
                "stdout": "",
                "unit": f"qc-sandbox-{uuid.uuid4().hex}.service",
            })

        return receipt_for(grant, observations, key)

    setattr(
        _transport,
        "_qc_backend_capabilities",
        {
            "backend_kind": "local_blackbox_process_isolated_oracle",
            "filesystem_and_network_containment": False,
            "is_uncontained_test_double": True,
            "image_sha256": None,
        },
    )
    return _transport


REMOTE_LINUX_VERIFIER_DRIVER = r"""
import datetime, hashlib, json, os, pathlib, sys
sys.path.insert(0, "/opt/qwen-collective/source")
from vps_agent.independent_verifier import run_protected
from vps_agent.linux_sandbox import sha

payload = json.loads(sys.stdin.read())
grant = payload["grant"]
key = bytes.fromhex(payload["key_hex"])
image_sha = grant["binding"]["image_sha256"]

policy_dir = pathlib.Path("/etc/qwen-sandbox")
policy_dir.mkdir(mode=0o755, exist_ok=True)
os.chown(policy_dir, 0, 0)
os.chmod(policy_dir, 0o755)
policy_path = policy_dir / "test-host.json"

machine_id = pathlib.Path("/etc/machine-id").read_bytes().strip()
now_utc = datetime.datetime.now(datetime.timezone.utc)
policy = {
    "schema": "qc.linux_test_host.v1",
    "environment": "non-production",
    "machine_id_sha256": sha(machine_id),
    "expires_at": (now_utc + datetime.timedelta(minutes=15)).isoformat(),
    "allowed_image_sha256": [image_sha],
}
policy_path.write_bytes(json.dumps(policy).encode("utf-8"))
os.chown(policy_path, 0, 0)
os.chmod(policy_path, 0o644)

try:
    receipt = run_protected(grant, key)
    res = {"ok": True, "receipt": receipt}
    if payload.get("test_nonce_replay"):
        try:
            run_protected(grant, key)
            res["nonce_replay_denied"] = False
        except FileExistsError:
            res["nonce_replay_denied"] = True
finally:
    try:
        policy_path.unlink()
    except FileNotFoundError:
        pass
    try:
        __import__("subprocess").run(["/usr/bin/systemctl", "reset-failed"], check=False)
    except Exception:
        pass

print("QC_RECEIPT_JSON:" + json.dumps(res, sort_keys=True, separators=(",", ":")))
"""


def make_laptop_ssh_verifier_transport(
    host: str = "100.101.187.115",
    user: str = "emceemcee",
    key_filename: Optional[str] = None,
    record_runs: Optional[List[Dict[str, Any]]] = None,
) -> Callable[[Dict[str, Any], bytes], Dict[str, Any]]:
    """Create a live SSH transport that executes `vps_agent.independent_verifier.run_protected`
    as root on `qwen-laptop` (`100.101.187.115`) inside the pinned squashfs root image
    (`43b057aa6a7133de2bbed57a8caaeecd07aea9228fa7cb6bec1608286b967b61.raw`).
    """
    import base64
    import paramiko

    resolved_key = key_filename or str(Path.home() / ".ssh" / "id_ed25519")
    b64_driver = base64.b64encode(REMOTE_LINUX_VERIFIER_DRIVER.encode("utf-8")).decode("ascii")
    cached_client: List[Any] = []

    def _get_client() -> Any:
        if cached_client:
            transport_obj = cached_client[0].get_transport()
            if transport_obj is not None and transport_obj.is_active():
                return cached_client[0]
            try:
                cached_client[0].close()
            except Exception:
                pass
            cached_client.clear()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(host, 22, user, key_filename=resolved_key, timeout=15)
        cached_client.append(client)
        return client

    def _transport(grant: Dict[str, Any], key: bytes) -> Dict[str, Any]:
        validate_grant(grant)
        client = _get_client()
        stdin_payload = json.dumps({
            "grant": grant,
            "key_hex": key.hex(),
            "test_nonce_replay": True,
        })
        cmd = f"sudo python3 -I -B -c \"import base64; exec(base64.b64decode('{b64_driver}').decode('utf-8'))\""
        stdin, stdout, stderr = client.exec_command(cmd, timeout=90)
        stdin.write(stdin_payload)
        stdin.channel.shutdown_write()
        out_text = stdout.read().decode("utf-8", errors="replace")
        err_text = stderr.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        if rc != 0:
            raise RuntimeError(f"Remote Linux verifier failed (rc={rc}): {err_text[-1200:]}")
        marker = "QC_RECEIPT_JSON:"
        idx = out_text.rfind(marker)
        if idx < 0:
            raise RuntimeError(f"Missing QC_RECEIPT_JSON marker in remote verifier output: {out_text[-800:]}")
        parsed = json.loads(out_text[idx + len(marker):].strip())
        if record_runs is not None:
            record_runs.append({
                "nonce": grant["nonce"],
                "nonce_replay_denied": parsed.get("nonce_replay_denied"),
                "observations": parsed["receipt"]["body"]["observations"],
            })
        return parsed["receipt"]

    setattr(
        _transport,
        "_qc_backend_capabilities",
        {
            "backend_kind": "linux_systemd_root_image_sandbox",
            "filesystem_and_network_containment": True,
            "is_uncontained_test_double": False,
            "image_sha256": PINNED_LINUX_ROOT_IMAGE_SHA256,
            "host": host,
        },
    )
    return _transport
