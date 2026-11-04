# Bounded process supervision

Development candidate, 2026-10-27. See [work register](README.md), [technical map](TECHMAP.md), [execution protocol](EXECUTION_PROTOCOL.md), and [increment report](evidence/INCREMENT_13_BOUNDED_PROCESSES.md).

## Implemented boundary

`vps_agent/bounded_process.py` supervises reviewed Class B jobs with a parent-owned Windows Job Object (`windows_job.py`) and a fixed stdlib-only child supervisor (`process_job.py`). The job has kill-on-last-handle-close, no breakaway flags and a 32-process ceiling including the supervisor. The supervisor joins the job and closes its local job handle **before** launching the reviewed target; only the parent retains the handle. A parent crash therefore closes the last handle. A separate supervisor watchdog can terminate the named job if the parent's execution loop stalls.

The Windows API behavior and structure layouts were checked against Microsoft’s [Job Objects documentation](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects), [extended limit structure](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information), and [job accounting/query API](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject). Local tests exercise real jobs and check that recorded child/grandchild processes are no longer running; documentation alone is not acceptance evidence.

This controls process lifetime. It does not isolate host files, credentials, network access or verifier results. It is not a hostile-code sandbox and does not lift generated-code holds. Linux process groups are deliberately not accepted as an equivalent boundary: a verified cgroup/backend is still needed. Unsupported platforms or failed kernel setup have no unrestricted fallback.

## Limits and result handling

- The authorization gate still caps runtime at 30 seconds and may impose a smaller reviewed-script limit. Process launch/supervisor setup count toward the runtime deadline.
- Stdout and stderr drain in fixed 4 KiB reads into a shared bounded capture budget, 64 KiB combined by default. Exceeding the budget kills the job and reports `output_limit`; output is not buffered without limit and then presented as success after truncation. The existing human-readable summaries remain limited separately.
- Timeout, cancellation, live descendants after the main target exits, output overflow and uncertain cleanup prevent success. A zero main exit code cannot waive descendant or cleanup checks.
- Kernel active-process accounting and pipe-drain completion establish process cleanup. Verification failures report `cleanup_unverified`; handle closure remains a kill fallback, but is not reported as confirmed cleanup without the accounting evidence.
- Artifacts are collected only after process cleanup is confirmed. Failure paths suppress artifact collection. Workspaces are removed only after confirmed process cleanup and path checks. Deletion failures return an error and suppress artifacts; uncertain cleanup retains the workspace for review rather than deleting files while processes may still write.
- Process cleanup has a bounded wait; general filesystem deletion latency, disk quotas, memory/CPU budgets and descriptor-based hostile-filesystem defenses are not certified by this increment.

## Revocation and disconnection

For reviewed scripts, the worker heartbeat thread passes a cancellation event into the supervisor. Lease heartbeat denial, any non-200 status or network exception triggers cancellation. The server rechecks script feature state, explicit node permissions/holds, current native plan/project/run binding and the consumed authorization record during renewal, within the lease update transaction.

Revocation is detected on a heartbeat, not instantaneously. The current interval is 5–15 seconds with a request timeout; the original runtime deadline remains the independent upper bound. Existing non-script heartbeat behavior is preserved. Cancellation does not grant a retry; the one-shot authorization still requires a new approved task for recovery.

## Testing qualifications

Tests use reviewed local fixtures and synthetic native project/plan/task records with authenticated TestClient requests. They do not run a model, connect to the VPS or prove full OS confinement. Kernel-process tests bypass the Windows virtual-environment launcher because its own cleanup can conceal an orphan; they exercise the base interpreter and inspect actual recorded process identities.

The Linux launcher source currently times out a `systemd-run` client without independently proving cgroup-wide cleanup. That path remains NO-GO. A future VPS backend must enforce service/cgroup lifetime, revoke/kill the whole group, confirm emptiness and keep verifier authority outside the generated app. It requires an isolated verification environment and separate approval before live provisioning or service changes.
