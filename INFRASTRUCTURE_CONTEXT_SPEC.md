# Qwen Collective — Architecture Context Packet Specification

## 1. Purpose & Guiding Principles

AI instances (Qwen) need accurate, live understanding of the infrastructure to evaluate strategies, recommend tool allocations, and reason about execution viability.

To accomplish this safely without flooding model prompts or injecting stale assumptions:
1. **Never memorized:** The model receives live, current registry data, not static system prompts.
2. **Token-Budgeted:** Like Project Context Packets, Architecture Context Packets fit strict token allocations (e.g. 500 to 4000 tokens).
3. **Explicit Priority Pruning:** Low-priority information is pruned first when budgets are constrained.
4. **Trust Tagging:** Clearly distinguishes verified operational facts from unverified or stale records.

---

## 2. Priority Hierarchy

| Priority | Section | Content | Pruning Behavior |
| :---: | :--- | :--- | :--- |
| **1** | `[TASK_OR_INSTRUCTION_SCOPE]` | User prompt or current operational query | **Never pruned** |
| **2** | `[CONTROLLER_AND_BOUNDS]` | Controller role, authoritative policies, approval boundaries | Essential |
| **3** | `[VERIFIED_CAPABILITIES]` | Confirmed, active capabilities with supported operations | Pruned by relevance |
| **4** | `[ACTIVE_WORKERS_SUMMARY]` | Machine names, roles, online status, OS, available RAM | Summarized |
| **5** | `[ACTIVE_DEPLOYMENTS]` | Currently running persistent services and scheduled jobs | Pruned if unneeded |
| **6** | `[RESOURCE_CONSTRAINTS]` | Hard limits (timeouts, max RAM, concurrency limits) | Preserved if relevant |
| **7** | `[UNVERIFIED_OR_DEGRADED]` | Installed software that is NOT verified or degraded | Omitted under tight budget |
| **8** | `[RECENT_FAILURES_AND_DRIFT]` | Reconciliations, recent execution errors, alerts | Omitted first |

---

## 3. Formatting & Security Separation

Architecture Context Packets render with explicit Markdown delimiter blocks:
```markdown
# [SYSTEM_ARCHITECTURE_CONTEXT]
Timestamp: 2026-10-14T10:00:00Z | Token Budget: 2000 | Freshness: Live Registry

## [CONTROLLER_AND_BOUNDS]
- Role: Authoritative Windows Coordinator
- Node: desktop-44h6qvp (Windows 10, Tailscale Connected)
- Invariant: Model proposes; human operator authorizes deployment and persistent executions.

## [VERIFIED_CAPABILITIES]
- cap_ctrl_llm_inference: Local Qwen 2.5 Inference (Verified)
- cap_vps_python_exec: Isolated Python 3.12 Script Runner on VPS (Verified)

## [ACTIVE_WORKERS_SUMMARY]
- desktop-44h6qvp (controller): Online, 64 GB RAM (38 GB free)
- qwen-vps-worker-01 (vps_worker): Online, 4 GB RAM (2.4 GB free)

## [RESOURCE_CONSTRAINTS]
- VPS Execution Timeout: 300s
- Outbound Egress: HTTPS Only (SSRF guarded)

## [OMISSION_NOTICE]
Sections [UNVERIFIED_OR_DEGRADED] and [RECENT_FAILURES_AND_DRIFT] omitted to fit 2000 token limit.
```

---

## 4. Status

* **Status:** Implemented in Milestone 1 via `app.services.infrastructure.build_architecture_context_packet`.
