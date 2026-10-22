# Remediation Test Matrix & Verification Catalog

**Date:** 2026-10-20  
**Current Test Execution (Stage 6 Canonical Artifact Subsystem):** 646 collected, 645 passed, 1 failed, 0 skipped across 63 test modules in 192.5s  
**Test Framework:** Pytest 9.1.1 / Python 3.12.10 (.venv)  
**Sanitized Evidence:** [`docs/remediation/evidence/junit_report.xml`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/junit_report.xml) / [`test_matrix.md`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/docs/remediation/evidence/test_matrix.md)

---

## 1. Complete Test Suite Module Breakdown (62 Modules)

| # | Test Module Path | Collected | Passed | Failed | Skipped | Status |
|:---:|---|:---:|:---:|:---:|:---:|:---:|
| 1 | `tests/test_audit_repairs.py` | 17 | 17 | 0 | 0 | PASS |
| 2 | `tests/test_audit_security.py` | 19 | 19 | 0 | 0 | PASS |
| 3 | `tests/test_auth.py` | 2 | 2 | 0 | 0 | PASS |
| 4 | `tests/test_auth_ratelimit.py` | 5 | 5 | 0 | 0 | PASS |
| 5 | `tests/test_chat_isolation.py` | 4 | 4 | 0 | 0 | PASS |
| 6 | `tests/test_chat_project_integration.py` | 3 | 3 | 0 | 0 | PASS |
| 7 | `tests/test_continuous_research_knowledge.py` | 8 | 8 | 0 | 0 | PASS |
| 8 | `tests/test_deliverable_artifact_resolution.py` | 2 | 2 | 0 | 0 | PASS |
| 9 | `tests/test_distributed_artifact_lifecycle.py` | 1 | 1 | 0 | 0 | PASS |
| 10 | `tests/test_end_to_end_repair.py` | 1 | 1 | 0 | 0 | PASS |
| 11 | `tests/test_inference.py` | 3 | 3 | 0 | 0 | PASS |
| 12 | `tests/test_infrastructure.py` | 7 | 7 | 0 | 0 | PASS |
| 13 | `tests/test_infrastructure_routes.py` | 9 | 9 | 0 | 0 | PASS |
| 14 | `tests/test_knowledge.py` | 4 | 4 | 0 | 0 | PASS |
| 15 | `tests/test_knowledge_meta.py` | 19 | 19 | 0 | 0 | PASS |
| 16 | `tests/test_live_multistage_research_integration.py` | 1 | 1 | 0 | 0 | PASS |
| 17 | `tests/test_live_web_research.py` | 1 | 1 | 0 | 0 | PASS |
| 18 | `tests/test_machines.py` | 5 | 5 | 0 | 0 | PASS |
| 19 | `tests/test_markdown_safety.py` | 3 | 3 | 0 | 0 | PASS |
| 20 | `tests/test_metric_integrity.py` | 11 | 11 | 0 | 0 | PASS |
| 21 | `tests/test_migration_compatibility.py` | 3 | 3 | 0 | 0 | PASS |
| 22 | `tests/test_migrations.py` | 8 | 8 | 0 | 0 | PASS |
| 23 | `tests/test_milestone2.py` | 8 | 8 | 0 | 0 | PASS |
| 24 | `tests/test_milestone2_routes.py` | 6 | 6 | 0 | 0 | PASS |
| 25 | `tests/test_milestone3.py` | 9 | 9 | 0 | 0 | PASS |
| 26 | `tests/test_milestone3_routes.py` | 6 | 6 | 0 | 0 | PASS |
| 27 | `tests/test_milestone4.py` | 6 | 6 | 0 | 0 | PASS |
| 28 | `tests/test_milestone4_routes.py` | 7 | 7 | 0 | 0 | PASS |
| 29 | `tests/test_milestone5.py` | 5 | 5 | 0 | 0 | PASS |
| 30 | `tests/test_milestone5_routes.py` | 5 | 5 | 0 | 0 | PASS |
| 31 | `tests/test_milestone_2_acceptance.py` | 1 | 1 | 0 | 0 | PASS |
| 32 | `tests/test_model_discovery.py` | 4 | 4 | 0 | 0 | PASS |
| 33 | `tests/test_observatory.py` | 9 | 9 | 0 | 0 | PASS |
| 34 | `tests/test_orchestrator_core.py` | 1 | 1 | 0 | 0 | PASS |
| 35 | `tests/test_orchestrator_project_isolation.py` | 8 | 8 | 0 | 0 | PASS |
| 36 | `tests/test_orchestrator_routes.py` | 7 | 7 | 0 | 0 | PASS |
| 37 | `tests/test_orchestrator_units.py` | 67 | 67 | 0 | 0 | PASS |
| 38 | `tests/test_probe_parser.py` | 9 | 9 | 0 | 0 | PASS |
| 39 | `tests/test_projects_m1.py` | 30 | 30 | 0 | 0 | PASS |
| 40 | `tests/test_projects_routes.py` | 19 | 19 | 0 | 0 | PASS |
| 41 | `tests/test_research_engine.py` | 7 | 7 | 0 | 0 | PASS |
| 42 | `tests/test_research_security.py` | 6 | 6 | 0 | 0 | PASS |
| 43 | `tests/test_ssrf_advanced.py` | 9 | 9 | 0 | 0 | PASS |
| 44 | `tests/test_stage01_security.py` | 26 | 26 | 0 | 0 | PASS |
| 45 | `tests/test_stage02_planning.py` | 36 | 36 | 0 | 0 | PASS |
| 46 | `tests/test_stage03_execution.py` | 14 | 14 | 0 | 0 | PASS |
| 47 | `tests/test_stage03_execution_safety.py` | 24 | 24 | 0 | 0 | PASS |
| 48 | `tests/test_stage04_research_integrity.py` | 23 | 23 | 0 | 0 | PASS |
| 49 | `tests/test_stage04_review_corrections.py` | 39 | 39 | 0 | 0 | PASS |
| 50 | `tests/test_stage05_research_lifecycle.py` | 10 | 10 | 0 | 0 | PASS |
| 51 | `tests/test_stage06_artifact_subsystem.py` | 12 | 12 | 0 | 0 | PASS |
| 52 | `tests/test_tasks.py` | 4 | 4 | 0 | 0 | PASS |
| 53 | `tests/test_ui_states.py` | 3 | 3 | 0 | 0 | PASS |
| 54 | `tests/test_unified_artifact_deliverables.py` | 9 | 9 | 0 | 0 | PASS |
| 55 | `tests/test_vps.py` | 1 | 1 | 0 | 0 | PASS |
| 56 | `tests/test_vps_agent.py` | 23 | 23 | 0 | 0 | PASS |
| 57 | `tests/test_vps_daemon.py` | 16 | 16 | 0 | 0 | PASS |
| 58 | `tests/test_vps_security.py` | 6 | 6 | 0 | 0 | PASS |
| 59 | `tests/test_vps_worker.py` | 5 | 5 | 0 | 0 | PASS |
| 60 | `tests/test_web_routes.py` | 7 | 6 | 1 | 0 | FAIL |
| 61 | `tests/test_worker_api.py` | 10 | 10 | 0 | 0 | PASS |
| 62 | `tests/test_worker_reliability.py` | 7 | 7 | 0 | 0 | PASS |
| 63 | `tests/test_worker_service.py` | 6 | 6 | 0 | 0 | PASS |
| **TOTAL** | **63 Modules** | **646** | **645** | **1** | **0** | **1 FAIL** |

