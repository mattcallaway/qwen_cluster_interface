"""Authoritative action manifest and verified capability registry for Qwen Collective.

Enforces:
1. Strict registration of genuine executable actions (library scripts, controller steps, inference).
2. Separate declared, discovered, and verified capability probe states with TTL.
3. Node eligibility evaluation based on verified capabilities, platform, and policy.
4. Dynamic generation of prompt catalogs based on verified cluster capabilities.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("qc.orchestrator.action_registry")


class ActionKind(str, Enum):
    INFERENCE = "inference"
    CONTROLLER_STEP = "controller_step"
    LIBRARY_SCRIPT = "library_script"


ActionCategory = ActionKind


class CapabilityVerificationStatus(str, Enum):
    DECLARED = "declared"
    DISCOVERED = "discovered"
    VERIFIED = "verified"
    STALE = "stale"
    FAILED = "failed"


@dataclass
class ActionManifest:
    action_id: str = ""
    version: str = "1.0.0"
    kind: ActionKind = ActionKind.INFERENCE
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    required_capabilities: List[str] = field(default_factory=list)
    allowed_platforms: List[str] = field(default_factory=list)  # ["linux", "windows"] or empty for all
    risk_level: str = "low"  # low, medium, high
    timeout_budget: int = 300
    side_effects: bool = False
    handler: Optional[str] = None
    sample_call: Optional[Dict[str, Any]] = None
    name: Optional[str] = None
    category: Optional[ActionKind] = None

    def __post_init__(self) -> None:
        if self.name and not self.action_id:
            self.action_id = self.name
        elif not self.name and self.action_id:
            self.name = self.action_id
        if self.category and not self.kind:
            self.kind = self.category
        elif not self.category and self.kind:
            self.category = self.kind


@dataclass
class CapabilityProbeRecord:
    node_id: str
    capability: str
    status: CapabilityVerificationStatus
    verified_at: Optional[str] = None
    ttl_seconds: int = 3600
    software_runtime: str = "python 3.12"
    evidence: Dict[str, Any] = field(default_factory=dict)

    def is_valid(self, now_iso: Optional[str] = None) -> bool:
        if self.status != CapabilityVerificationStatus.VERIFIED or not self.verified_at:
            return False
        try:
            ver_time = datetime.datetime.fromisoformat(self.verified_at.replace("Z", "+00:00"))
            now_time = datetime.datetime.fromisoformat(now_iso.replace("Z", "+00:00")) if now_iso else datetime.datetime.now(datetime.timezone.utc)
            delta = (now_time - ver_time).total_seconds()
            return 0 <= delta <= self.ttl_seconds
        except Exception:
            return False


class ActionRegistry:
    """Authoritative catalog of implemented executable actions and worker capabilities."""

    def __init__(self) -> None:
        self._actions: Dict[Tuple[str, str], ActionManifest] = {}
        self._probes: Dict[Tuple[str, str], CapabilityProbeRecord] = {}
        self._gaps: List[Dict[str, Any]] = []
        self._bootstrap_default_actions()

    def _bootstrap_default_actions(self) -> None:
        # 1. Registered library scripts
        self.register(ActionManifest(
            action_id="sys_health_basic",
            version="1.0.0",
            kind=ActionKind.LIBRARY_SCRIPT,
            description="Run non-destructive host and sandbox telemetry inspection (CPU, memory, disk, platform).",
            required_capabilities=["python_execution"],
            allowed_platforms=["linux", "windows"],
            risk_level="low",
            timeout_budget=90,
            handler="app.services.orchestration.script_library.sys_health_basic",
        ))

        # 2. Registered controller steps
        controller_steps_def = [
            ("assemble_report", "Compile deliverables and task outputs into a cohesive Markdown document."),
            ("research_search", "Execute outbound web search queries against configured providers."),
            ("research_fetch", "Retrieve source HTML/text with socket-pinned HTTP transport."),
            ("research_browser_read", "Render and extract text using proxy-isolated Playwright browser."),
            ("research_extract", "Extract structured claims and evidence fragments from documents."),
            ("research_verify", "Cross-corroborate candidate claims against independent sources."),
            ("research_synthesize", "Synthesize fact-based research summary with citation grounding."),
            ("research_citation_audit", "Audit citation coverage and reject zero-citation or ungrounded claims."),
            ("research_full_cycle", "Execute full multi-stage research cycle on the controller."),
            ("pipeline_kv_diagnostics", "Execute Increment 17 distributed pipeline parallelism & searchable paged KV diagnostics."),
        ]
        for step_id, desc in controller_steps_def:
            self.register(ActionManifest(
                action_id=step_id,
                version="1.0.0",
                kind=ActionKind.CONTROLLER_STEP,
                description=desc,
                required_capabilities=[],
                allowed_platforms=[],
                risk_level="low",
                timeout_budget=300,
                handler=f"app.services.orchestration.controller_steps.{step_id}",
            ))

        # 3. Model inference action
        self.register(ActionManifest(
            action_id="inference",
            version="1.0.0",
            kind=ActionKind.INFERENCE,
            description="Generate text analysis, summary, code, or structured JSON using a local model.",
            required_capabilities=["inference"],
            allowed_platforms=[],
            risk_level="low",
            timeout_budget=280,
            handler="app.services.worker.execute_inference",
        ))

    def register(self, manifest: ActionManifest) -> None:
        key = (manifest.kind.value, manifest.action_id)
        self._actions[key] = manifest
        logger.debug("Registered action %s:%s (v%s)", manifest.kind.value, manifest.action_id, manifest.version)

    def get(self, action_id: str, kind: Optional[str] = None) -> Optional[ActionManifest]:
        if kind is not None:
            return self._actions.get((kind, action_id))
        for (k, aid), manifest in self._actions.items():
            if aid == action_id:
                return manifest
        return None

    resolve = get

    def is_registered(self, action_id: str, kind: Optional[str] = None) -> bool:
        return self.get(action_id, kind) is not None

    def create_capability_gap(
        self,
        task_key: str,
        required_capability: str,
        reason: str,
        candidate_nodes: Optional[List[str]] = None,
        blocking: bool = True,
    ) -> Dict[str, Any]:
        gap = {
            "task_key": task_key,
            "required_capability": required_capability,
            "reason": reason,
            "candidate_nodes": candidate_nodes or [],
            "blocking": blocking,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self._gaps.append(gap)
        return gap

    def list_capability_gaps(self) -> List[Dict[str, Any]]:
        return list(self._gaps)

    def record_probe_result(
        self,
        node_id: str,
        capability: str,
        success: bool,
        ttl_seconds: int = 3600,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> CapabilityProbeRecord:
        status = CapabilityVerificationStatus.VERIFIED if success else CapabilityVerificationStatus.FAILED
        if ttl_seconds <= 0:
            status = CapabilityVerificationStatus.STALE
        return self.record_probe(
            node_id=node_id,
            capability=capability,
            status=status,
            evidence=evidence,
            ttl_seconds=max(0, ttl_seconds),
        )

    def find_eligible_nodes(
        self,
        action_id: str,
        fleet: List[Dict[str, Any]],
        kind: Optional[str] = None,
        enforce_verified: bool = True
    ) -> List[Dict[str, Any]]:
        manifest = self.get(action_id, kind)
        if not manifest:
            return []
        nodes, _ = self.get_eligible_nodes(manifest, fleet, enforce_verified=enforce_verified)
        return nodes

    def record_probe(
        self,
        node_id: str,
        capability: str,
        status: CapabilityVerificationStatus,
        evidence: Optional[Dict[str, Any]] = None,
        ttl_seconds: int = 3600,
        now_iso: Optional[str] = None,
        software_runtime: str = "python 3.12"
    ) -> CapabilityProbeRecord:
        now_str = now_iso or datetime.datetime.now(datetime.timezone.utc).isoformat()
        rec = CapabilityProbeRecord(
            node_id=node_id,
            capability=capability,
            status=status,
            verified_at=now_str if status == CapabilityVerificationStatus.VERIFIED else None,
            ttl_seconds=ttl_seconds,
            software_runtime=software_runtime,
            evidence=evidence or {},
        )
        self._probes[(node_id, capability)] = rec
        return rec

    def is_capability_verified(self, node_id: str, capability: str, now_iso: Optional[str] = None) -> bool:
        rec = self._probes.get((node_id, capability))
        if not rec:
            return False
        return rec.is_valid(now_iso)

    def get_eligible_nodes(
        self,
        manifest: ActionManifest,
        fleet: List[Dict[str, Any]],
        now_iso: Optional[str] = None,
        enforce_verified: bool = True
    ) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        """Return (eligible_nodes, exclusion_reasons)."""
        eligible: List[Dict[str, Any]] = []
        exclusions: Dict[str, str] = {}

        for n in fleet:
            nid = n.get("id", "unknown")
            if not n.get("is_worker"):
                exclusions[nid] = "Node is not a worker."
                continue
            if not n.get("enabled"):
                exclusions[nid] = "Node is administratively disabled."
                continue
            if not n.get("authenticated"):
                exclusions[nid] = "Node is not authenticated."
                continue
            if n.get("health") != "online":
                exclusions[nid] = f"Node health is '{n.get('health')}', must be 'online'."
                continue

            # Check platform
            node_platform = n.get("platform", "").lower()
            if manifest.allowed_platforms and node_platform and node_platform not in manifest.allowed_platforms:
                exclusions[nid] = f"Node platform '{node_platform}' not in allowed: {manifest.allowed_platforms}."
                continue

            # Check capabilities
            caps = set(n.get("capabilities", []))
            missing_caps = [c for c in manifest.required_capabilities if c not in caps]
            if missing_caps:
                exclusions[nid] = f"Node lacks declared capabilities: {missing_caps}."
                continue

            # If strict verification enforced, check probe records
            if enforce_verified:
                unverified_caps = [c for c in manifest.required_capabilities if not self.is_capability_verified(nid, c, now_iso)]
                if unverified_caps:
                    exclusions[nid] = f"Node capabilities not verified or TTL expired: {unverified_caps}."
                    continue

            eligible.append(n)

        return eligible, exclusions

    def build_prompt_catalog(self, nodes: List[Dict[str, Any]]) -> str:
        """Dynamically generate catalog of executable tools based on verified cluster state."""
        lines: List[str] = [
            "AVAILABLE EXECUTABLE ACTIONS (Do not invent actions outside this manifest):",
            "1. Action Type 'inference':",
            "   - Purpose: Text synthesis, code generation, summarization, report writing.",
            "   - Format: {\"type\": \"inference\", \"instruction\": \"...\", \"inputs\": [\"t1\"], \"max_tokens\": 200}",
            "2. Action Type 'controller_step':",
            "   - Purpose: Deterministic controller execution (research and aggregation).",
        ]
        controller_actions = [m for m in self._actions.values() if m.kind == ActionKind.CONTROLLER_STEP]
        for ca in sorted(controller_actions, key=lambda x: x.action_id):
            lines.append(f"   - step '{ca.action_id}': {ca.description}")

        lines.append("3. Action Type 'library_script':")
        lines.append("   - Purpose: Verified tools executed directly on worker nodes.")
        lib_actions = [m for m in self._actions.values() if m.kind == ActionKind.LIBRARY_SCRIPT]
        for la in sorted(lib_actions, key=lambda x: x.action_id):
            lines.append(f"   - script_id '{la.action_id}': {la.description} (Requires: {la.required_capabilities})")

        return "\n".join(lines)


# Global singleton instance
GLOBAL_ACTION_REGISTRY = ActionRegistry()
