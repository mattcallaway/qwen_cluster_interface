# Qwen Collective — Execution Security & Isolation Specification

## 1. Threat Model & Security Principles

Qwen Collective orchestrates local and remote compute resources across multiple nodes. The core security mandate is:
> **The VPS and worker nodes must never become an unrestricted remote-control channel or privilege escalation vector into the Windows controller, host operating system, or private network.**

---

## 2. Security Boundaries & Invariants

### 2.1 Least Privilege Linux Identities
* Remote programs and worker jobs execute strictly under a non-root system account (e.g. `qwen-worker` or `nobody`).
* No job may execute with root privileges or invoke `sudo`.
* The host Docker socket (`/var/run/docker.sock`) is never mounted into or accessible from worker jobs.

### 2.2 Filesystem Isolation
* Every job executes inside a dedicated temporary workspace (e.g. `/var/lib/qwen-worker/jobs/{job_id}`).
* Access to system directories (`/etc`, `/boot`, `/root`, `/home`) is denied or mounted read-only.
* Workspace cleanup runs deterministically upon task completion or cancellation.

### 2.3 Resource Quotas & Execution Limits
* Every execution specification requires:
  * Maximum timeout (default: 300 seconds; hard maximum: 3600 seconds).
  * Memory ceiling (e.g. 512 MB to 2048 MB) enforced via cgroups / ulimit.
  * CPU core limits.
  * Output buffer limits (stdout/stderr truncated at 100 KB to prevent memory exhaustion).

### 2.4 Network Egress & SSRF Protection
* By default, worker jobs have no network access unless explicitly declared and authorized.
* Web retrieval operations pass through controller/worker SSRF validation (`app.services.network_security.validate_endpoint_url`), blocking loopback, link-local, AWS/cloud metadata services (`169.254.169.254`), and private subnets unless explicitly whitelisted.

### 2.5 Secret Isolation
* Secrets (API keys, database credentials) are **never embedded in**:
  * Git repositories or deployment manifests.
  * AI model prompts or context packets.
  * Execution logs or stdout/stderr streams.
  * Markdown notes or knowledge vault files.
* Secrets are injected into the runtime process environment only at the moment of authorized execution and scrubbed upon completion.

---

## 3. Status

* **Status:** Implemented in Milestone 1 (read-only probes, permission schemas, security classification); sandboxed runtime isolation enforced in Milestone 3.
