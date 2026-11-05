"""Increment 19 Independent Review Remediation Regressions.

Tests the three core defects identified in `INCREMENT_19_INDEPENDENT_REVIEW.md` at
commit `301c15ddb6cf487df9d1d73547cbe9f8def7c5bf`:

1. `run_repository_test_suite` and `apply_and_verify_repository_patch` must not
   import candidate code and holdout tests into the same host interpreter, allow
   candidate stdout/unittest tampering to establish a pass, or claim protected
   verification on an uncontained host when no protected verifier backend is configured.
2. Coordinator repair prompts in `deliberate_loop.py` must not embed repository-specific
   solution code (`on_hand - reserved`, `reverse=False`) or inline fault-injection prompts.
3. Action/tool selection metrics must report `proposal_validation_rate` (`"not_evaluated"`
   when `total_proposals == 0`, never `1.0`), record action origin (`template`,
   `caller_supplied_contract`, `model_proposal`, `controller_policy`), never claim
   `model_tool_selection_accuracy` for template-fixed actions, and separate
   `proposal_validity`, `action_appropriateness`, `execution_outcome`, and `task_outcome`.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from app.services.orchestration import (
    deliberate_loop,
    engine,
    intake as intake_mod,
    tool_catalogue,
    working_memory as wm,
)
from app.services.orchestration.common import new_id


def test_01_host_in_interpreter_holdout_and_stdout_spoofing_rejected(tmp_path: Path) -> None:
    """Defect 1: Candidate code that prints fake SUMMARY and exits 0, or runs without a
    configured protected backend, must never pass or claim protected verification.
    """
    ws = tmp_path / "spoof_repo"
    pkg = ws / "pkg"
    tests_dir = ws / "tests"
    holdout_dir = tmp_path / "holdout"
    pkg.mkdir(parents=True)
    tests_dir.mkdir(parents=True)
    holdout_dir.mkdir(parents=True)

    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "calc.py").write_text(
        "def compute(x: int) -> int:\n    return x - 1\n",
        encoding="utf-8",
    )
    (tests_dir / "test_public.py").write_text(
        "import unittest\nfrom pkg.calc import compute\n"
        "class TestPublic(unittest.TestCase):\n"
        "    def test_one(self):\n"
        "        self.assertEqual(compute(5), 6)\n",
        encoding="utf-8",
    )
    (holdout_dir / "test_holdout.py").write_text(
        "import unittest\nfrom pkg.calc import compute\n"
        "class TestHoldout(unittest.TestCase):\n"
        "    def test_secret(self):\n"
        "        self.assertEqual(compute(10), 11)\n",
        encoding="utf-8",
    )

    # 1a. Host `run_repository_test_suite` must NOT accept `extra_test_dir` to run holdouts in-interpreter,
    # and must explicitly mark its output as public development diagnostics only (never protected verification).
    spoof_code = (
        "import os, sys\n"
        "print('SUMMARY: ran=3 failures=0 errors=0', flush=True)\n"
        "os._exit(0)\n"
    )
    (pkg / "calc.py").write_text(spoof_code, encoding="utf-8")
    pub_res = tool_catalogue.run_repository_test_suite(
        workspace_path=str(ws),
        test_rel_path="tests/test_public.py",
    )
    assert pub_res["passed"] is False, "Candidate printing fake SUMMARY + os._exit(0) must not pass!"
    assert pub_res.get("protected_verification") is False
    assert pub_res.get("hidden_oracle_separated") is False
    assert pub_res.get("verification_scope") == "public_development_diagnostics_only"

    # 1b. Without a configured protected verification backend, `apply_and_verify_repository_patch`
    # must HOLD candidate execution rather than running holdout tests in a host unittest process.
    (pkg / "calc.py").write_text("def compute(x: int) -> int:\n    return x - 1\n", encoding="utf-8")
    patch_res = tool_catalogue.apply_and_verify_repository_patch(
        workspace_path=str(ws),
        target_file="pkg/calc.py",
        patched_content="def compute(x: int) -> int:\n    return x + 1\n",
        public_test_rel_path="tests/test_public.py",
        holdout_test_dir=str(holdout_dir),
    )
    assert patch_res["accepted"] is False
    assert patch_res["status"] == "held_protected_backend_required"
    assert patch_res.get("protected_verification") is False


def test_02_no_embedded_domain_solution_in_coordinator_repair_prompt() -> None:
    """Defect 2: `deliberate_loop.py` must not contain hardcoded `on_hand`/`reserved` or `reverse=False`
    repair instructions or inline fault-injection prompts.
    """
    src = inspect.getsource(deliberate_loop)
    forbidden_snippets = [
        "int(b.get('on_hand', 0)) - int(b.get('reserved', 0))",
        "ascending FEFO sort (`reverse=False`)",
        "keeping the existing `sort(reverse=True)` line unchanged",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in src, f"Embedded domain solution/hook still present in deliberate_loop.py: {snippet}"


def test_03_action_validation_rate_and_origin_separation() -> None:
    """Defect 3: Zero proposals must report `'not_evaluated'` (never 1.0), template-selected actions
    must record `action_origin='template'` and `model_tool_selection_accuracy='not_evaluated'`,
    and each step must separate `proposal_validity`, `action_appropriateness`, `execution_outcome`, and `task_outcome`.
    """
    created = engine.create_project_with_run(
        name=f"Metrics Separation Test {new_id('p')}",
        actor="tester",
        intake={"objective": "Evaluate supplier quotes under budget $40,000 and delivery <= 14 days for 10 units."},
        autonomy_level=2,
    )
    pid = created["project"]["id"]
    run_id = created["run"]["id"]
    ledger = intake_mod.parse_intake_requirements({
        "objective": "Evaluate supplier quotes under budget $40,000 and delivery <= 14 days for 10 units.",
        "constraints": ["Maximum budget $40,000", "Delivery <= 14 days", "Quantity: 10 units"],
        "deliverables": ["resolved_supplier_quotes.json", "option_comparison.json", "supplier_recommendation.md"],
    })
    intake_mod.persist_requirement_ledger(pid, ledger, run_id=run_id, actor="tester")

    # Run 1 step of supplier template workflow (with explicit quotes per AUD-01)
    from app.services.orchestration import acceptance_harness
    res = deliberate_loop.advance_deliberate_project(
        project_id=pid,
        quotes_data=acceptance_harness.SYNTHETIC_SUPPLIER_QUOTES,
        use_live_worker=False,
        max_steps=1,
    )
    stats = res["tool_selection_stats"]
    assert "proposal_validation_rate" in stats
    assert stats.get("model_tool_selection_accuracy") == "not_evaluated"
    assert stats["action_origin_counts"]["template"] >= 1
    assert stats["action_origin_counts"]["model_proposal"] == 0

    step0 = res["steps_executed"][0]
    assert step0["action_origin"] == "template"
    assert step0["proposal_validity"] is True
    assert step0["execution_outcome"] == "succeeded"
    assert step0["task_outcome"] == "verified"

    # Also verify 0-proposal case when project has no runnable contracts
    wm.set_project_dispatch_stopped(pid, stopped=True)
    res_zero = deliberate_loop.advance_deliberate_project(
        project_id=pid,
        use_live_worker=False,
        max_steps=1,
    )
    zero_stats = res_zero["tool_selection_stats"]
    assert zero_stats["total_proposals"] == 0
    assert zero_stats["proposal_validation_rate"] == "not_evaluated"
    assert zero_stats.get("tool_selection_accuracy") != 1.0


def test_04_independent_action_appropriateness_judged_cases() -> None:
    """Section 6: Independently judged cases where the correct action is inspection, testing,
    retrieval, clarification, waiting, stopping, or reporting a blocker, distinguishing
    syntactically valid but inappropriate actions from appropriate ones.
    """
    assert hasattr(deliberate_loop, "evaluate_next_action_appropriateness"), (
        "deliberate_loop must implement evaluate_next_action_appropriateness"
    )
    assert hasattr(deliberate_loop, "run_action_selection_benchmark"), (
        "deliberate_loop must implement run_action_selection_benchmark"
    )
    bench = deliberate_loop.run_action_selection_benchmark()
    assert bench["total_cases"] >= 7
    assert bench["appropriate_cases_passed"] == bench["total_cases"]
    assert bench["inappropriate_but_syntactically_valid_rejected"] >= 3
