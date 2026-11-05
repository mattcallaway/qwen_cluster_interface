"""Protected Repository Verification Bridge (Increment 19 Review Remediation).

Establishes genuine candidate/oracle separation and kernel-enforced containment
for repository bug-fix verification:

1. **No Uncontained Host Execution Fallback**:
   - By default (`_ACTIVE_TRANSPORT = None`), protected repository verification is HELD
     (`status="held_protected_backend_required"`). Candidate generated code is never
     executed on the ordinary controller host without an explicitly configured
     non-production verification transport.
2. **Hidden-Oracle & Authority Separation**:
   - Protected holdout suites must be declarative black-box `qc.json_io_cases.v1` suites
     (`cases.json` / `holdout_cases.json`). In-interpreter `unittest` holdout files are
     rejected for protected verification.
   - Only approved repository source files (`src/...`) and a single case's public `input`
     (`/run/qc-input.json`) enter the candidate environment.
   - Hidden `expected` oracle values, other test cases, `strict_json_equal` evaluation
     logic, the 256-bit HMAC signing key, and `verifier-audit/<nonce>` remain outside
     candidate control.
   - Candidate stdout, exit codes, test-result objects, or claimed `"accepted": true`
     flags cannot establish a pass.
3. **Explicit Containment Classification**:
   - Separates:
     * `process_lifetime_and_output_control`
     * `filesystem_and_network_containment` (True ONLY on `linux_systemd_root_image_sandbox`)
     * `hidden_oracle_separation`
     * `independent_result_authority`
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
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


def configure_protected_repo_verifier(
    transport: Optional[Callable[[Dict[str, Any], bytes], Dict[str, Any]]] = None,
    *,
    backend_kind: str = "local_blackbox_process_isolated_oracle",
    nonproduction: bool = False,
) -> None:
    """Explicitly configure or clear the non-production repository verifier transport."""
    global _ACTIVE_TRANSPORT, _ACTIVE_BACKEND_KIND, _ACTIVE_NONPRODUCTION
    if transport is None or not nonproduction:
        _ACTIVE_TRANSPORT = None
        _ACTIVE_BACKEND_KIND = "held"
        _ACTIVE_NONPRODUCTION = False
    else:
        _ACTIVE_TRANSPORT = transport
        _ACTIVE_BACKEND_KIND = backend_kind
        _ACTIVE_NONPRODUCTION = True


def get_protected_repo_verifier_state() -> Dict[str, Any]:
    """Return the active protected repository verifier configuration.

    Refuses activation if `settings.data_dir` points to persistent `.qwen-collective` storage.
    """
    from app.config import settings

    is_isolated_data_dir = ".qwen-collective" not in str(settings.data_dir).lower()
    active = (
        _ACTIVE_NONPRODUCTION is True
        and callable(_ACTIVE_TRANSPORT)
        and is_isolated_data_dir
    )
    return {
        "configured": active,
        "backend_kind": _ACTIVE_BACKEND_KIND if active else "held",
        "nonproduction": _ACTIVE_NONPRODUCTION and is_isolated_data_dir,
        "filesystem_and_network_containment": active and _ACTIVE_BACKEND_KIND == "linux_systemd_root_image_sandbox",
        "hidden_oracle_separation": active,
        "independent_result_authority": active,
        "process_lifetime_and_output_control": active,
    }


REPO_ENTRYPOINT_HARNESS = """import importlib, json, sys
sys.path.insert(0, "/src/src")
sys.path.insert(0, "src")
with open("/run/qc-input.json" if __import__("os").path.exists("/run/qc-input.json") else "_qc_input.json", "r", encoding="utf-8") as _f:
    _req = json.load(_f)