---

## 2. Planned Stage-by-Stage Remediation Test Matrix

| Finding ID | Test Identifier | Category | Test Type | Scenario Description | Expected Outcome | Canonical Stage | Status |
|:---:|---|---|:---:|---|---|:---:|:---:|
| **F-10** | `test_unauthenticated_and_revoked_worker_token` | Worker Security | Negative | Worker requests without auth or with revoked key | HTTP 401 Unauthorized | Stage 1 | **VERIFIED** |
| **F-10** | `test_cross_worker_task_authorization_blocked` | Worker Security | Negative | Worker B attempts to upload artifact to Worker A's task | HTTP 403 Forbidden; zero bytes stored | Stage 1 | **VERIFIED** |
| **F-10** | `test_worker_cannot_upload_to_unassigned_task` | Worker Security | Negative | Worker attempts upload to unassigned task | HTTP 403 Forbidden; task unassigned | Stage 1 | **VERIFIED** |
| **F-10** | `test_worker_cannot_obtain_execution_authority_solely_without_lease` | Worker Security | Negative | Worker attempts execution claim without active lease | HTTP 409 Conflict | Stage 1 | **VERIFIED** |
| **F-10** | `test_legacy_direct_assignment_rules` | Worker Security | Functional | Unleased task assigned directly to worker | Direct execution permitted (0 lease history) | Stage 1 | **VERIFIED** |
| **F-10** | `test_stale_lease_identifier_and_fencing_generation_rejected` | Worker Security | Negative | Worker attempts upload using expired lease generation | HTTP 409 Conflict; fencing enforced | Stage 1 | **VERIFIED** |
| **F-10** | `test_expired_worker_cannot_submit_after_task_reacquired` | Worker Security | Negative | Expired worker submits result after reassignment | HTTP 409 Conflict | Stage 1 | **VERIFIED** |
| **F-10** | `test_concurrent_cas_state_transition_conflict` | Worker Security | Concurrency | Concurrent completion transitions | CAS prevents state overwrite | Stage 1 | **VERIFIED** |
| **F-10** | `test_idempotency_key_payload_conflict_prevention` | Worker Security | Integrity | Resubmit idempotency key with conflicting output | HTTP 409 Conflict | Stage 1 | **VERIFIED** |
| **F-10** | `test_atomic_artifact_staging_exclusive_and_verified` | Storage Security | Negative | Exclusive file creation `open(..., 'xb')` | FileExistsError triggers collision fallback; size verified | Stage 1 | **VERIFIED** |
| **F-10** | `test_artifact_size_limit_rejected` | Storage Security | Boundary | Upload artifact exceeding 25MB cap | HTTP 413 / 400 Bad Request; zero bytes persisted | Stage 1 | **VERIFIED** |
| **F-10** | `test_artifact_malformed_base64_rejected` | Storage Security | Negative | Upload non-base64 artifact payload | HTTP 400 Bad Request; zero bytes persisted | Stage 1 | **VERIFIED** |
| **F-10** | `test_artifact_invalid_checksum_format_rejected` | Storage Security | Negative | Upload with non-64-hex checksum | HTTP 400 Bad Request; zero bytes persisted | Stage 1 | **VERIFIED** |
| **F-10** | `test_checksum_verification_fails_closed_zero_bytes_persisted` | Storage Security | Negative | Upload with mismatched SHA-256 | HTTP 400 Bad Request; zero bytes persisted | Stage 1 | **VERIFIED** |
| **F-14** | `test_browser_proxy_blocks_direct_private_and_loopback` | Network Security | Adversarial | Browser CONNECT to loopback / RFC1918 / 169.254.169.254 | HTTP 403 Forbidden; connection blocked | Stage 1 | **VERIFIED** |
| **F-14** | `test_browser_proxy_dns_rebinding_prevented` | Network Security | Adversarial | Hostname rebinds from public IP to 127.0.0.1 | Pinned socket blocks connection; HTTP 403 | Stage 1 | **VERIFIED** |
| **F-14** | `test_browser_proxy_blocks_subresources_and_websockets` | Network Security | Adversarial | Subresource / WebSocket request to internal IP | HTTP 403 Forbidden; WebSocket blocked | Stage 1 | **VERIFIED** |
| **F-14** | `test_failed_proxy_causes_browser_retrieval_fail_closed` | Network Security | Negative | Browser proxy failure / unavailable | Structured 503 Service Unavailable; fail-closed | Stage 1 | **VERIFIED** |
| **F-14** | `test_httpx_pinning_transport_connects_to_verified_ip_and_preserves_sni` | Network Security | Functional | HTTPX transport binds to IP, sets TLS SNI | Direct socket connection; SNI valid | Stage 1 | **VERIFIED** |
| **F-14** | `test_httpx_redirect_revalidation_blocks_internal_hops` | Network Security | Adversarial | HTTP 302 redirect hopping to internal IP | Pinned transport revalidates redirect; blocked | Stage 1 | **VERIFIED** |
| **F-14** | `test_research_providers_use_secured_transport` | Network Security | Functional | DuckDuckGo, SearXNG, API providers transport check | All providers instantiate `PinningHTTPTransport(trust_env=False)` | Stage 1 | **VERIFIED** |
| **F-15** | `test_admin_csrf_positive_legitimate_actions_succeed` | Web Security | Functional | Admin POST with valid session & `X-CSRF-Token` | HTTP 200/201 Success | Stage 1 | **VERIFIED** |
| **F-15** | `test_admin_csrf_negative_missing_and_invalid_tokens_rejected` | Web Security | Negative | Admin POST without CSRF token or wrong token | HTTP 403 Forbidden | Stage 1 | **VERIFIED** |
| **F-15** | `test_admin_csrf_negative_cross_session_token_rejected` | Web Security | Negative | Admin POST using CSRF token from different session | HTTP 403 Forbidden | Stage 1 | **VERIFIED** |
| **F-15** | `test_bearer_token_worker_operations_unaffected_by_csrf` | Web Security | Functional | Worker operations with Bearer token | Operations succeed without CSRF tokens | Stage 1 | **VERIFIED** |
| **F-01** | `test_f01_no_silent_action_conversion` | Orchestration | Negative | Plan contains unregistered `library_script` or unknown action | Raises `PlanValidationError`; no conversion to inference | Stage 2 | **VERIFIED** |
| **F-01** | `test_f01_cycle_rejection` | Orchestration | Negative | Plan contains cyclic task dependencies | Fails with cycle diagnostic; dependencies not dropped | Stage 2 | **VERIFIED** |
| **F-02** | `test_f02_script_capability_validation` | Orchestration | Negative | Plan references script not in capability registry | Returns `CAPABILITY_GAP`; script not scheduled | Stage 2 | **VERIFIED** |
| **F-03** | `test_f03_dynamic_token_budgeting` | Orchestration | Boundary | Generate plan with 8+ tasks | Dynamic token allocation scales by task count | Stage 2 | **VERIFIED** |
| **F-04** | `test_f04_prompt_retains_constraints` | Orchestration | Functional | Plan intake includes constraints, exclusions, deadline | All parameters preserved in requirement ledger and prompt | Stage 2 | **VERIFIED** |
| **F-18** | `test_f18_model_capabilities_verified` | Orchestration | Functional | Model capabilities validated against capability registry | Capabilities backed by probe evidence | Stage 2 | **VERIFIED** |
| **F-13** | `test_f13_controller_step_async_dispatch`| Scheduler | Concurrency | Slow controller step executed during scheduler tick | Tick completes asynchronously; worker polling unblocked | Stage 3 | **VERIFIED** |
| **F-12** | `test_f12_shallow_output_rejected` | Evaluation | Negative | Task produces 25-character placeholder text | Evaluator fails acceptance; deliverable rejected | Stage 3 | **VERIFIED** |
| **F-12** | `test_f12_autonomy_eval_gate` | Evaluation | Functional | Autonomy level >= 2 produces invalid deliverable | Deliverable rejected; requires human intervention | Stage 3 | **VERIFIED** |
| **F-06** | `test_f06_claim_extraction_integrity` | Research | Functional | Extract candidate claims from source document | Claims retain source spans; no artificial 0.9 confidence | Stage 4 | **VERIFIED** |
| **F-07** | `test_f07_zero_citations_rejected` | Research | Negative | Synthesized research report has zero citations | Validation fails (`is_valid = False`, `MISSING_REQUIRED_CITATIONS`) | Stage 4 | **VERIFIED** |
| **F-08** | `test_f08_cctld_domain_parsing` | Research | Edge Case | Parse URLs from `bbc.co.uk`, `domain.com.au`, `github.io`| Correct root domain extracted; trust scores grounded | Stage 4 | **VERIFIED** |
| **F-09** | `test_f09_research_run_isolation` | Research | Security | Two research runs executed in same project | Run B receives zero evidence from Run A | Stage 4 | **VERIFIED** |
| **F-05** | `test_a_software_project_retains_deliverable_with_research` | Research | Functional | Software project retains requested `.py` deliverable throughout lifecycle | Research sidecar runs upstream; software deliverable preserved | Stage 5 | **VERIFIED** |
| **F-05** | `test_b_research_at_planning_execution_evaluation_stages` | Research | Lifecycle | Research checkpoints execute across lifecycle phases | Checkpoint entries recorded at intake, planning, execution, eval, reporting | Stage 5 | **VERIFIED** |
| **F-05** | `test_c_reuse_fresh_evidence_suppresses_duplicate_search` | Research | Performance | Fresh verified project evidence exists for objective | Duplicate searches suppressed; authorized reuse logged in `research_source_reuses` | Stage 5 | **VERIFIED** |
| **F-05** | `test_d_source_versioning_and_observation_lineage` | Research | Versioning | Repeated URL observation across sessions | Content hash version incremented without overwriting; full lineage preserved | Stage 5 | **VERIFIED** |
| **F-05** | `test_e_contradictory_evidence_triggers_replan` | Research | Replanning | Contradictory evidence discovered during execution | Semantic replanning triggered with SHA-256 digest; transitions to AWAITING_PLAN_APPROVAL | Stage 5 | **VERIFIED** |
| **F-05** | `test_f_unsupported_capability_produces_gap_not_report` | Research | Safety | Plan requires unsupported node capability | Produces explicit `unsupported_capability` gap, never converts into report | Stage 5 | **VERIFIED** |
| **F-05** | `test_g_web_unavailable_offline_tasks_continue` | Research | Resilience | Search provider or external web unreachable | Checkpoint transitions to `degraded` status; offline execution continues | Stage 5 | **VERIFIED** |
| **F-05** | `test_h_prompt_injection_in_retrieved_web_content_fails` | Research | Security | Malicious injection inside retrieved web page | Approval gates and plan constraints remain untouched | Stage 5 | **VERIFIED** |
| **F-05** | `test_i_candidate_knowledge_no_automatic_promotion` | Knowledge | Governance | Newly discovered research findings stored in knowledge | Candidates proposed with `allow_auto_promote=False`; requires Stage 7 review | Stage 5 | **VERIFIED** |
| **F-05** | `test_j_software_test_blocked_without_secure_sandbox` | Execution | Safety | Code execution requested without verified OS sandbox | Evaluation returns `BLOCKED` (`SECURE_EXECUTION_UNAVAILABLE`) | Stage 5 | **VERIFIED** |
| **F-11** | `test_f11_cas_full_unification` | Storage | Functional | All deliverable writes routed through CAS service | Stored under `data/artifacts/xx/hash`; zero mutable overwrites | Stage 6 | **VERIFIED** |
| **F-11** | `test_f11_cross_project_reuse` | Storage | Functional | Reuse artifact version in second project | Provenance and lineage retained; no copy-paste attribution | Stage 6 | **VERIFIED** |
| **F-18** | `test_f18_knowledge_trust_tiers` | Knowledge | Functional | Research discovery promoted to knowledge vault | Transitions through Captured $\rightarrow$ Candidate $\rightarrow$ Corroborated $\rightarrow$ Reviewed $\rightarrow$ Reusable | Stage 7 | **VERIFIED** |
| **F-18** | `test_i_model_router_persistent_capability_verification_f18` | Orchestration | Governance | Model router capability verification | Persistent probe in DB verified; unverified nodes rejected | Stage 7 | **VERIFIED** |
| **F-16** | `test_f16_route_aliases_functional` | API Contract | Functional | Call `/api/v1/orchestrator/runs` and `/api/v1/orchestration/...` | Both routes return identical successful responses | Stage 8 |
| **F-17** | `test_f17_backup_and_restore_cycle` | Operations | Integration | Generate full recovery bundle; restore to clean directory | SQLite DB, CAS blobs, vault notes, and manifest consistent | Stage 8 |

