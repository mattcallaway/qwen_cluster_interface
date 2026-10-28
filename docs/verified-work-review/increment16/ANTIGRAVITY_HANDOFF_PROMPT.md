# Antigravity handoff — verified Qwen Collective work through increment 16

Continue the existing Qwen Collective project. Read the actual repository and the handoff evidence before changing code. This handoff is dated 2026-10-28. Treat the files below as evidence to inspect, not proof that production is ready.

## Locate the work

- Your existing checkout: `C:\Users\Matt C\.gemini\antigravity-ide\scratch\qwen-collective`.
- Codex review outputs: `C:\Users\Matt C\Documents\Codex\2026-10-26\referenced-chatgpt-conversation-this-is-an\outputs`.
- Candidate source: the `candidate` directory in those outputs. Cumulative changes: `qwen-containment.patch`; exact original/candidate SHA-256 bindings: `candidate-manifest.json`.
- GitHub repository: `https://github.com/mattcallaway/qwen_cluster_interface`. Look for review branch `codex/verified-work-increment16`, directory `docs/verified-work-review/increment16`. Verify that branch exists and record its actual commit; do not assume publishing succeeded solely because this prompt names it.
- Read `INCREMENT_16_PROTECTED_CODE_VERIFICATION.md`, candidate `docs/verified-work/README.md`, `TECHMAP.md`, `PROTECTED_VERIFIER.md` and `PRODUCTION_RELEASE.md`. These integrate with the original README, canonical `TECHMAP.md`, legacy `TECH_MAP.md` and remediation register.

## What has been done

The isolated development candidate contains the cumulative systemic-remediation work through increment 16: native evidence bindings, fail-closed research/software certification, source/claim applicability checks, append-only feedback, Decimal financial constraints and derivations, negation-preserving repair containment, native retrieval, versioned code proposals, one-shot script authorization, bounded process supervision, selected actual Linux containment tests, and a protected code-verification bridge. Read each increment's report for exact limitations; do not infer general reasoning, research accuracy or deployment readiness from this list.

Increment 16 adds a native controller service API, `engine.verify_native_code_proposal(...)`, held by default and rejected outside development mode. It can capture exact immutable completed worker inference response bytes into a canonical `qc.code_bundle.v1` artifact. Native task, completed lease, worker, immutable execution/idempotency, current approved plan and pinned artifact/test versions must agree. Origin labels alone cannot authorize developer-rewritten bytes.

The controller durably issues a fresh, bounded grant to an explicitly configured protected verifier. On the authorized laptop, a one-shot root process runs submitted source as an unprivileged, confined systemd root-image service. Only read-only source and public input enter that environment. Expected answers, private audit storage and the per-grant HMAC receipt key stay outside. The controller authenticates the receipt, independently compares observed answers, and rechecks current bindings. Replay, stale evidence, forged success flags, wrong output, missing cleanup and transport failure cannot pass. HMAC here is a trusted controller/verifier boundary, not public-key proof or independence from a compromised controller/root operator.

## Verified results and limits

- 137 focused local checks passed.
- Selected regression suite: **417 passed, 6 opt-in live tests skipped, 7 warnings**, in `increment16-integration-tests.txt`.
- Those six live scenarios were separately enabled and all passed, covering **18 actual confined laptop executions**: correct code, wrong answers, forged pass flags, boundary tampering, deadline and output overflow. See `increment16-laptop-live-tests.txt` and the final batch's `laptop-verifier-*.json` files.
- 55 fixed external fixture-oracle checks passed in `laptop-verifier-independent-checks.json`.
- Native database records in these laptop tests were **synthetic fixtures**. A real controller-dispatched, leased model-worker coding canary has **not** run. Do not describe this as autonomous live model coding acceptance.
- Existing laptop worker/model service PIDs and running state stayed unchanged; temporary policies were removed and all observed test process groups were empty. No existing services were restarted, no packages installed for this increment, no production deployment performed and no safety holds changed.
- Passing the narrow approved integer-case profile records only scoped behavior evidence. `accepted` and `certification_eligible` stay false. Independent test authorship/adequacy, general code correctness, web applications, VPS deployment and full containment escape resistance remain unproven.