_mod = importlib.import_module(_req["module"])
_fn = getattr(_mod, _req["function"])
_args = _req.get("args") or []
_kwargs = _req.get("kwargs") or {}
_out = _fn(*_args, **_kwargs)
print(json.dumps(_out, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
"""


def build_repository_code_bundle(
    workspace_path: str,
    target_file: str,
    patched_content: str,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Build a `qc.code_bundle.v1` bundle containing repository Python files under `src/`
    with `target_file` replaced in-memory by `patched_content` and a fixed read-only
    `src/main.py` black-box invocation harness.

    Returns `(bundle_dict, unrelated_file_hashes)`.
    """
    root = Path(workspace_path).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Workspace directory not found: {workspace_path}")

    norm_target = Path(target_file).as_posix().lstrip("/")
    if ".." in Path(norm_target).parts or not re.fullmatch(r"[A-Za-z0-9_./-]+", norm_target):
        raise ValueError(f"Unsafe target_file path: {target_file}")

    files_list: List[Dict[str, str]] = [
        {"path": "src/main.py", "content": REPO_ENTRYPOINT_HARNESS},
    ]
    unrelated_hashes: Dict[str, str] = {}
    found_target = False

    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise ValueError(f"Symlink forbidden in repository workspace: {p}")
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(root).parts):
            continue
        if rel == norm_target:
            found_target = True
            files_list.append({"path": f"src/{rel}", "content": patched_content})
        else:
            raw_bytes = p.read_bytes()
            unrelated_hashes[rel] = hashlib.sha256(raw_bytes).hexdigest()
            if rel.endswith(".py") and not rel.startswith("tests/"):
                files_list.append({
                    "path": f"src/{rel}",
                    "content": raw_bytes.decode("utf-8"),
                })

    if not found_target:
        raise ValueError(f"Target file '{norm_target}' not found in workspace '{root}'")

    bundle = {
        "schema": "qc.code_bundle.v1",
        "files": sorted(files_list, key=lambda x: x["path"]),
    }
    return bundle, unrelated_hashes


def load_protected_holdout_suite(
    holdout_path_or_dir: str,
) -> Tuple[Dict[str, Any], List[str]]:
    """Load declarative black-box `qc.json_io_cases.v1` cases from `holdout_cases.json` or `cases.json`.

    Rejects directories that only contain Python `unittest` files so expected answers
    never enter the candidate Python interpreter.
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

    raw_doc = strict(candidate_file.read_text(encoding="utf-8"))
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
    """Verify a candidate repository patch against declarative black-box cases using the
    configured protected verification transport (`vps_agent.independent_verifier`).

    Never executes candidate code if no protected verification transport is configured.
    """
    state = get_protected_repo_verifier_state()
    if not state["configured"] or _ACTIVE_TRANSPORT is None:
        return {
            "schema": "qc.protected_repo_verification.v1",
            "status": "held_protected_backend_required",
            "executed": False,
            "passed": False,
            "behavior_verified": False,
            "accepted": False,
            "protected_verification": False,
            "containment_backend": "held",
            "process_lifetime_and_output_control": False,
            "filesystem_and_network_containment": False,
            "hidden_oracle_separation": True,
            "independent_result_authority": True,
            "error": "Protected repository verifier transport is held; candidate code was not executed on the controller host.",
        }

    bundle, unrelated_hashes = build_repository_code_bundle(
        workspace_path=workspace_path,
        target_file=target_file,
        patched_content=patched_content,
    )
    testdoc, req_ids = load_protected_holdout_suite(holdout_test_dir)

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
    observations = receipt["body"]["observations"]
    all_cleanup = all(obs.get("cleanup_confirmed") is True for obs in observations)
    all_passed = len(cases_evaluated) > 0 and all(c["passed"] is True for c in cases_evaluated) and all_cleanup

    return {
        "schema": "qc.protected_repo_verification.v1",
        "status": "verified_for_scope" if all_passed else "failed",
        "executed": True,
        "passed": all_passed,
        "behavior_verified": all_passed,
        "accepted": all_passed,
        "certification_eligible": False,
        "protected_verification": True,
        "containment_backend": state["backend_kind"],
        "process_lifetime_and_output_control": True,
        "filesystem_and_network_containment": state["backend_kind"] == "linux_systemd_root_image_sandbox",
        "hidden_oracle_separation": True,
        "independent_result_authority": True,
        "grant_nonce": grant["nonce"],
        "grant_sha256": receipt["body"]["grant_sha256"],
        "source_sha256": source_sha,
        "tests_sha256": tests_sha,
        "image_sha256": image_sha256,
        "tests_run": len(cases_evaluated),
        "failures": sum(1 for c in cases_evaluated if not c["passed"]),
        "errors": sum(1 for obs in observations if obs.get("reason") != "completed" or obs.get("exit_code") != 0),
        "cleanup_confirmed": all_cleanup,
        "cases": cases_evaluated,
        "observations": observations,
        "receipt": receipt,
    }


def make_local_blackbox_isolated_transport() -> Callable[[Dict[str, Any], bytes], Dict[str, Any]]:
    """Create a local out-of-process black-box verifier transport for fast non-SSH unit tests.

    Guarantees:
    - Validates `grant` via `validate_grant(grant)`.
    - Consumes `grant['nonce']` in a private consumed-nonce set (preventing replay).
    - Stages ONLY `source_json` files and the single-case `case['input']` (`_qc_input.json`)
      into an isolated temporary directory per case.
    - NEVER writes `case['expected']`, `tests_json`, `grant`, or `key` into the child
      directory or environment.
    - Evaluates `strict_json_equal` outside the child process via `check_receipt`.
    - Marked as `local_blackbox_process_isolated_oracle` (`filesystem_and_network_containment=False`),
      never claiming Linux kernel mount/network containment.
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

        for idx, case in enumerate(cases):
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

            unit_id = f"qc-sandbox-{uuid.uuid4().hex}.service"
            observations.append({
                "case_id": case["id"],
                "executed": True,
                "reason": proc.reason,
                "exit_code": int(proc.returncode) if proc.returncode is not None else 1,
                "cleanup_confirmed": bool(proc.cleanup_confirmed),
                "stdout": (proc.stdout or "")[:65536],
                "unit": unit_id,
            })
            if not proc.cleanup_confirmed:
                raise RuntimeError("Unconfirmed process cleanup; verifier stopped")

        return receipt_for(grant, observations, key)

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

    return _transport