---

## 3. Mandatory Black-Box Scenario Acceptance Matrix (BB-01 through BB-30)

Scheduled for comprehensive validation in **Stage 9**:

| Scenario ID | Test Focus / Description | Expected Pass Criteria |
|:---:|---|---|
| **BB-01** | Unknown script `generate_natural_response` | Typed `CAPABILITY_GAP`, never converted to inference |
| **BB-02** | Unknown controller step in plan | Validation/replan required, not substituted with report |
| **BB-03** | Cyclic task graph in plan proposal | Explicit cycle error, no dependencies dropped |
| **BB-04** | User requested deliverables & success criteria | All preserved in stored requirement ledger |
| **BB-05** | Factual research report with zero citations | Citation audit fails (`MISSING_REQUIRED_CITATIONS`) |
| **BB-06** | Two syndicated identical articles | Not counted as independent corroboration |
| **BB-07** | Contradictory source sentences | Surfaces contradiction; similarity does not imply support |
| **BB-08** | Two research runs on same project | Zero cross-run evidence contamination |
| **BB-09** | Worker B uploads artifact to Worker A's task | HTTP 403 Forbidden; zero bytes stored |
| **BB-10** | Worker sends invalid/corrupted checksum | HTTP 400 Bad Request; partial temp file purged |
| **BB-11** | Stale lease attempt submits after reassignment | Rejected; accepted outcome remains fenced |
| **BB-12** | DNS rebinds public $\rightarrow$ private during fetch | Socket-level IP pinning blocks connection |
| **BB-13** | Browser navigates to localhost/LAN/Tailscale | Blocked by local egress filter proxy |
| **BB-14** | Admin mutating POST without CSRF token | HTTP 403 Forbidden for cookie auth |
| **BB-15** | Two different artifacts with same filename | Both immutable versions downloadable by CAS hash |
| **BB-16** | Same artifact reused in second project | Exact producer lineage retained |
| **BB-17** | Unverified artifact promoted to knowledge | Queued as candidate, not auto-promoted to verified facts |
| **BB-18** | Long controller fetch during scheduler tick | No duplicate acceptance; other project ticks progress |
| **BB-19** | Model claims service deployed without running app | Goal NOT verified; deliverable rejected |
| **BB-20** | Health report missing measured telemetry | Marked `BLOCKED`; no synthetic numbers |
| **BB-21** | Research + build functioning demo | Runnable code and tests produced plus sources |
| **BB-22** | Documented API examples in specification | All examples pass contract tests |
| **BB-23** | Backup/restore full system bundle | Database, CAS blobs, vault, and manifest consistent |
| **BB-24** | Historical projects & legacy deliverables | Fully readable after additive migrations |
| **BB-25** | External LLM / web offline | Honest degraded states; offline tasks succeed |
| **BB-26** | Prompt injection requesting approval bypass | Security policy and approval gates unaffected |
| **BB-27** | Malicious HTML/SVG artifact preview | No script execution in web dashboard |
| **BB-28** | Revoked worker authentication token | Immediate rejection of all privileged operations |
| **BB-29** | Legitimate Tailscale reverse proxy request | Allowed through proxy headers; untrusted origins blocked |
| **BB-30** | Unreviewed vault snippet injected into planner | Trust tier label attached; hard constraints preserved |
