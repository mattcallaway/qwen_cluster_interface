"""Increment 19 — Section 5: Multi-Variant Repository Repair Transfer E2E Verification.

Evaluates the general-purpose coordinator (`deliberate_loop.py`) across 3 distinct, independently
specified repository defect variants whose solutions are completely absent from coordinator code
and prompts:
1. Variant A (Boundary Handling): `sla_credit_calculator/credits.py` (`calculate_sla_credit`)
2. Variant B (Ordering / Selection Behavior): `incident_router/router.py` (`select_oncall_engineer`)
3. Variant C (State / Aggregation Logic + Labeled Fixture Fault Injection Recovery):
   `ledger_reconciler/reconcile.py` (`summarize_account_balances`)

All 3 variants execute end-to-end through:
- Separately running FastAPI/Uvicorn controller (`POST /api/v1/chat/deliberate`)
- Separately running authenticated `VPSWorkerAgent` (`qwen2.5:3b` on local Ollama)
- Live SSH protected Linux verifier (`vps_agent.independent_verifier.run_protected` inside
  `linux_systemd_root_image_sandbox` on `qwen-laptop` `100.101.187.115`, image
  `43b057aa6a7133de2bbed57a8caaeecd07aea9228fa7cb6bec1608286b967b61`).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Tuple

import httpx
import pytest

from app.services.orchestration import protected_repo_verifier

LAPTOP_HOST = "100.101.187.115"
LAPTOP_USER = "emceemcee"
SSH_KEY_PATH = str(Path.home() / ".ssh" / "id_ed25519")
EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "verified-work"
    / "evidence"
    / "increment19-fresh-variants-transfer-20261104.json"
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_health(base_url: str, timeout_s: float = 25.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/health", timeout=2.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Controller at {base_url} did not become healthy within {timeout_s}s")


def _start_controller_with_protected_linux_verifier(
    repo_root: Path,
    data_dir: Path,
    port: int,
    secret_key: str,
) -> subprocess.Popen:
    env = os.environ.copy()
    env["QC_DATA_DIR"] = str(data_dir)
    env["QC_ALLOWED_WORKSPACE_ROOTS"] = str(data_dir.parent)
    env["QC_VERIFIER_HOLDOUT_ROOTS"] = str(data_dir.parent)
    env["QC_ENV"] = "development"
    env["QC_SECRET_KEY"] = secret_key
    env["QC_ORCHESTRATOR_ENABLED"] = "0"
    env["QC_PAGED_KV_ENABLED"] = "false"
    env["QC_GIT_REVISION"] = "HEAD"
    log_path = data_dir / f"controller_variants_{port}.log"
    log_fh = open(log_path, "a", encoding="utf-8")

    # Configure the non-production Linux root-image SSH verifier inside the isolated controller subprocess
    runner_code = (
        "import uvicorn\n"
        "from app.services.orchestration import protected_repo_verifier\n"
        f"transport = protected_repo_verifier.make_laptop_ssh_verifier_transport(host={LAPTOP_HOST!r}, user={LAPTOP_USER!r}, key_filename={SSH_KEY_PATH!r})\n"
        "protected_repo_verifier.configure_protected_repo_verifier(transport, backend_kind='linux_systemd_root_image_sandbox', nonproduction=True)\n"
        f"uvicorn.run('app.main:app', host='127.0.0.1', port={port}, log_level='warning')\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", runner_code],
        cwd=str(repo_root),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _wait_for_health(f"http://127.0.0.1:{port}")
    return proc


def _start_worker_daemon_subprocess(
    repo_root: Path,
    worker_data_dir: Path,
    controller_url: str,
    worker_id: str,
    enrollment_token: str,
) -> subprocess.Popen:
    env = os.environ.copy()
    env["QC_CONTROLLER_URL"] = controller_url
    env["QC_WORKER_ID"] = worker_id
    env["QC_WORKER_NAME"] = "Live Worker Fresh Variants Inc19"
    env["QC_WORKER_ROLE"] = "vps_worker"
    env["QC_ENROLLMENT_TOKEN"] = enrollment_token
    env["QC_WORKER_DATA_DIR"] = str(worker_data_dir)
    env["QC_POLL_INTERVAL"] = "1"
    env["QC_HEARTBEAT_INTERVAL"] = "5"
    env["QC_LEASE_DURATION"] = "120"
    log_path = worker_data_dir / "worker_variants.log"
    log_fh = open(log_path, "a", encoding="utf-8")
    worker_code = (
        "import time\n"
        "from vps_agent.agent import VPSWorkerAgent\n"
        "agent = VPSWorkerAgent()\n"
        "assert agent.register(), 'Worker failed to register'\n"
        "agent.send_heartbeat()\n"
        "while True:\n"
        "    if not agent.is_registered:\n"
        "        agent.register()\n"
        "    agent.send_heartbeat()\n"
        "    agent.poll_and_execute_task()\n"
        "    time.sleep(0.35)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", worker_code],
        cwd=str(repo_root),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _hash_directory_files(root: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# ---------------------------------------------------------------------------
# Variant Fixture Builders (Frozen before evaluation)
# ---------------------------------------------------------------------------

def _build_variant_a_sla_boundary(base_dir: Path) -> Tuple[Path, Path, Dict[str, Any]]:
    """Variant A (Boundary Handling): `sla_credit_calculator/credits.py`."""
    ws = base_dir / "variant_a_sla_repo"
    pkg = ws / "sla_credit_calculator"
    tests_dir = ws / "tests"
    holdout_dir = base_dir / "variant_a_holdout"
    pkg.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    holdout_dir.mkdir(parents=True, exist_ok=True)

    (pkg / "__init__.py").write_text("from .credits import calculate_sla_credit\n", encoding="utf-8")
    (pkg / "formatting.py").write_text(
        '"""Unrelated SLA invoice formatting helper."""\n'
        "def format_cents_usd(cents: int) -> str:\n"
        "    return f'${cents / 100:.2f}'\n",
        encoding="utf-8",
    )
    (ws / "README.md").write_text("# SLA Credit Calculator\nComputes service-level credit tiers in basis points.\n", encoding="utf-8")

    buggy_credits = '''"""SLA uptime basis-point and credit tier calculator."""
from __future__ import annotations
from typing import Any, Dict


def calculate_sla_credit(
    monthly_fee_cents: int,
    downtime_minutes: int,
    total_minutes: int = 43200,
) -> Dict[str, Any]:
    """Calculate SLA uptime in integer basis points (0..10000) and credit amount.

    Specification rules:
    1. If `monthly_fee_cents < 0` or `total_minutes <= 0`, raise `ValueError`.
    2. Clamp `downtime_minutes` to `[0, total_minutes]` so `clamped_downtime = min(max(int(downtime_minutes), 0), int(total_minutes))`.
    3. Compute `uptime_minutes = total_minutes - clamped_downtime` and integer basis points:
       `uptime_bps = (uptime_minutes * 10000) // total_minutes`.
    4. Determine `credit_rate_pct` using inclusive upper boundaries on `uptime_bps`:
       - If `uptime_bps >= 9990`: `credit_rate_pct = 0`
       - Else if `uptime_bps >= 9900`: `credit_rate_pct = 10`
       - Else if `uptime_bps >= 9500`: `credit_rate_pct = 25`
       - Else (`uptime_bps < 9500`): `credit_rate_pct = 50`
    5. Compute `credit_cents = (int(monthly_fee_cents) * credit_rate_pct) // 100`.
    6. Return `{"uptime_bps": int, "credit_rate_pct": int, "credit_cents": int, "sla_breached": bool}`
       where `sla_breached` is `credit_rate_pct > 0`.
    """
    if monthly_fee_cents < 0 or total_minutes <= 0:
        raise ValueError("Invalid fee or total_minutes")

    # BUG 1: Does not clamp downtime_minutes to [0, total_minutes]
    uptime_minutes = total_minutes - int(downtime_minutes)
    uptime_bps = (uptime_minutes * 10000) // total_minutes

    # BUG 2: Off-by-one boundary comparisons (> instead of >=) and wrong tier assignment
    if uptime_bps > 9990:
        credit_rate_pct = 0
    elif uptime_bps > 9900:
        credit_rate_pct = 10
    elif uptime_bps > 9500:
        credit_rate_pct = 25
    else:
        credit_rate_pct = 50

    credit_cents = (int(monthly_fee_cents) * credit_rate_pct) // 100
    return {
        "uptime_bps": uptime_bps,
        "credit_rate_pct": credit_rate_pct,
        "credit_cents": credit_cents,
        "sla_breached": credit_rate_pct > 0,
    }
'''
    (pkg / "credits.py").write_text(buggy_credits, encoding="utf-8")

    pub_test = '''import unittest
from sla_credit_calculator.credits import calculate_sla_credit


class TestSlaCreditsPublic(unittest.TestCase):
    def test_exact_tier_boundaries(self):
        # 43200 total minutes: 432 minutes downtime -> exactly 9900 bps -> should be 10% credit (not 25%)
        res_9900 = calculate_sla_credit(100000, 432, 43200)
        self.assertEqual(res_9900["uptime_bps"], 9900)
        self.assertEqual(res_9900["credit_rate_pct"], 10)
        self.assertEqual(res_9900["credit_cents"], 10000)
        self.assertTrue(res_9900["sla_breached"])

        # 43 minutes downtime -> 42768/43200 -> 9990 bps -> should be 0% credit
        res_9990 = calculate_sla_credit(100000, 43, 43200)
        self.assertEqual(res_9990["uptime_bps"], 9990)
        self.assertEqual(res_9990["credit_rate_pct"], 0)
        self.assertFalse(res_9990["sla_breached"])

    def test_downtime_clamping(self):
        # Negative downtime clamped to 0 -> 10000 bps -> 0% credit
        res_neg = calculate_sla_credit(50000, -15, 1000)
        self.assertEqual(res_neg["uptime_bps"], 10000)
        self.assertEqual(res_neg["credit_cents"], 0)
        # Downtime exceeding total_minutes clamped to total_minutes -> 0 bps -> 50% credit
        res_over = calculate_sla_credit(50000, 2500, 1000)
        self.assertEqual(res_over["uptime_bps"], 0)
        self.assertEqual(res_over["credit_rate_pct"], 50)
        self.assertEqual(res_over["credit_cents"], 25000)


if __name__ == "__main__":
    unittest.main()
'''
    (tests_dir / "test_credits_public.py").write_text(pub_test, encoding="utf-8")

    holdout_cases = {
        "schema": "qc.json_io_cases.v1",
        "cases": [
            {
                "id": "sla_boundary_9990_bps",
                "requirement_id": "req_inclusive_tier_boundaries",
                "input": {
                    "module": "sla_credit_calculator.credits",
                    "function": "calculate_sla_credit",
                    "args": [200000, 1, 1000],
                    "kwargs": {},
                },
                "expected": {"uptime_bps": 9990, "credit_rate_pct": 0, "credit_cents": 0, "sla_breached": False},
            },
            {
                "id": "sla_boundary_9900_bps",
                "requirement_id": "req_inclusive_tier_boundaries",
                "input": {
                    "module": "sla_credit_calculator.credits",
                    "function": "calculate_sla_credit",
                    "args": [200000, 10, 1000],
                    "kwargs": {},
                },
                "expected": {"uptime_bps": 9900, "credit_rate_pct": 10, "credit_cents": 20000, "sla_breached": True},
            },
            {
                "id": "sla_boundary_9500_bps",
                "requirement_id": "req_inclusive_tier_boundaries",
                "input": {
                    "module": "sla_credit_calculator.credits",
                    "function": "calculate_sla_credit",
                    "args": [200000, 50, 1000],
                    "kwargs": {},
                },
                "expected": {"uptime_bps": 9500, "credit_rate_pct": 25, "credit_cents": 50000, "sla_breached": True},
            },
            {
                "id": "sla_clamp_extreme_downtime",
                "requirement_id": "req_downtime_clamping",
                "input": {
                    "module": "sla_credit_calculator.credits",
                    "function": "calculate_sla_credit",
                    "args": [80000, 99999, 1000],
                    "kwargs": {},
                },
                "expected": {"uptime_bps": 0, "credit_rate_pct": 50, "credit_cents": 40000, "sla_breached": True},
            },
        ],
    }
    (holdout_dir / "holdout_cases.json").write_text(json.dumps(holdout_cases, indent=2), encoding="utf-8")

    frozen_meta = {
        "variant_id": "variant_a_boundary_handling",
        "target_file": "sla_credit_calculator/credits.py",
        "public_test_rel_path": "tests/test_credits_public.py",
        "initial_workspace_hashes": _hash_directory_files(ws),
        "holdout_sha256": hashlib.sha256((holdout_dir / "holdout_cases.json").read_bytes()).hexdigest(),
    }
    return ws, holdout_dir, frozen_meta


def _build_variant_b_incident_router(base_dir: Path) -> Tuple[Path, Path, Dict[str, Any]]:
    """Variant B (Ordering / Selection Behavior): `incident_router/router.py`."""
    ws = base_dir / "variant_b_router_repo"
    pkg = ws / "incident_router"
    tests_dir = ws / "tests"
    holdout_dir = base_dir / "variant_b_holdout"
    pkg.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    holdout_dir.mkdir(parents=True, exist_ok=True)

    (pkg / "__init__.py").write_text("from .router import select_oncall_engineer\n", encoding="utf-8")
    (pkg / "escalation_policy.py").write_text(
        '"""Unrelated escalation policy constants."""\n'
        "DEFAULT_PAGE_TIMEOUT_SECONDS = 300\n",
        encoding="utf-8",
    )
    (ws / "README.md").write_text("# On-Call Incident Router\nSelects least-loaded eligible on-duty responder.\n", encoding="utf-8")

    buggy_router = '''"""On-call incident responder selection router."""
from __future__ import annotations
from typing import Any, Dict, List


def select_oncall_engineer(
    engineers: List[Dict[str, Any]],
    required_skill: str,
    max_active_incidents: int = 3,
) -> Dict[str, Any]:
    """Select the least-loaded eligible on-call engineer for an incident.

    Specification rules:
    1. An engineer is eligible if and only if:
       - `bool(eng.get("on_duty", False))` is True
       - `required_skill` is in `eng.get("skills", [])`
       - `int(eng.get("active_incidents", 0)) < int(max_active_incidents)`
    2. Sort eligible engineers ascending by `(int(eng.get("active_incidents", 0)), str(eng["engineer_id"]))`
       so the responder with the fewest active incidents (and lexicographically smallest `engineer_id` on ties)
       is selected first.
    3. Return:
       - `"selected_engineer_id"`: str or None (if no eligible engineer exists)
       - `"eligible_count"`: int count of eligible engineers
       - `"candidate_order"`: list of `str(eng["engineer_id"])` in sorted eligible order
    """
    eligible: List[Dict[str, Any]] = []
    for eng in engineers:
        # BUG 1: Does not check on_duty or required_skill membership properly
        if int(eng.get("active_incidents", 0)) <= int(max_active_incidents):
            eligible.append(eng)

    # BUG 2: Sorts descending (reverse=True) instead of ascending by (active_incidents, engineer_id)
    eligible.sort(key=lambda e: (int(e.get("active_incidents", 0)), str(e["engineer_id"])), reverse=True)
    order = [str(e["engineer_id"]) for e in eligible]
    return {
        "selected_engineer_id": order[0] if order else None,
        "eligible_count": len(order),
        "candidate_order": order,
    }
'''
    (pkg / "router.py").write_text(buggy_router, encoding="utf-8")

    pub_test = '''import unittest
from incident_router.router import select_oncall_engineer


class TestIncidentRouterPublic(unittest.TestCase):
    def test_least_loaded_on_duty_with_skill(self):
        roster = [
            {"engineer_id": "ENG-03", "on_duty": True, "skills": ["postgres", "k8s"], "active_incidents": 2},
            {"engineer_id": "ENG-01", "on_duty": False, "skills": ["postgres"], "active_incidents": 0},
            {"engineer_id": "ENG-02", "on_duty": True, "skills": ["postgres"], "active_incidents": 1},
            {"engineer_id": "ENG-04", "on_duty": True, "skills": ["redis"], "active_incidents": 0},
            {"engineer_id": "ENG-05", "on_duty": True, "skills": ["postgres"], "active_incidents": 3},
        ]
        res = select_oncall_engineer(roster, required_skill="postgres", max_active_incidents=3)
        self.assertEqual(res["selected_engineer_id"], "ENG-02")
        self.assertEqual(res["eligible_count"], 2)
        self.assertEqual(res["candidate_order"], ["ENG-02", "ENG-03"])

    def test_tie_break_and_none_eligible(self):
        roster = [
            {"engineer_id": "ENG-B", "on_duty": True, "skills": ["network"], "active_incidents": 1},
            {"engineer_id": "ENG-A", "on_duty": True, "skills": ["network"], "active_incidents": 1},
        ]
        res = select_oncall_engineer(roster, required_skill="network", max_active_incidents=2)
        self.assertEqual(res["selected_engineer_id"], "ENG-A")
        self.assertEqual(res["candidate_order"], ["ENG-A", "ENG-B"])

        empty_res = select_oncall_engineer(roster, required_skill="storage", max_active_incidents=2)
        self.assertIsNone(empty_res["selected_engineer_id"])
        self.assertEqual(empty_res["eligible_count"], 0)


if __name__ == "__main__":
    unittest.main()
'''
    (tests_dir / "test_router_public.py").write_text(pub_test, encoding="utf-8")

    holdout_cases = {
        "schema": "qc.json_io_cases.v1",
        "cases": [
            {
                "id": "router_select_lowest_load",
                "requirement_id": "req_filter_and_ascending_load_order",
                "input": {
                    "module": "incident_router.router",
                    "function": "select_oncall_engineer",
                    "args": [
                        [
                            {"engineer_id": "ENG-9", "on_duty": True, "skills": ["storage", "linux"], "active_incidents": 2},
                            {"engineer_id": "ENG-4", "on_duty": True, "skills": ["storage"], "active_incidents": 0},
                            {"engineer_id": "ENG-1", "on_duty": False, "skills": ["storage"], "active_incidents": 0},
                            {"engineer_id": "ENG-2", "on_duty": True, "skills": ["storage"], "active_incidents": 4},
                        ],
                        "storage",
                        3,
                    ],
                    "kwargs": {},
                },
                "expected": {
                    "selected_engineer_id": "ENG-4",
                    "eligible_count": 2,
                    "candidate_order": ["ENG-4", "ENG-9"],
                },
            },
            {
                "id": "router_lexicographical_tie_break",
                "requirement_id": "req_filter_and_ascending_load_order",
                "input": {
                    "module": "incident_router.router",
                    "function": "select_oncall_engineer",
                    "args": [
                        [
                            {"engineer_id": "ENG-20", "on_duty": True, "skills": ["tls"], "active_incidents": 1},
                            {"engineer_id": "ENG-10", "on_duty": True, "skills": ["tls"], "active_incidents": 1},
                        ],
                        "tls",
                        3,
                    ],
                    "kwargs": {},
                },
                "expected": {
                    "selected_engineer_id": "ENG-10",
                    "eligible_count": 2,
                    "candidate_order": ["ENG-10", "ENG-20"],
                },
            },
            {
                "id": "router_all_at_capacity",
                "requirement_id": "req_capacity_cutoff",
                "input": {
                    "module": "incident_router.router",
                    "function": "select_oncall_engineer",
                    "args": [
                        [
                            {"engineer_id": "ENG-10", "on_duty": True, "skills": ["tls"], "active_incidents": 2},
                        ],
                        "tls",
                        2,
                    ],
                    "kwargs": {},
                },
                "expected": {
                    "selected_engineer_id": None,
                    "eligible_count": 0,
                    "candidate_order": [],
                },
            },
        ],
    }
    (holdout_dir / "holdout_cases.json").write_text(json.dumps(holdout_cases, indent=2), encoding="utf-8")

    frozen_meta = {
        "variant_id": "variant_b_ordering_selection",
        "target_file": "incident_router/router.py",
        "public_test_rel_path": "tests/test_router_public.py",
        "initial_workspace_hashes": _hash_directory_files(ws),
        "holdout_sha256": hashlib.sha256((holdout_dir / "holdout_cases.json").read_bytes()).hexdigest(),
    }
    return ws, holdout_dir, frozen_meta


def _build_variant_c_ledger_reconciler(base_dir: Path) -> Tuple[Path, Path, Dict[str, Any]]:
    """Variant C (State / Aggregation Logic): `ledger_reconciler/reconcile.py`."""
    ws = base_dir / "variant_c_ledger_repo"
    pkg = ws / "ledger_reconciler"
    tests_dir = ws / "tests"
    holdout_dir = base_dir / "variant_c_holdout"
    pkg.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    holdout_dir.mkdir(parents=True, exist_ok=True)

    (pkg / "__init__.py").write_text("from .reconcile import summarize_account_balances\n", encoding="utf-8")
    (pkg / "audit_log.py").write_text(
        '"""Unrelated ledger audit log constant."""\n'
        "LEDGER_SCHEMA_VERSION = 1\n",
        encoding="utf-8",
    )
    (ws / "README.md").write_text("# Ledger Account Balance Reconciler\nAggregates posted journal entries by account.\n", encoding="utf-8")

    buggy_reconcile = '''"""Account balance state and aggregation reconciler."""
from __future__ import annotations
from typing import Any, Dict, List


def summarize_account_balances(
    entries: List[Dict[str, Any]],
    target_currency: str = "USD",
) -> Dict[str, Any]:
    """Aggregate posted journal entries by `account_id` for `target_currency`.

    Specification rules:
    1. Only process entries where `entry.get("status") == "posted"` and
       `entry.get("currency", "USD") == target_currency`. Ignore all other entries
       (such as `"voided"` or `"pending"` status or other currencies).
    2. For each matching posted entry:
       - If `entry.get("kind") == "credit"`, add `int(entry["amount_cents"])` to the account balance.
       - If `entry.get("kind") == "debit"`, subtract `int(entry["amount_cents"])` from the account balance.
       - Otherwise raise `ValueError("Unsupported entry kind")`.
    3. Return a dict with:
       - `"accounts"`: dict mapping `account_id` (sorted keys) to its integer net balance in cents
       - `"net_total_cents"`: integer sum of all account balances in `"accounts"`
       - `"applied_count"`: integer number of posted entries applied
    """
    balances: Dict[str, int] = {}
    applied_count = 0

    for entry in entries:
        # BUG 1: Does not filter by status == 'posted' or currency == target_currency
        acct = str(entry["account_id"])
        amt = int(entry["amount_cents"])
        kind = entry.get("kind")
        if kind not in ("credit", "debit"):
            raise ValueError(f"Unsupported entry kind: {kind}")

        # BUG 2: Adds both credit and debit instead of subtracting debit
        balances[acct] = balances.get(acct, 0) + amt
        applied_count += 1

    sorted_accounts = {k: balances[k] for k in sorted(balances.keys())}
    return {
        "accounts": sorted_accounts,
        "net_total_cents": sum(sorted_accounts.values()),
        "applied_count": applied_count,
    }
'''
    (pkg / "reconcile.py").write_text(buggy_reconcile, encoding="utf-8")

    pub_test = '''import unittest
from ledger_reconciler.reconcile import summarize_account_balances


class TestLedgerReconcilePublic(unittest.TestCase):
    def test_credit_debit_and_status_filtering(self):
        rows = [
            {"account_id": "ACCT-100", "kind": "credit", "amount_cents": 5000, "status": "posted", "currency": "USD"},
            {"account_id": "ACCT-100", "kind": "debit", "amount_cents": 1200, "status": "posted", "currency": "USD"},
            {"account_id": "ACCT-100", "kind": "debit", "amount_cents": 9999, "status": "voided", "currency": "USD"},
            {"account_id": "ACCT-200", "kind": "credit", "amount_cents": 3000, "status": "posted", "currency": "EUR"},
            {"account_id": "ACCT-200", "kind": "credit", "amount_cents": 2500, "status": "posted", "currency": "USD"},
        ]
        res = summarize_account_balances(rows, target_currency="USD")
        self.assertEqual(res["accounts"], {"ACCT-100": 3800, "ACCT-200": 2500})
        self.assertEqual(res["net_total_cents"], 6300)
        self.assertEqual(res["applied_count"], 3)


if __name__ == "__main__":
    unittest.main()
'''
    (tests_dir / "test_reconcile_public.py").write_text(pub_test, encoding="utf-8")

    holdout_cases = {
        "schema": "qc.json_io_cases.v1",
        "cases": [
            {
                "id": "ledger_posted_credits_and_debits",
                "requirement_id": "req_debit_subtraction_and_status_filter",
                "input": {
                    "module": "ledger_reconciler.reconcile",
                    "function": "summarize_account_balances",
                    "args": [
                        [
                            {"account_id": "A-1", "kind": "credit", "amount_cents": 10000, "status": "posted", "currency": "USD"},
                            {"account_id": "A-1", "kind": "debit", "amount_cents": 4500, "status": "posted", "currency": "USD"},
                            {"account_id": "A-1", "kind": "credit", "amount_cents": 8000, "status": "pending", "currency": "USD"},
                            {"account_id": "A-2", "kind": "debit", "amount_cents": 1500, "status": "posted", "currency": "USD"},
                        ],
                        "USD",
                    ],
                    "kwargs": {},
                },
                "expected": {
                    "accounts": {"A-1": 5500, "A-2": -1500},
                    "net_total_cents": 4000,
                    "applied_count": 3,
                },
            },
            {
                "id": "ledger_currency_isolation",
                "requirement_id": "req_currency_filter",
                "input": {
                    "module": "ledger_reconciler.reconcile",
                    "function": "summarize_account_balances",
                    "args": [
                        [
                            {"account_id": "EU-1", "kind": "credit", "amount_cents": 7000, "status": "posted", "currency": "EUR"},
                            {"account_id": "EU-1", "kind": "debit", "amount_cents": 2000, "status": "posted", "currency": "EUR"},
                            {"account_id": "EU-1", "kind": "credit", "amount_cents": 5000, "status": "posted", "currency": "USD"},
                        ],
                        "EUR",
                    ],
                    "kwargs": {},
                },
                "expected": {
                    "accounts": {"EU-1": 5000},
                    "net_total_cents": 5000,
                    "applied_count": 2,
                },
            },
        ],
    }
    (holdout_dir / "holdout_cases.json").write_text(json.dumps(holdout_cases, indent=2), encoding="utf-8")

    frozen_meta = {
        "variant_id": "variant_c_state_aggregation_and_repair",
        "target_file": "ledger_reconciler/reconcile.py",
        "public_test_rel_path": "tests/test_reconcile_public.py",
        "initial_workspace_hashes": _hash_directory_files(ws),
        "holdout_sha256": hashlib.sha256((holdout_dir / "holdout_cases.json").read_bytes()).hexdigest(),
    }
    return ws, holdout_dir, frozen_meta


def test_fresh_variants_repair_transfer_live_e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 5: Run all 3 fresh repository defect variants through `/api/v1/chat/deliberate`,
    authenticated `VPSWorkerAgent` (`qwen2.5:3b`), and the live Linux root-image sandbox on
    `qwen-laptop` (`100.101.187.115`), verifying zero solution leakage in coordinator source/prompts.
    """
    import re
    import sqlite3
    from app.auth import create_session_token
    from app.config import settings
    from app.database import get_db_connection, init_db
    from app.services.orchestration import model_router, working_memory as wm

    if not Path(SSH_KEY_PATH).exists():
        pytest.skip(f"SSH key not present at {SSH_KEY_PATH}")

    repo_root = Path(__file__).resolve().parents[1]
    delib_src = (repo_root / "app" / "services" / "orchestration" / "deliberate_loop.py").read_text(encoding="utf-8")

    # Static check: no domain solution expressions for any of the 3 fresh variants in deliberate_loop.py
    forbidden_variant_tokens = [
        "calculate_sla_credit",
        "select_oncall_engineer",
        "summarize_account_balances",
        "uptime_bps >= 9990",
        "credit_rate_pct",
        "active_incidents",
        "amount_cents",
    ]
    for tok in forbidden_variant_tokens:
        assert tok not in delib_src, f"Coordinator source must not contain variant token: {tok}"

    base_dir = tmp_path
    data_dir = base_dir / "controller_data"
    worker_dir = base_dir / "worker_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    worker_dir.mkdir(parents=True, exist_ok=True)

    # Freeze all 3 fixtures and holdout suites BEFORE starting evaluation
    ws_a, hold_a, meta_a = _build_variant_a_sla_boundary(base_dir)
    ws_b, hold_b, meta_b = _build_variant_b_incident_router(base_dir)
    ws_c, hold_c, meta_c = _build_variant_c_ledger_reconciler(base_dir)

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    secret_key = "inc19-fresh-variants-secret-key-0123456789abcdef"

    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "secret_key", secret_key)
    init_db()
    wm.GLOBAL_WORKING_SET_CACHE = wm.WorkingSetCache()

    worker_id = "node-inc19-variants-live"
    ctrl_proc = _start_controller_with_protected_linux_verifier(repo_root, data_dir, port, secret_key)
    worker_proc = None
    admin_cookie = create_session_token("admin")

    try:
        with httpx.Client(
            base_url=base_url,
            cookies={"qc_session": admin_cookie},
            headers={"Authorization": f"Bearer {admin_cookie}"},
            timeout=240.0,
        ) as client:
            html_page = client.get("/projects/orchestrate/new").text
            m_csrf = re.search(r'data-csrf="([0-9a-f]+)"', html_page)
            assert m_csrf, "Could not extract CSRF token from controller"
            csrf_headers = {"X-CSRF-Token": m_csrf.group(1), "Authorization": f"Bearer {admin_cookie}"}

            r_enr = client.post(
                "/api/v1/worker/enrollment-tokens",
                json={"allowed_node_id": worker_id, "expires_in_seconds": 3600},
                headers=csrf_headers,
            )
            assert r_enr.status_code == 200, r_enr.text
            enrollment_token = r_enr.json()["enrollment_token"]

            worker_proc = _start_worker_daemon_subprocess(
                repo_root,
                worker_dir,
                base_url,
                worker_id,
                enrollment_token,
            )

            enrolled_ok = False
            for _ in range(45):
                if worker_proc.poll() is not None:
                    out = (worker_dir / "worker_variants.log").read_text(encoding="utf-8", errors="replace")
                    raise RuntimeError(f"Worker daemon exited prematurely: {out}")
                conn = get_db_connection()
                try:
                    nrow = conn.execute(
                        "SELECT id, last_contact_at FROM infrastructure_nodes WHERE id = ?",
                        (worker_id,),
                    ).fetchone()
                    trow = conn.execute(
                        "SELECT id FROM worker_auth_tokens WHERE node_id = ? AND revoked = 0",
                        (worker_id,),
                    ).fetchone()
                    if nrow and trow and nrow["last_contact_at"]:
                        enrolled_ok = True
                        break
                finally:
                    conn.close()
                time.sleep(0.25)
            assert enrolled_ok, "Authenticated VPSWorkerAgent did not complete enrollment and heartbeat"

            model_router.verify_node_model_capability(
                worker_id, "qwen2.5:3b", verified_by="admin_capability_probe", passed=True
            )

            variant_specs = [
                {
                    "meta": meta_a,
                    "ws": ws_a,
                    "holdout": hold_a,
                    "objective": "Fix the SLA credit tier boundary and downtime clamping defects in sla_credit_calculator/credits.py.",
                    "constraints": [
                        "Clamp downtime_minutes to [0, total_minutes]",
                        "Use inclusive tier boundaries: >= 9990 -> 0%, >= 9900 -> 10%, >= 9500 -> 25%, < 9500 -> 50%",
                        "Preserve sla_credit_calculator/formatting.py and README.md unchanged",
                    ],
                    "injected_first_attempt_candidate": None,
                },
                {
                    "meta": meta_b,
                    "ws": ws_b,
                    "holdout": hold_b,
                    "objective": "Fix the on-call engineer eligibility filtering and ascending load selection in incident_router/router.py.",
                    "constraints": [
                        "Only select engineers where on_duty is True, required_skill is in skills, and active_incidents < max_active_incidents",
                        "Sort eligible engineers ascending by (int(active_incidents), str(engineer_id))",
                        "Preserve incident_router/escalation_policy.py and README.md unchanged",
                    ],
                    "injected_first_attempt_candidate": None,
                },
                {
                    "meta": meta_c,
                    "ws": ws_c,
                    "holdout": hold_c,
                    "objective": "Fix the account balance aggregation and status/currency filtering in ledger_reconciler/reconcile.py.",
                    "constraints": [
                        "Only include entries with status == 'posted' and currency == target_currency",
                        "Add credit amount_cents and subtract debit amount_cents",
                        "Preserve ledger_reconciler/audit_log.py and README.md unchanged",
                    ],
                    # Labeled test-fixture fault injection on Attempt 1 to prove domain-neutral repair on Attempt 2
                    "injected_first_attempt_candidate": (ws_c / "ledger_reconciler" / "reconcile.py").read_text(encoding="utf-8"),
                },
            ]

            variant_results: List[Dict[str, Any]] = []

            for vspec in variant_specs:
                meta = vspec["meta"]
                ws_path = vspec["ws"]
                hold_path = vspec["holdout"]

                payload: Dict[str, Any] = {
                    "message": vspec["objective"],
                    "intake": {
                        "objective": vspec["objective"],
                        "constraints": vspec["constraints"],
                        "deliverables": [
                            "repo_inspection_and_reproduction.json",
                            "verified_bugfix_patch.diff",
                            "bugfix_verification_report.md",
                        ],
                        "workspace_path": str(ws_path),
                        "holdout_test_dir": str(hold_path),
                    },
                    "advance": True,
                    "max_steps": 5,
                    "use_live_worker": True,
                    "preferred_model": "qwen2.5:3b",
                }
                if vspec["injected_first_attempt_candidate"] is not None:
                    # Assert HTTP /api/v1/chat/deliberate rejects injected_first_attempt_candidate on external API requests (AUD-03)
                    rejected_payload = dict(payload)
                    rejected_payload["injected_first_attempt_candidate"] = vspec["injected_first_attempt_candidate"]
                    rej_resp = client.post("/api/v1/chat/deliberate", json=rejected_payload, headers=csrf_headers)
                    assert rej_resp.status_code == 400, f"Expected 400 for injected_first_attempt_candidate over HTTP: {rej_resp.text}"
                    # Create project via HTTP with advance=False, then invoke internal harness with injected_first_attempt_candidate
                    init_payload = dict(payload)
                    init_payload["advance"] = False
                    resp = client.post("/api/v1/chat/deliberate", json=init_payload, headers=csrf_headers)
                    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
                    body = resp.json()
                    from app.services.orchestration import deliberate_loop

                    adv = deliberate_loop.advance_deliberate_project(
                        body["project_id"],
                        run_id=body["run_id"],
                        actor="admin",
                        max_steps=5,
                        use_live_worker=True,
                        preferred_model="qwen2.5:3b",
                        injected_first_attempt_candidate=vspec["injected_first_attempt_candidate"],
                    )
                else:
                    resp = client.post("/api/v1/chat/deliberate", json=payload, headers=csrf_headers)
                    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
                    body = resp.json()
                    adv = body["advance_result"]
                assert adv["final_cycle_phase"] == "completed", f"Variant {meta['variant_id']} did not complete: {json.dumps(adv, indent=2)}"

                pid = body["project_id"]
                after_hashes = _hash_directory_files(ws_path)
                target_rel = meta["target_file"]

                # Verify all unrelated files remained byte-for-byte identical
                for rel_k, before_sha in meta["initial_workspace_hashes"].items():
                    if rel_k != target_rel:
                        assert after_hashes.get(rel_k) == before_sha, (
                            f"Unrelated file {rel_k} was modified in {meta['variant_id']}"
                        )
                assert after_hashes[target_rel] != meta["initial_workspace_hashes"][target_rel]

                # Inspect task contract t2 and epistemic items in DB
                db_file = data_dir / "qwen_collective.db"
                conn = sqlite3.connect(str(db_file))
                conn.row_factory = sqlite3.Row
                try:
                    t2_row = conn.execute(
                        "SELECT output_summary_json FROM project_task_contracts WHERE project_id = ? AND task_key = 't2_propose_and_verify_patch'",
                        (pid,),
                    ).fetchone()
                    t2_summary = json.loads(t2_row["output_summary_json"])
                finally:
                    conn.close()

                assert t2_summary["containment_backend"] == "linux_systemd_root_image_sandbox"
                assert t2_summary["filesystem_and_network_containment"] is True
                assert t2_summary["unrelated_files_preserved"] is True
                assert t2_summary["lease_id"] is not None

                if vspec["injected_first_attempt_candidate"] is not None:
                    assert len(adv["repair_events"]) == 1
                    assert adv["repair_events"][0]["failure_origin"] == "injected_test_fixture"

                variant_results.append({
                    "variant_id": meta["variant_id"],
                    "project_id": pid,
                    "target_file": target_rel,
                    "frozen_before_sha256": meta["initial_workspace_hashes"][target_rel],
                    "verified_after_sha256": after_hashes[target_rel],
                    "frozen_holdout_sha256": meta["holdout_sha256"],
                    "unrelated_files_preserved": True,
                    "unrelated_file_hashes": {
                        k: v for k, v in after_hashes.items() if k != target_rel
                    },
                    "worker_id": worker_id,
                    "lease_id": t2_summary["lease_id"],
                    "producing_actor": t2_summary["producing_actor"],
                    "containment_backend": t2_summary["containment_backend"],
                    "filesystem_and_network_containment": t2_summary["filesystem_and_network_containment"],
                    "public_tests_run": t2_summary["public_tests_run"],
                    "holdout_tests_run": t2_summary["holdout_tests_run"],
                    "prompts_sent": t2_summary.get("prompts_sent", []),
                    "repair_events": adv.get("repair_events", []),
                    "tool_selection_stats": adv.get("tool_selection_stats", {}),
                    "patched_code": (ws_path / target_rel).read_text(encoding="utf-8"),
                })

            evidence_payload = {
                "schema": "qc.increment19_fresh_variants_transfer_evidence.v1",
                "host": LAPTOP_HOST,
                "image_sha256": protected_repo_verifier.PINNED_LINUX_ROOT_IMAGE_SHA256,
                "model": "qwen2.5:3b",
                "worker_id": worker_id,
                "variants_evaluated": len(variant_results),
                "all_variants_verified": len(variant_results) == 3,
                "variants": variant_results,
            }
            EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
            if os.environ.get("QC_RERUN_FRESH_VARIANTS_EVIDENCE_PATH", "").strip(): Path(os.environ["QC_RERUN_FRESH_VARIANTS_EVIDENCE_PATH"].strip()).write_text(json.dumps(evidence_payload, indent=2), encoding="utf-8")
            elif not EVIDENCE_PATH.exists() or os.environ.get("QC_OVERWRITE_HISTORICAL_EVIDENCE") == "1": EVIDENCE_PATH.write_text(json.dumps(evidence_payload, indent=2), encoding="utf-8")
    finally:
        if worker_proc is not None:
            worker_proc.terminate()
            try:
                worker_proc.wait(timeout=5)
            except Exception:
                worker_proc.kill()
        ctrl_proc.terminate()
        try:
            ctrl_proc.wait(timeout=5)
        except Exception:
            ctrl_proc.kill()
