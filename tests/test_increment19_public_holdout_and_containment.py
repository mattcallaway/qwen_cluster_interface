"""Increment 19 — Independent Public & Holdout Suite Execution and Universal Containment Regressions.

Covers all 10 required acceptance checks from Section 4 plus live Linux root-image containment checks:
1. Passing holdouts plus a failing public suite rejects the candidate (and reports the public failure).
2. Passing public tests plus failing holdouts rejects the candidate.
3. An unsupported required public suite returns `not_evaluated` with a specific reason and blocks acceptance.
4. An empty public suite (`0` discovered tests) does not pass and blocks acceptance.
5. Missing containment prevents execution through every exposed entry point (`apply_and_verify_repository_patch`
   with or without holdouts, `run_repository_test_suite`, `run_bounded_python_verification`,
   `GLOBAL_TOOL_CATALOGUE.execute_tool`, and `advance_deliberate_project`).
6. Backend startup failure, timeout, and lost availability never trigger host execution.
7. Fake success text (`SUMMARY:` / JSON) and forged candidate result files cannot produce acceptance.
8. Public and holdout evidence remain distinct and bound to the correct candidate and suite digests.
9. Cleanup failure (`cleanup_confirmed=False`) is reported independently and blocks acceptance.
10. A supported valid candidate passes both suites within containment, and backend names / caller flags
    alone cannot establish `filesystem_and_network_containment=True` on an uncontained test double.
11. Live Linux root-image containment check on `qwen-laptop` (`100.101.187.115`) verifying public and
    holdout suites execute independently inside `linux_systemd_root_image_sandbox` and cannot access
    host sentinels/secrets, write outside allowed locations, use prohibited networking, or leave child processes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
import pytest

from app.config import settings
from app.database import init_db
from app.services.orchestration import (
    deliberate_loop,
    engine,
    intake as intake_mod,
    protected_repo_verifier,
    tool_catalogue,
)
from app.services.orchestration.common import new_id

LAPTOP_HOST = "100.101.187.115"
LAPTOP_USER = "emceemcee"
SSH_KEY_PATH = str(Path.home() / ".ssh" / "id_ed25519")
EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "verified-work"
    / "evidence"
    / "increment19-public-holdout-containment-20261104.json"
)


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "qc_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "data_dir", data_dir)
    init_db()
    protected_repo_verifier.configure_protected_repo_verifier(None)
    yield tmp_path
    protected_repo_verifier.configure_protected_repo_verifier(None)


def _make_repo_and_holdouts(
    base_dir: Path,
    *,
    public_test_content: str,
    public_rel_path: str = "tests/test_public.py",
    holdout_cases_list: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Path, Path]:
    ws = base_dir / f"ws_{new_id('r')}"
    pkg = ws / "calc_pkg"
    pub_parent = (ws / public_rel_path).parent
    holdout_dir = base_dir / f"holdout_{new_id('h')}"
    pkg.mkdir(parents=True, exist_ok=True)
    pub_parent.mkdir(parents=True, exist_ok=True)
    holdout_dir.mkdir(parents=True, exist_ok=True)

    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "helper.py").write_text("HELPER_CONST = 42\n", encoding="utf-8")
    (pkg / "engine.py").write_text(
        "def evaluate_quota(base: int, bonus: int) -> dict:\n"
        "    return {'total': base - bonus, 'valid': False}\n",
        encoding="utf-8",
    )
    (ws / public_rel_path).write_text(public_test_content, encoding="utf-8")

    default_cases = holdout_cases_list or [
        {
            "id": "holdout_case_1",
            "requirement_id": "req_quota_sum",
            "input": {
                "module": "calc_pkg.engine",
                "function": "evaluate_quota",
                "args": [100, 25],
                "kwargs": {},
            },
            "expected": {"total": 125, "valid": True},
        },
        {
            "id": "holdout_case_2",
            "requirement_id": "req_quota_sum",
            "input": {
                "module": "calc_pkg.engine",
                "function": "evaluate_quota",
                "args": [0, 10],
                "kwargs": {},
            },
            "expected": {"total": 10, "valid": True},
        },
    ]
    (holdout_dir / "holdout_cases.json").write_text(
        json.dumps({"schema": "qc.json_io_cases.v1", "cases": default_cases}, indent=2),
        encoding="utf-8",
    )
    return ws, holdout_dir


VALID_ENGINE_CODE = (
    "def evaluate_quota(base: int, bonus: int) -> dict:\n"
    "    return {'total': int(base) + int(bonus), 'valid': True}\n"
)


def test_01_passing_holdouts_plus_failing_public_suite_rejects_candidate(isolated_env: Path) -> None:
    """Requirement 1: Passing holdouts plus a deliberately failing public suite must reject the candidate,
    and the public failure must appear in the final result and never be overwritten by holdout success.
    """
    deliberately_failing_public_test = (
        "import unittest\n"
        "from calc_pkg.engine import evaluate_quota\n\n"
        "class TestPublicDeliberateFailure(unittest.TestCase):\n"
        "    def test_public_passes_nominal(self):\n"
        "        self.assertEqual(evaluate_quota(10, 5), {'total': 15, 'valid': True})\n\n"
        "    def test_public_deliberate_failure(self):\n"
        "        # Deliberately expects 9999 so public suite fails while all holdouts pass\n"
        "        self.assertEqual(evaluate_quota(10, 5)['total'], 9999)\n"
    )
    ws, holdout_dir = _make_repo_and_holdouts(
        isolated_env,
        public_test_content=deliberately_failing_public_test,
    )
    orig_engine = (ws / "calc_pkg" / "engine.py").read_text(encoding="utf-8")

    # Even if a caller passes backend_kind="linux_systemd_root_image_sandbox" with a local double,
    # public tests must actually execute and fail!
    local_double = protected_repo_verifier.make_local_blackbox_isolated_transport()
    protected_repo_verifier.configure_protected_repo_verifier(
        local_double,
        backend_kind="linux_systemd_root_image_sandbox",
        nonproduction=True,
        allow_uncontained_test_double=True,
    )

    res = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws),
        target_file="calc_pkg/engine.py",
        patched_content=VALID_ENGINE_CODE,
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=str(holdout_dir),
    )

    # Holdouts passed (2/2), but public suite failed (1 failure out of 2 tests) -> overall REJECTED
    assert res["holdout_tests"] is not None
    assert res["holdout_tests"]["passed"] is True
    assert res["holdout_tests"]["tests_run"] == 2
    assert res["public_tests"] is not None
    assert res["public_tests"]["passed"] is False
    assert res["public_tests"]["status"] == "failed"
    assert res["public_tests"]["expected_test_count"] == 2
    assert res["public_tests"]["tests_run"] == 2
    assert res["public_tests"]["failures"] >= 1
    assert res["accepted"] is False
    assert res["status"] == "failed"
    # Verify target file was reverted on failure
    assert (ws / "calc_pkg" / "engine.py").read_text(encoding="utf-8") == orig_engine


def test_02_passing_public_tests_plus_failing_holdouts_rejects_candidate(isolated_env: Path) -> None:
    """Requirement 2: Passing public tests plus failing holdout tests must reject the candidate."""
    passing_public_test = (
        "import unittest\n"
        "from calc_pkg.engine import evaluate_quota\n\n"
        "class TestPublicPass(unittest.TestCase):\n"
        "    def test_only_checks_10_and_5(self):\n"
        "        self.assertEqual(evaluate_quota(10, 5), {'total': 15, 'valid': True})\n"
    )
    ws, holdout_dir = _make_repo_and_holdouts(
        isolated_env,
        public_test_content=passing_public_test,
    )
    # Candidate hardcodes (10, 5) to pass the public test, but fails holdout cases (100, 25) and (0, 10)
    overfitted_candidate = (
        "def evaluate_quota(base: int, bonus: int) -> dict:\n"
        "    if (base, bonus) == (10, 5):\n"
        "        return {'total': 15, 'valid': True}\n"
        "    return {'total': 0, 'valid': False}\n"
    )

    protected_repo_verifier.configure_protected_repo_verifier(
        protected_repo_verifier.make_local_blackbox_isolated_transport(),
        nonproduction=True,
        allow_uncontained_test_double=True,
    )

    res = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws),
        target_file="calc_pkg/engine.py",
        patched_content=overfitted_candidate,
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=str(holdout_dir),
    )

    assert res["public_tests"]["passed"] is True
    assert res["public_tests"]["tests_run"] == 1
    assert res["holdout_tests"]["passed"] is False
    assert res["holdout_tests"]["failures"] == 2
    assert res["accepted"] is False
    assert res["status"] == "failed"


def test_03_unsupported_public_suite_returns_not_evaluated_and_blocks_acceptance(isolated_env: Path) -> None:
    """Requirement 3: An unsupported required public suite (e.g. pytest-only standalone functions or
    unsupported file extension) must return `status='not_evaluated'` with a specific reason, never
    `passed`, and must block acceptance even when holdouts pass.
    """
    pytest_only_public_suite = (
        "import pytest\n"
        "from calc_pkg.engine import evaluate_quota\n\n"
        "@pytest.fixture\n"
        "def sample_pair():\n"
        "    return (10, 5)\n\n"
        "def test_standalone_pytest_function(sample_pair):\n"
        "    assert evaluate_quota(*sample_pair) == {'total': 15, 'valid': True}\n"
    )
    ws, holdout_dir = _make_repo_and_holdouts(
        isolated_env,
        public_test_content=pytest_only_public_suite,
    )

    protected_repo_verifier.configure_protected_repo_verifier(
        protected_repo_verifier.make_local_blackbox_isolated_transport(),
        nonproduction=True,
        allow_uncontained_test_double=True,
    )

    res = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws),
        target_file="calc_pkg/engine.py",
        patched_content=VALID_ENGINE_CODE,
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=str(holdout_dir),
    )

    assert res["public_tests"]["status"] == "not_evaluated"
    assert res["public_tests"]["passed"] is False
    assert "unsupported_public_test_framework" in str(res["public_tests"]["reason"])
    assert res["holdout_tests"]["passed"] is True
    assert res["accepted"] is False
    assert res["status"] == "not_evaluated"


def test_04_empty_public_suite_does_not_pass_and_blocks_acceptance(isolated_env: Path) -> None:
    """Requirement 4: Zero discovered tests in a public suite must not count as success and must block acceptance."""
    empty_unittest_suite = (
        "import unittest\n\n"
        "class EmptyTestCase(unittest.TestCase):\n"
        "    def helper_not_a_test(self):\n"
        "        return True\n"
    )
    ws, holdout_dir = _make_repo_and_holdouts(
        isolated_env,
        public_test_content=empty_unittest_suite,
    )

    protected_repo_verifier.configure_protected_repo_verifier(
        protected_repo_verifier.make_local_blackbox_isolated_transport(),
        nonproduction=True,
        allow_uncontained_test_double=True,
    )

    res = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws),
        target_file="calc_pkg/engine.py",
        patched_content=VALID_ENGINE_CODE,
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=str(holdout_dir),
    )

    assert res["public_tests"]["expected_test_count"] == 0
    assert res["public_tests"]["tests_run"] == 0
    assert res["public_tests"]["passed"] is False
    assert res["public_tests"]["reason"] == "zero_public_tests_discovered"
    assert res["accepted"] is False


def test_05_missing_containment_prevents_execution_across_every_entry_point(isolated_env: Path) -> None:
    """Requirement 5: When no containment backend is configured, candidate code must NEVER execute
    through any entry point (`apply_and_verify_repository_patch` with or without holdouts,
    `run_repository_test_suite`, `run_bounded_python_verification`, `execute_tool`, or `advance_deliberate_project`).
    """
    sentinel = isolated_env / "host_sentinel_must_not_exist.txt"
    sentinel_code = (
        f"import pathlib\n"
        f"pathlib.Path({str(sentinel)!r}).write_text('EXECUTED_ON_HOST')\n"
        f"def evaluate_quota(base: int, bonus: int) -> dict:\n"
        f"    return {{'total': base + bonus, 'valid': True}}\n"
    )
    pub_test = (
        "import unittest\n"
        "from calc_pkg.engine import evaluate_quota\n"
        "class TestPub(unittest.TestCase):\n"
        "    def test_one(self):\n"
        "        self.assertEqual(evaluate_quota(1, 2)['total'], 3)\n"
    )
    ws, holdout_dir = _make_repo_and_holdouts(isolated_env, public_test_content=pub_test)
    (ws / "calc_pkg" / "engine.py").write_text(sentinel_code, encoding="utf-8")

    # Ensure no containment backend is configured
    protected_repo_verifier.configure_protected_repo_verifier(None)

    # Entry point A: apply_and_verify_repository_patch WITH holdout_test_dir
    r_patch_holdout = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws),
        target_file="calc_pkg/engine.py",
        patched_content=sentinel_code,
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=str(holdout_dir),
    )
    assert r_patch_holdout["accepted"] is False
    assert r_patch_holdout["status"] == "held_protected_backend_required"
    assert not sentinel.exists(), "Sentinel created during apply_and_verify_repository_patch with holdouts!"

    # Entry point B: apply_and_verify_repository_patch WITHOUT holdout_test_dir
    r_patch_no_holdout = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws),
        target_file="calc_pkg/engine.py",
        patched_content=sentinel_code,
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=None,
    )
    assert r_patch_no_holdout["accepted"] is False
    assert r_patch_no_holdout["status"] == "held_containment_backend_required"
    assert not sentinel.exists(), "Sentinel created during apply_and_verify_repository_patch without holdouts!"

    # Entry point C: run_repository_test_suite directly
    r_pub_direct = tool_catalogue.run_repository_test_suite(
        workspace_path=str(ws),
        test_rel_path="tests/test_public.py",
    )
    assert r_pub_direct["passed"] is False
    assert r_pub_direct["status"] == "held_containment_backend_required"
    assert not sentinel.exists(), "Sentinel created during run_repository_test_suite!"

    # Entry point D: GLOBAL_TOOL_CATALOGUE.execute_tool("run_repository_test_suite")
    r_tool_pub = tool_catalogue.GLOBAL_TOOL_CATALOGUE.execute_tool(
        "run_repository_test_suite",
        {"workspace_path": str(ws), "test_rel_path": "tests/test_public.py"},
    )
    assert r_tool_pub["status"] == "held_containment_backend_required"
    assert not sentinel.exists()

    # Entry point E: run_bounded_python_verification & execute_and_test_code
    r_bounded = tool_catalogue.run_bounded_python_verification(
        code=sentinel_code,
        test_code="def test_x(): assert True\n",
    )
    assert r_bounded["accepted"] is False
    assert r_bounded["status"] == "held_containment_backend_required"
    assert not sentinel.exists(), "Sentinel created during run_bounded_python_verification!"

    r_tool_exec = tool_catalogue.GLOBAL_TOOL_CATALOGUE.execute_tool(
        "execute_and_test_code",
        {"code": sentinel_code, "test_code": "def test_x(): assert True\n", "filename": "solution.py", "timeout_seconds": 5.0},
    )
    assert r_tool_exec["status"] == "failed"
    assert r_tool_exec["result"]["status"] == "held_containment_backend_required"
    assert not sentinel.exists()

    # Entry point F: advance_deliberate_project on repository_bugfix domain when backend is held
    created = engine.create_project_with_run(
        name="Held Repo Project",
        actor="tester",
        intake={"objective": "Fix calc_pkg/engine.py"},
        autonomy_level=2,
    )
    pid = created["project"]["id"]
    rid = created["run"]["id"]
    ledger = intake_mod.parse_intake_requirements({
        "objective": "Fix calc_pkg/engine.py",
        "constraints": ["Target file: calc_pkg/engine.py", "Public tests: tests/test_public.py"],
        "deliverables": ["repo_inspection_and_reproduction.json", "verified_bugfix_patch.diff"],
    })
    intake_mod.persist_requirement_ledger(pid, ledger, run_id=rid, actor="tester")
    adv = deliberate_loop.advance_deliberate_project(
        pid,
        workspace_path=str(ws),
        holdout_test_dir=str(holdout_dir),
        max_steps=2,
        use_live_worker=False,
    )
    assert adv["final_cycle_phase"] == "blocked"
    assert not sentinel.exists(), "Sentinel created during advance_deliberate_project when backend was held!"


def test_06_backend_startup_failure_timeout_and_lost_availability_never_fallback_to_host(isolated_env: Path) -> None:
    """Requirement 6: Backend startup failure, timeout, or lost availability must fail/hold cleanly
    and NEVER fall back to executing candidate code on the controller host.
    """
    sentinel = isolated_env / "host_fallback_sentinel.txt"
    candidate_code = (
        f"import pathlib\n"
        f"pathlib.Path({str(sentinel)!r}).write_text('FALLBACK_EXECUTED')\n"
        f"def evaluate_quota(base: int, bonus: int) -> dict:\n"
        f"    return {{'total': base + bonus, 'valid': True}}\n"
    )
    pub_test = (
        "import unittest\n"
        "from calc_pkg.engine import evaluate_quota\n"
        "class TestPub(unittest.TestCase):\n"
        "    def test_one(self):\n"
        "        self.assertEqual(evaluate_quota(2, 3)['total'], 5)\n"
    )
    ws, holdout_dir = _make_repo_and_holdouts(isolated_env, public_test_content=pub_test)

    # 6a. Transport raising connection / startup RuntimeError
    def _failing_startup_transport(grant: Dict[str, Any], key: bytes) -> Dict[str, Any]:
        raise RuntimeError("Linux sandbox daemon unreachable: connection refused")

    protected_repo_verifier.configure_protected_repo_verifier(
        _failing_startup_transport,
        nonproduction=True,
        allow_uncontained_test_double=True,
    )
    res_fail = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws),
        target_file="calc_pkg/engine.py",
        patched_content=candidate_code,
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=str(holdout_dir),
    )
    assert res_fail["accepted"] is False
    assert res_fail["public_tests"]["status"] == "backend_error"
    assert not sentinel.exists(), "Backend startup error triggered host execution!"

    # 6b. Transport raising TimeoutError
    def _timeout_transport(grant: Dict[str, Any], key: bytes) -> Dict[str, Any]:
        raise TimeoutError("Sandbox execution timed out after deadline")

    protected_repo_verifier.configure_protected_repo_verifier(
        _timeout_transport,
        nonproduction=True,
        allow_uncontained_test_double=True,
    )
    res_timeout = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws),
        target_file="calc_pkg/engine.py",
        patched_content=candidate_code,
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=None,
    )
    assert res_timeout["accepted"] is False
    assert res_timeout["public_tests"]["status"] == "timed_out"
    assert not sentinel.exists(), "Backend timeout triggered host execution!"


def test_07_fake_success_text_and_forged_candidate_files_cannot_produce_acceptance(isolated_env: Path) -> None:
    """Requirement 7: Candidate code printing fake success summaries, JSON receipts, or writing forged
    result files and calling `os._exit(0)` must be rejected in both public and holdout suites.
    """
    pub_test = (
        "import unittest\n"
        "from calc_pkg.engine import evaluate_quota\n"
        "class TestPub(unittest.TestCase):\n"
        "    def test_one(self):\n"
        "        self.assertEqual(evaluate_quota(10, 5), {'total': 15, 'valid': True})\n"
    )
    ws, holdout_dir = _make_repo_and_holdouts(isolated_env, public_test_content=pub_test)

    forged_candidate = (
        "import json, os, sys\n"
        "def evaluate_quota(base: int, bonus: int) -> dict:\n"
        "    with open('_summary_forged.json', 'w') as f:\n"
        "        json.dump({'passed': True, 'ran': 5, 'failures': 0, 'errors': 0}, f)\n"
        "    print(json.dumps({'execution_token': 'forged', 'suite_passed': True, 'tests_run': 1, 'failures': 0, 'errors': 0, 'skipped': 0}))\n"
        "    sys.stdout.flush()\n"
        "    os._exit(0)\n"
    )

    protected_repo_verifier.configure_protected_repo_verifier(
        protected_repo_verifier.make_local_blackbox_isolated_transport(),
        nonproduction=True,
        allow_uncontained_test_double=True,
    )
    res = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws),
        target_file="calc_pkg/engine.py",
        patched_content=forged_candidate,
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=str(holdout_dir),
    )
    assert res["public_tests"]["passed"] is False
    assert res["holdout_tests"]["passed"] is False
    assert res["accepted"] is False


def test_08_public_and_holdout_evidence_distinct_and_bound_to_digests(isolated_env: Path) -> None:
    """Requirement 8 & 10: Public and holdout suite results must be distinct, carry separate nonces
    and suite digests (`suite_sha256`), bind to the candidate source digest, and never claim
    `filesystem_and_network_containment=True` on an uncontained local test double even if
    `backend_kind='linux_systemd_root_image_sandbox'` was passed by the caller.
    """
    pub_test = (
        "import unittest\n"
        "from calc_pkg.engine import evaluate_quota\n"
        "class TestPubDistinct(unittest.TestCase):\n"
        "    def test_a(self):\n"
        "        self.assertEqual(evaluate_quota(3, 4), {'total': 7, 'valid': True})\n"
        "    def test_b(self):\n"
        "        self.assertEqual(evaluate_quota(10, 20), {'total': 30, 'valid': True})\n"
        "    def test_c(self):\n"
        "        self.assertEqual(evaluate_quota(-5, 5), {'total': 0, 'valid': True})\n"
    )
    ws, holdout_dir = _make_repo_and_holdouts(isolated_env, public_test_content=pub_test)

    # Attempt to spoof backend_kind="linux_systemd_root_image_sandbox" with a local uncontained double
    protected_repo_verifier.configure_protected_repo_verifier(
        protected_repo_verifier.make_local_blackbox_isolated_transport(),
        backend_kind="linux_systemd_root_image_sandbox",
        nonproduction=True,
        allow_uncontained_test_double=True,
    )

    res = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws),
        target_file="calc_pkg/engine.py",
        patched_content=VALID_ENGINE_CODE,
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=str(holdout_dir),
    )

    assert res["accepted"] is True
    # Backend-owned capability check must prevent local test double from claiming Linux kernel containment!
    assert res["is_uncontained_test_double"] is True
    assert res["filesystem_and_network_containment"] is False
    assert res["containment"]["filesystem_and_network_containment"] is False
    assert res["protected_verification"] is False

    pub = res["public_tests"]
    hold = res["holdout_tests"]
    assert pub["suite_role"] == "public"
    assert hold["suite_role"] == "holdout"
    assert pub["tests_run"] == 3  # 3 unittest methods in TestPubDistinct
    assert hold["tests_run"] == 2  # 2 JSON I/O cases in holdout_cases.json
    assert pub["suite_sha256"] != hold["suite_sha256"]
    assert pub["grant_nonce"] != hold["grant_nonce"]
    assert pub["grant_sha256"] != hold["grant_sha256"]

    expected_holdout_sha = hashlib.sha256((holdout_dir / "holdout_cases.json").read_bytes()).hexdigest()
    assert hold["suite_sha256"] == expected_holdout_sha


def test_09_cleanup_failure_reported_independently_and_blocks_acceptance(isolated_env: Path) -> None:
    """Requirement 9: When process/cgroup cleanup cannot be confirmed (`cleanup_confirmed=False`),
    it must be reported independently in `containment` and block overall acceptance.
    """
    pub_test = (
        "import unittest\n"
        "from calc_pkg.engine import evaluate_quota\n"
        "class TestPub(unittest.TestCase):\n"
        "    def test_one(self):\n"
        "        self.assertEqual(evaluate_quota(1, 2), {'total': 3, 'valid': True})\n"
    )
    ws, holdout_dir = _make_repo_and_holdouts(isolated_env, public_test_content=pub_test)

    dirty_cleanup_transport = protected_repo_verifier.make_local_blackbox_isolated_transport(
        simulate_cleanup_failure=True
    )
    protected_repo_verifier.configure_protected_repo_verifier(
        dirty_cleanup_transport,
        nonproduction=True,
        allow_uncontained_test_double=True,
    )

    res = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws),
        target_file="calc_pkg/engine.py",
        patched_content=VALID_ENGINE_CODE,
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=str(holdout_dir),
    )

    assert res["accepted"] is False
    assert res["status"] == "cleanup_unverified"
    assert res["cleanup_confirmed"] is False
    assert res["containment"]["cleanup_confirmed"] is False
    chk_map = {c["name"]: c["passed"] for c in res["checks"]}
    assert chk_map["containment_and_cleanup_confirmed"] is False


def test_10_live_linux_root_image_independent_public_and_holdout_containment(isolated_env: Path) -> None:
    """Live Linux root-image verification on `qwen-laptop` (`100.101.187.115`):
    1. Passing holdouts + deliberately failing public suite inside `linux_systemd_root_image_sandbox` -> REJECTED.
    2. Valid candidate passing both 3-test public suite and 2-case holdout suite inside `linux_systemd_root_image_sandbox`
       -> ACCEPTED (`filesystem_and_network_containment=True`, `protected_verification=True`, distinct nonces & digests).
    3. Direct `run_repository_test_suite` (without holdouts) executes inside `linux_systemd_root_image_sandbox`
       and blocks access to host sentinels (`/etc/qwen-sandbox/test-host.json`), outside writes, and network sockets.
    """
    if not Path(SSH_KEY_PATH).exists():
        pytest.skip(f"SSH key not present at {SSH_KEY_PATH}")

    recorded_runs: List[Dict[str, Any]] = []
    ssh_transport = protected_repo_verifier.make_laptop_ssh_verifier_transport(
        host=LAPTOP_HOST,
        user=LAPTOP_USER,
        key_filename=SSH_KEY_PATH,
        record_runs=recorded_runs,
    )
    protected_repo_verifier.configure_protected_repo_verifier(
        ssh_transport,
        backend_kind="linux_systemd_root_image_sandbox",
        nonproduction=True,
    )

    # Case A: Live Linux run where holdouts pass (2/2) but public suite has a deliberate failure (1/2 fails)
    failing_pub_test = (
        "import unittest\n"
        "from calc_pkg.engine import evaluate_quota\n\n"
        "class TestLiveLinuxPublicFail(unittest.TestCase):\n"
        "    def test_ok(self):\n"
        "        self.assertEqual(evaluate_quota(10, 5), {'total': 15, 'valid': True})\n"
        "    def test_deliberate_fail_on_linux(self):\n"
        "        self.assertEqual(evaluate_quota(10, 5)['total'], -999)\n"
    )
    ws_fail, hold_fail = _make_repo_and_holdouts(isolated_env, public_test_content=failing_pub_test)
    live_fail_res = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws_fail),
        target_file="calc_pkg/engine.py",
        patched_content=VALID_ENGINE_CODE,
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=str(hold_fail),
    )
    assert live_fail_res["holdout_tests"]["passed"] is True
    assert live_fail_res["holdout_tests"]["filesystem_and_network_containment"] is True
    assert live_fail_res["public_tests"]["passed"] is False
    assert live_fail_res["public_tests"]["filesystem_and_network_containment"] is True
    assert live_fail_res["public_tests"]["tests_run"] == 2
    assert live_fail_res["public_tests"]["failures"] == 1
    assert live_fail_res["accepted"] is False

    # Case B: Live Linux run where public test probes containment boundaries (sentinel read, outside write, network)
    # and verifies that all prohibited operations are blocked inside the root-image sandbox while valid assertions pass!
    containment_probing_pub_test = (
        "import os, pathlib, socket, unittest\n"
        "from calc_pkg.engine import evaluate_quota\n\n"
        "class TestLiveLinuxPublicContained(unittest.TestCase):\n"
        "    def test_01_host_sentinel_and_secrets_inaccessible(self):\n"
        "        self.assertFalse(pathlib.Path('/etc/qwen-sandbox/test-host.json').exists())\n"
        "        self.assertFalse(pathlib.Path('/var/lib/qwen-sandbox/verifier-audit').exists())\n"
        "        for k in os.environ:\n"
        "            self.assertNotIn('SECRET', k.upper())\n\n"
        "    def test_02_outside_writes_and_network_blocked(self):\n"
        "        with self.assertRaises(OSError):\n"
        "            pathlib.Path('/src/src/calc_pkg/helper.py').write_text('pwn')\n"
        "        with self.assertRaises(OSError):\n"
        "            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "            s.settimeout(1.0)\n"
        "            s.connect(('1.1.1.1', 80))\n\n"
        "    def test_03_functional_quota_calculation(self):\n"
        "        self.assertEqual(evaluate_quota(40, 2), {'total': 42, 'valid': True})\n"
    )
    ws_ok, hold_ok = _make_repo_and_holdouts(isolated_env, public_test_content=containment_probing_pub_test)
    live_ok_res = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws_ok),
        target_file="calc_pkg/engine.py",
        patched_content=VALID_ENGINE_CODE,
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=str(hold_ok),
    )
    assert live_ok_res["accepted"] is True
    assert live_ok_res["status"] == "verified_for_scope"
    assert live_ok_res["protected_verification"] is True
    assert live_ok_res["filesystem_and_network_containment"] is True
    assert live_ok_res["cleanup_confirmed"] is True
    assert live_ok_res["public_tests"]["passed"] is True
    assert live_ok_res["public_tests"]["tests_run"] == 3
    assert live_ok_res["public_tests"]["filesystem_and_network_containment"] is True
    assert live_ok_res["holdout_tests"]["passed"] is True
    assert live_ok_res["holdout_tests"]["tests_run"] == 2
    assert live_ok_res["holdout_tests"]["filesystem_and_network_containment"] is True
    assert live_ok_res["public_tests"]["grant_nonce"] != live_ok_res["holdout_tests"]["grant_nonce"]

    # Case C: Direct public-only call (`run_repository_test_suite` without holdouts) also executes inside Linux root-image sandbox
    direct_pub_live = tool_catalogue.run_repository_test_suite(
        workspace_path=str(ws_ok),
        test_rel_path="tests/test_public.py",
    )
    assert direct_pub_live["passed"] is True
    assert direct_pub_live["tests_run"] == 3
    assert direct_pub_live["containment_backend"] == "linux_systemd_root_image_sandbox"
    assert direct_pub_live["filesystem_and_network_containment"] is True
    assert direct_pub_live["cleanup_confirmed"] is True

    evidence_doc = {
        "schema": "qc.increment19.public_holdout_containment_live_evidence.v1",
        "host": LAPTOP_HOST,
        "image_sha256": protected_repo_verifier.PINNED_LINUX_ROOT_IMAGE_SHA256,
        "case_a_holdouts_pass_public_fails_rejected": {
            "accepted": live_fail_res["accepted"],
            "status": live_fail_res["status"],
            "public_tests": {
                "suite_id": live_fail_res["public_tests"]["suite_id"],
                "suite_sha256": live_fail_res["public_tests"]["suite_sha256"],
                "candidate_source_sha256": live_fail_res["public_tests"]["candidate_source_sha256"],
                "passed": live_fail_res["public_tests"]["passed"],
                "tests_run": live_fail_res["public_tests"]["tests_run"],
                "expected_test_count": live_fail_res["public_tests"]["expected_test_count"],
                "failures": live_fail_res["public_tests"]["failures"],
                "grant_nonce": live_fail_res["public_tests"]["grant_nonce"],
                "filesystem_and_network_containment": live_fail_res["public_tests"]["filesystem_and_network_containment"],
                "cleanup_confirmed": live_fail_res["public_tests"]["cleanup_confirmed"],
            },
            "holdout_tests": {
                "suite_id": live_fail_res["holdout_tests"]["suite_id"],
                "suite_sha256": live_fail_res["holdout_tests"]["suite_sha256"],
                "candidate_source_sha256": live_fail_res["holdout_tests"]["candidate_source_sha256"],
                "passed": live_fail_res["holdout_tests"]["passed"],
                "tests_run": live_fail_res["holdout_tests"]["tests_run"],
                "failures": live_fail_res["holdout_tests"]["failures"],
                "grant_nonce": live_fail_res["holdout_tests"]["grant_nonce"],
                "filesystem_and_network_containment": live_fail_res["holdout_tests"]["filesystem_and_network_containment"],
                "cleanup_confirmed": live_fail_res["holdout_tests"]["cleanup_confirmed"],
            },
        },
        "case_b_both_suites_pass_inside_linux_containment": {
            "accepted": live_ok_res["accepted"],
            "status": live_ok_res["status"],
            "protected_verification": live_ok_res["protected_verification"],
            "containment": live_ok_res["containment"],
            "public_tests": {
                "suite_id": live_ok_res["public_tests"]["suite_id"],
                "suite_sha256": live_ok_res["public_tests"]["suite_sha256"],
                "candidate_source_sha256": live_ok_res["public_tests"]["candidate_source_sha256"],
                "passed": live_ok_res["public_tests"]["passed"],
                "tests_run": live_ok_res["public_tests"]["tests_run"],
                "grant_nonce": live_ok_res["public_tests"]["grant_nonce"],
            },
            "holdout_tests": {
                "suite_id": live_ok_res["holdout_tests"]["suite_id"],
                "suite_sha256": live_ok_res["holdout_tests"]["suite_sha256"],
                "candidate_source_sha256": live_ok_res["holdout_tests"]["candidate_source_sha256"],
                "passed": live_ok_res["holdout_tests"]["passed"],
                "tests_run": live_ok_res["holdout_tests"]["tests_run"],
                "grant_nonce": live_ok_res["holdout_tests"]["grant_nonce"],
            },
        },
        "case_c_direct_public_suite_contained": {
            "passed": direct_pub_live["passed"],
            "tests_run": direct_pub_live["tests_run"],
            "containment_backend": direct_pub_live["containment_backend"],
            "filesystem_and_network_containment": direct_pub_live["filesystem_and_network_containment"],
            "cleanup_confirmed": direct_pub_live["cleanup_confirmed"],
            "grant_nonce": direct_pub_live["grant_nonce"],
        },
        "total_ssh_sandbox_grants_executed": len(recorded_runs),
        "all_nonce_replays_denied": all(r.get("nonce_replay_denied") is True for r in recorded_runs),
    }
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(json.dumps(evidence_doc, indent=2), encoding="utf-8")

