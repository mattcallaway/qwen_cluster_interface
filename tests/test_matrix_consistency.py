"""
Matrix Consistency Validator:
Verifies that the certification ledger strictly conforms to the frozen canonical manifest.
Fails if any scenario identifier BB-01 through BB-30 is altered, missing, or redefined.
Also cryptographically pins the SHA-256 digest of canonical_manifest.json to detect any future tampering.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "docs" / "remediation" / "evidence" / "stage09" / "canonical_manifest.json"
LEDGER_PATH = REPO_ROOT / "docs" / "remediation" / "evidence" / "stage09" / "bb_reconciled_ledger.json"

# Cryptographically pinned SHA-256 digest of the frozen authoritative canonical manifest
# Derived directly from Stage 9 Preflight Baseline (docs/remediation/reports/STAGE_09_CERTIFICATION_PREFLIGHT.md)
PINNED_MANIFEST_SHA256 = "c8fe55243f297f5d9700a6923d5b67eed68cf18b2f61e38135c98ff26e48a0b7"


def test_canonical_manifest_provenance_and_hash():
    """Verify that canonical_manifest.json matches its cryptographically pinned SHA-256 digest."""
    assert MANIFEST_PATH.exists(), f"Missing manifest: {MANIFEST_PATH}"
    actual_hash = hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()
    assert actual_hash == PINNED_MANIFEST_SHA256, (
        f"Authoritative manifest was altered! Expected {PINNED_MANIFEST_SHA256}, got {actual_hash}"
    )


def test_canonical_manifest_integrity():
    """Verify that the frozen canonical manifest contains exactly BB-01 through BB-30."""
    assert MANIFEST_PATH.exists(), f"Missing manifest: {MANIFEST_PATH}"
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert "canonical_matrix" in manifest
    matrix = manifest["canonical_matrix"]
    assert len(matrix) == 30, f"Expected 30 canonical scenarios, got {len(matrix)}"

    expected_ids = [f"BB-{i:02d}" for i in range(1, 31)]
    assert sorted(list(matrix.keys())) == expected_ids

    # Verify no drift in key canonical requirements
    assert matrix["BB-04"]["title"] == "Deliverables Ledger"
    assert matrix["BB-09"]["title"] == "Cross-Worker Upload Gate"
    assert matrix["BB-10"]["title"] == "Corrupt Checksum Rejection"
    assert matrix["BB-12"]["title"] == "DNS Rebinding Defense"
    assert matrix["BB-13"]["title"] == "SSRF Loopback / LAN Block"
    assert matrix["BB-14"]["title"] == "Admin CSRF Protection"
    assert matrix["BB-16"]["title"] == "Artifact Reuse Lineage"
    assert matrix["BB-18"]["title"] == "Scheduler Tick Non-Blocking"
    assert matrix["BB-20"]["title"] == "Unmeasured Telemetry Flag"
    assert matrix["BB-22"]["title"] == "Contract API Examples"
    assert matrix["BB-25"]["title"] == "Offline Degraded Mode"
    assert matrix["BB-26"]["title"] == "Prompt Injection Gate"
    assert matrix["BB-29"]["title"] == "Tailscale Ingress Proxy"


def test_reconciled_ledger_consistency():
    """Verify that bb_reconciled_ledger.json strictly matches the frozen canonical manifest."""
    assert LEDGER_PATH.exists(), f"Missing reconciled ledger: {LEDGER_PATH}"
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)["canonical_matrix"]

    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    assert "canonical_scenarios" in ledger, "Ledger missing 'canonical_scenarios' key"
    scenarios = ledger["canonical_scenarios"]
    assert len(scenarios) == 30, f"Ledger must have exactly 30 canonical scenarios, found {len(scenarios)}"

    allowed_boundaries = {
        "Service-function verification",
        "In-process ASGI HTTP verification",
        "Running HTTP server verification",
        "Real browser verification",
        "Live physical network verification",
        "Incomplete or blocked"
    }

    # Verify exact boundary counts: 15 ASGI, 11 Service, 3 Running HTTP, 1 Real Browser
    counts: dict[str, int] = {}
    for sc_id, canonical_spec in manifest.items():
        assert sc_id in scenarios, f"Canonical scenario {sc_id} missing from ledger"
        entry = scenarios[sc_id]

        # Requirement and Title must exactly match
        assert entry["title"] == canonical_spec["title"], (
            f"Title drift on {sc_id}: expected '{canonical_spec['title']}', got '{entry.get('title')}'"
        )
        assert entry["canonical_ref"] == canonical_spec["canonical_ref"], (
            f"Ref drift on {sc_id}: expected '{canonical_spec['canonical_ref']}', got '{entry.get('canonical_ref')}'"
        )

        b = entry["execution_boundary"]
        assert b in allowed_boundaries, f"Invalid execution boundary '{b}' for {sc_id}"
        counts[b] = counts.get(b, 0) + 1

        # Status must be PASS, FAIL, or BLOCKED
        assert entry["status"] in {"PASS", "FAIL", "BLOCKED"}, (
            f"Invalid status '{entry.get('status')}' for {sc_id}"
        )

    assert counts.get("In-process ASGI HTTP verification", 0) == 15, f"Expected 15 ASGI scenarios, got {counts.get('In-process ASGI HTTP verification')}"
    assert counts.get("Service-function verification", 0) == 11, f"Expected 11 Service scenarios, got {counts.get('Service-function verification')}"
    assert counts.get("Running HTTP server verification", 0) == 3, f"Expected 3 Running HTTP scenarios, got {counts.get('Running HTTP server verification')}"
    assert counts.get("Real browser verification", 0) == 1, f"Expected 1 Real Browser scenario, got {counts.get('Real browser verification')}"

    # Verify summary_counts block in ledger
    assert "summary_counts" in ledger
    sc_block = ledger["summary_counts"]["boundaries"]
    assert sc_block["In-process ASGI HTTP verification"] == 15
    assert sc_block["Service-function verification"] == 11
    assert sc_block["Running HTTP server verification"] == 3
    assert sc_block["Real browser verification"] == 1
