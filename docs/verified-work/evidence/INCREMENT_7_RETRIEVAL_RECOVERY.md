# Increment 7: durable retrieval budgets, deadlines and recovery

**Completed and tested in the isolated candidate. Production files, data, holds, credentials and running services remain unchanged.**

## Budget reservation before I/O

The new retrieval-attempt ledger reserves one request and up to eight seconds of the project's existing time allowance before external I/O can begin. Reservations, outcome records and append-only audit events are persisted in the same temporary application database used by the native pipeline.

Worker-side reservations are committed atomically with native lease assignment. The lease response supplies the reservation identity and I/O deadline. The standalone worker refuses retrieval without that reservation, in addition to the existing local opt-in and controller authorization checks. Worker result submission settles the reservation against the exact task and lease. A late or abandoned reservation cannot support a native observation receipt.

The controller observer independently reserves its own request allowance in a separate short transaction. The signed receipt is now version 2 and binds to a successful observation reservation. A verified source normally costs two request slots: one worker retrieval and one independent controller observation. The historical `docs_used` counter therefore counts charged requests on this path, not independent publishers or distinct documents.

Failures, deadlines, crashes and abandoned reservations retain the full reserved allowance. A successful controller observation can refund its unused time using controller-measured elapsed time. Worker-reported durations never authorize a refund. Reusing an existing valid receipt does not consume another request or fetch again.

## Network work outside write transactions

Observation now has three phases:

1. Validate scope and permissions, reserve budget, commit.
2. Retrieve and extract in a separately supervised process, with no database write transaction open.
3. Revalidate the native chain and current holds; settle the reservation and commit the receipt atomically.

Failure settlement uses its own committed transaction. Rolling back the calling ledger connection cannot undo the charge or erase a failed observation. If receipt construction/persistence fails after a successful fetch, no success event or receipt survives, and the conservative failure charge remains.

The manager first obtains the durable receipt on an idle connection, then stores the ledger snapshot and feedback proposals in a short transaction. Reuse of the receipt inside that transaction is verification-only; it cannot initiate a network request. Recovery writes during worker leasing also commit before the claim transaction starts, avoiding an implicit-transaction/nested-BEGIN problem.

## Whole-operation deadlines

The standalone `research_job` executes the fixed HTTPS fetch/extraction operation in a child process. A single monotonic deadline covers child startup, DNS, connections, redirects, rate-limit waits, streamed body reads, extraction and parent result decoding. Slow responses cannot extend the deadline by resetting per-read timeouts or redirecting repeatedly. The child cannot select an executable, script or signing authority from task data.

The parent kills and reaps the child on timeout; tests verify that those child processes have exited. Up to two seconds are allowed for termination/reaping after the operation deadline. A child watchdog uses the same host-monotonic deadline as a fallback when its parent disappears. The test suite exercises a real parent-process crash and observes the child's watchdog exit.

The child receives only the queued URL, deadline and time allowance. Its environment is reduced to basic operating-system path/temp variables; controller signing keys and worker credentials are not passed. It uses the vendored, parity-checked secure fetcher/extractor already packaged with the worker.

This is not a claim of hard real-time behavior under a malfunctioning OS. The external parent kill is stronger than the Python child watchdog for a parser/native extension holding the GIL. OS job/cgroup containment for parent loss during such a stall remains a deployment hardening requirement. The current implementation explicitly limits its supported MIME/extraction paths.

## Restart recovery and retry bounds

Reservations have ACTIVE, SUCCEEDED, FAILED, TIMED_OUT or ABANDONED states. Expired active reservations become ABANDONED, with no refund, and receive an immutable recovery event. Recovery is idempotent and runs through the existing lease reconciler and before a new controller observation reservation.

Retries of an operation must pass persisted backoff (5 seconds, then exponential growth capped at 60 seconds), a maximum of three charged reservations, and the project's remaining request/time limits. Concurrent requests serialize only their short reservation transaction; a duplicate operation or last-slot race cannot double-spend the budget.

Automatic graph-level retrieval retries remain disabled. This increment establishes accounting and recovery primitives; it does not silently authorize autonomous retry policy or remove existing task-approval requirements. A recovered reservation is not proof of a successful retrieval.

## Validation evidence

`increment7-integration-tests.txt`: **148 passed**, including 17 new bounds/recovery tests and the existing packaged-worker process, native receipts, feedback, objective/blocked reporting, authorization, worker execution, reliability, buffering and version checks.

`candidate-tests.txt`: **31 focused containment/provenance/aggregation/Decimal-ledger checks passed** after the final changes. These two runs contain 179 passing checks, not the entire repository test suite.

The new tests cover:

- actual process termination for a stalled fetch, trickling stream, repeated redirects and stalled extraction;
- a successful process returning the exact extracted statement;
- a real process crash after committing its reservation, followed by restart recovery without a refund;
- worker reservation recovery through the existing reconciler, even before any source request occurs;
- failure charges surviving caller rollback and a failed receipt-persistence transaction;
- immediate retry backoff, three-attempt exhaustion, and retained time/request charges;
- duplicate-operation and last-budget-slot contention across independent database connections;
- another writer committing an observer hold during network work, proving no write lock spans that work and that the post-I/O hold check still blocks acceptance;
- durable timeout records, append-only audit events, and a child watchdog surviving parent loss.

Protocol tests use the actual shared retrieval operation with an in-process synthetic HTTP transport for speed. The deadline and packaged-worker tests exercise the actual child-process boundary, with synthetic source responses. The final harness blocks live model discovery as well as outbound sockets, including the legacy hardware check's potential CLI fallback. No live research or model inference was requested. Existing HTTP-client deprecation and duplicate OpenAPI operation-ID warnings remain.

The initial crash/concurrency harness issues were corrected without weakening application controls: the crash child now blocks production environment loading, and each test thread opens its own database connection.

## Deliverables and release gates

The updated review package contains the patch, original-byte manifest, source, tests and logs. The standalone worker development ZIP has been regenerated to include the supervised retrieval job. No key, credential, environment file or test driver is packaged with it. These artifacts remain development handoffs, not production installers.

Explicit schema installation in development now creates retrieval-attempt/event tables. No production migration has been applied; deploying this code requires a reviewed migration and rollback plan. The new signed receipt format rejects older unsupported receipt versions instead of silently upgrading them.

The new accounting covers the dedicated native retrieval pipeline. Legacy controller-fetch paths still require migration or explicit containment before claiming universal egress accounting. The research certification hold remains the protection against using those paths as trusted certification evidence.

Next development work should bind evidence and claims to exact deliverable/contract revisions, establish source applicability and complete claim coverage, and invalidate affected outputs when evidence changes. Typed financial generation/acceptance integration and controller signing-key privilege separation also remain outstanding. Full regression, real-environment canary review, backups and rollback rehearsal must precede any production deployment or restart request.

**Release status: NO-GO. No production approval is requested at this stage.**
