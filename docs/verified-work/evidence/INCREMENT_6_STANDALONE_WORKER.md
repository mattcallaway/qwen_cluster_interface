# Increment 6: standalone retrieval worker and native scheduler loop

**Result: the isolated controller scheduler and a packaged worker process completed the native retrieval/receipt/feedback loop. Production remains unchanged.**

## What changed

The research planner now converts a ledger proposal into a dedicated `research_fetch` graph task. The task retains its proposal identity and queued URL, pins the worker, requires explicit task approval, and permits one attempt. It does not execute a script supplied by a model or task payload.

Plan validation recognizes this action and rejects unsupported fields, missing proposal IDs, credential-bearing/non-HTTPS URLs, unpinned execution, automatic approval and automatic retries. A separate controller action was added rather than relabeling a developer script or silently falling back to inference.

The existing node router requires explicit web-retrieval permission and an allowed task type, with no operational hold. The authoritative leasing gateway enforces web permission and operational holds independently. Existing inference-only worker settings are unchanged.

The scheduler now dispatches approved retrieval tasks through the existing task service, leases and worker result protocol. It checks the current approved plan and scoped feedback proposal before queueing. Direct dispatch cannot substitute for the recorded task approval. A configured controller verifier is required; no signing key is loaded by default. Result collection independently observes the retrieval, verifies the receipt and feeds the result back into the evidence ledger. Task acceptance means retrieval evidence passed its checks, not that a factual claim or project is FULFILLED.

The research manager has a lightweight feedback-only initialization mode, avoiding unrelated browser/cache/source-service setup on this path. The new action is also explicitly classified as research by project evaluation, so omission of a separate coverage artifact cannot remove the research hold.

## Standalone worker

The outbound worker agent advertises retrieval only when its new local setting `QC_WORKER_ALLOW_WEB_FETCH` is explicitly enabled. The setting defaults off. The executor independently refuses retrieval without local permission and a task/lease/idempotency identity; task data cannot turn the setting on. Unsupported input fields, including script content, are rejected.

The dedicated operation uses the same HTTPS/public-destination fetcher and extractor as the controller, with the existing size/timeout controls and no browser/PDF fallback. It returns hashes and URL identity, not a truth verdict or signing key. The controller still makes its own independent observation.

The worker's import of `app.version` was removed. A generated local version module and four vendored research modules let it run without the controller `app/` tree. Vendoring rewrites import paths only; a manifest records controller-source and packaged-module hashes, and parity tests verify the derivation. Version representations now use the shared declared version when the development bundle is built.

`scripts/build_retrieval_worker.py` builds a development ZIP containing worker Python files, the runtime manifest and requirements resolved from the tested environment. It excludes arbitrary JSON/configuration files, environment files, credentials, controller code and the process-test driver. This is a development bundle, not a production installer or a complete deployment of existing script-execution isolation services. Dependency installation, platform compatibility, transitive dependency locking and deployment review remain release prerequisites.

## Process-level test evidence

The positive process test performs the following in temporary storage:

1. Creates a project/research run, requirement, claim and ledger-generated evidence-gap proposal.
2. Uses the normal planner revision service, plan validation and plan approval/materialization service. It does not manually insert a dispatched controller task or completed lease.
3. Starts the run and ticks the real scheduler. The test proves that plan approval alone queues no retrieval task; the scheduler requests task approval.
4. Approves that isolated task and ticks the scheduler again, which creates and dispatches its underlying native task.
5. Builds/extracts the standalone worker bundle and starts an isolated local controller HTTP server. The controller uses the already initialized temporary test database, with application startup/background loops disabled.
6. Launches a separate Python process with isolated import mode. That process imports only the packaged worker, registers using temporary test credentials, and runs the agent's real poll/lease/execute/submit cycle over actual local HTTP. The child asserts that no controller `app` module was imported.
7. Ticks the controller scheduler to collect the result. Independent controller observation produces a signed receipt and a ledger snapshot, and the planner proposes the remaining applicability-verification path.
8. Confirms that the retrieval task is accepted, factual/project certification remains false, the project is not completed, and the isolated server shuts down.

Public research responses and synthetic `.example` DNS are simulated separately in worker and controller. The worker child can connect only to its assigned ephemeral loopback controller port. It cannot contact the production controller, Ollama, a Pi/VPS, or the public network. The test executes one genuine agent cycle, not a long-running installed service or real hardware canary.

## Validation results

- `increment6-integration-tests.txt`: **130 passed**, covering native retrieval, feedback, objective/blocked reporting, authorization, existing worker execution, offline buffering/reconciliation, reliability and version identity.
- `candidate-tests.txt`: **31 passed**, covering containment, native provenance, aggregation and Decimal finance, including a new research-hold check for `research_fetch` without a coverage artifact.
- `increment6-process-tests.txt`: **9 passed** in the final worker/packaging-focused run. This overlaps eight cases in the integration run and adds the credential-file exclusion case; do not add these runs as if every case were distinct.

The broader suite initially found four missing test-support-file failures because two unchanged scripts were absent from the isolated copy. Copying those existing scripts resolved the setup issue; no sandbox policy was weakened. Existing HTTP-client deprecation warnings and a duplicate OpenAPI operation-ID warning remain. The entire repository suite and a real Linux/Windows hardware fleet run have not been completed.

## Approval and remaining work

The worker ZIP and review package contain no production settings or credentials. No production files, database/schema records, holds, credentials, services, processes or Russian River artifacts were changed. Only an isolated test HTTP server and a bounded worker child process were started and stopped.

This increment closes the prior worker-packaging and fixture-only-scheduler limitations. Remaining release blockers include:

- Failed-observation budget reservations/backoff and strict whole-operation deadlines, including streaming responses and controller transaction lock duration. Automatic retrieval retries are rejected until this is addressed.
- Controller signing-key provisioning, rotation and process/OS privilege separation from developer scripts and database administrators.
- Complete claim inventory, source authority/applicability verification, exact deliverable/contract revision binding and evidence-driven invalidation of previously accepted outputs.
- Integration of typed financial ledgers into generation/rendering and final certification, beyond the tested validator prototype.
- Reviewed schema migrations, full regression/real-environment canary evidence, release manifest, backup and rollback rehearsal.

The next development stage should address failure accounting and hard retrieval deadlines before any live canary is proposed. Production integration, schema/key/policy changes, node enablement, deployment and service restarts still require explicit authorization. **Research certification stays blocked; production release remains NO-GO.**