## First: scan both working copies and GitHub without overwriting anything

1. Record GitHub's actual default branch tip and review-branch tip. Record your local branch, HEAD, upstream, working-tree/index changes and untracked counts. Do not print credentials or private data. Inspect `SYNC_STATUS.json` if present, but refresh observations.
2. Compare your current original bytes against every non-null `original_sha256` in `candidate-manifest.json`. Compare candidate bytes against every `candidate_sha256`. Report exact mismatched paths and expected/observed hashes. A null original hash means the path did not exist in the recorded baseline; if it now exists, treat that as drift to reconcile.
3. The original baseline was a **dirty working tree**, not HEAD alone, based on local commit `5d3d90a21a33b1a8b9b0bc6c81387bd9f40a60fa`. At the 2026-10-28 read-only check, GitHub `main` was `4be7f547f4919380a22abdd2122961d559d2f46f`; the named local remediation branch was not present on GitHub. Refresh these facts. The review publication is a package branch, not proof that runtime source on main or either machine has been synchronized.
4. Preserve all existing local/index/untracked work. Do not reset, clean, force push, blindly apply the patch or stage the entire original directory. It contains unrelated artifacts and possible local secrets. Do not publish private keys, passwords, tokens, `.env`, databases, CAS blobs or unrelated project files.

## Reconcile and update GitHub

Create an isolated integration branch based on the correct current repository state. Reconcile the dirty baseline and candidate changes file by file; preserve your independent work and disclose conflicts. Use manifest hashes and an applicability check before applying any patch. The package branch contains review artifacts; its existence does not mean the candidate has been merged into runnable source.

Use the existing repository README and canonical technical map as entry points and integrate the verified-work register and current approval gates. Keep historical readiness claims clearly marked as historical. Do not replace the project's documentation with an unrelated standalone map.

Run meaningful focused tests and the selected regression suite in an isolated test data directory, with production orchestration/network side effects disabled. See the reviewed rehearsal scripts and logs for exact selection and environment. Do not use production services or data to satisfy tests. Do not rerun privileged laptop rehearsals by assuming current approval/configuration: first inspect current host state, policy/image bindings and the exact scoped test plan; existing authorization covers only the reviewed isolated tests and does not authorize production activation.

Commit only reviewed code, tests and documentation with no unrelated files or credentials. Push a normal integration/review branch to GitHub and create/update a pull request if credentials/tooling permit. Do not merge main, force push or trigger a production deployment. Verify the remote branch SHA equals the local commit after pushing. If authentication is unavailable, report the blocked publication explicitly and provide the prepared commit/branch; never claim GitHub is updated without remote confirmation.

Finish with a short sync report: local branch/HEAD, GitHub branch/HEAD, baseline/candidate hash matches and drift, test results, known differences between Antigravity and Codex, and pending gates. “In sync” requires actual compared commits and bytes; distinguish review package synchronization, integrated runtime source synchronization and deployment state. Do not call two systems synchronized simply because both can read this prompt.

## Where we are going next

Build a fresh isolated native leased-model coding canary, with a separate test controller database and worker identity. Generate an exact code bundle through the real controller → task → lease → worker inference path. Feed its unmodified immutable response through capture and the protected verifier. Demonstrate both a correct result and a failed case, followed by a bounded revision using a fresh grant; exercise stale/revoked work and cleanup. Avoid leaking hidden answers into repair prompts. No developer-script substitution for native execution or certification is allowed.

After that, propose explicit scheduler budgets, approval/revocation/recovery rules, test-adequacy review and narrowly scoped automatic verification/feedback. Only after those gates pass should you propose a concrete production rollout with exact build/configuration/schema changes, backup/migration rehearsal, rollback triggers and target services.

Preserve current Pi/desktop/VPS safety holds. Do not regenerate the Russian River project. Do not restart, deploy, migrate production or remove holds without a separate concrete approval. Continue authorized reversible development and verification without repeatedly asking for permission; ask only for an actual missing decision or a production approval gate.
