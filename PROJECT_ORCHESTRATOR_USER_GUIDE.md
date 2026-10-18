# Project Orchestrator User Guide

Welcome to the **Autonomous Project Orchestrator** in Qwen Collective. This guide walks you through the complete lifecycle of creating, approving, executing, and monitoring autonomous distributed projects across your cluster.

---

## 1. Getting Started: Creating a New Orchestrated Project

To launch an orchestrated project:

1. Navigate to the **Projects** section in the main navigation bar.
2. Click the purple **"Orchestrate with AI"** button (or navigate directly to `/projects/orchestrate/new`).
3. You will see the project intake form:

```
┌────────────────────────────────────────────────────────────────────────┐
│  Create Orchestrated Project                                          │
├────────────────────────────────────────────────────────────────────────┤
│  Project Name:                                                         │
│  [ Distributed System Health Report                                 ]  │
│                                                                        │
│  Objective / Goal (Natural Language):                                  │
│  [ Collect basic health information from the VPS and Linux laptop,  ]  │
│  [ use a local AI model to summarize the results, and generate a     ]  │
│  [ consolidated system report.                                       ]  │
│                                                                        │
│  Autonomy Level:                                                       │
│  ( ) Level 0 — Manual Step-by-Step                                     │
│  (•) Level 1 — Assisted with Human Approvals (Recommended)             │
│  ( ) Level 2 — Supervised Autonomous Execution                         │
│  ( ) Level 3 — Fully Autonomous with Replanning                        │
│                                                                        │
│  Preferred Planning Model:                                             │
│  [ Auto (Template / Local Qwen 1.5B)                                ▼] │
│                                                                        │
│  [ Cancel ]                                  [ Create & Start Plan ]  │
└────────────────────────────────────────────────────────────────────────┘
```

### Understanding Autonomy Levels

- **Level 0 (Manual Step-by-Step)**: The engine schedules nothing automatically. You must review each task and click **"Step"** to execute one task at a time. Ideal for auditing new scripts.
- **Level 1 (Assisted — Default)**: The engine proposes tasks according to the approved plan, but pauses before dispatching each task for your review. You approve or reject individual tasks in the dashboard. Deliverables require your explicit acceptance.
- **Level 2 (Supervised Autonomous)**: Low-risk tasks (such as pre-approved diagnostic scripts and local inference) dispatch and run automatically. High-risk tasks pause for human approval. Deliverables are auto-accepted when deterministic schema checks pass.
- **Level 3 (Fully Autonomous)**: The engine runs all tasks automatically and will autonomously attempt a single replan if a task fails. The new plan will pause for your review before resuming.

Click **"Create & Start Plan"**.

---

## 2. The Planning and Review Phase

Once created, the project enters the `PLANNING` state. The AI Planner analyzes the objective, resolves dependencies, and formats a structured execution graph.

Once generated, the project enters **`AWAITING_PLAN_APPROVAL`**, and the dashboard presents the plan review interface:

```
┌────────────────────────────────────────────────────────────────────────┐
│  Status: AWAITING PLAN APPROVAL                     Revision: #1       │
├────────────────────────────────────────────────────────────────────────┤
│  Proposed Plan: "Distributed System Health Report"                     │
│  Validation: PASS (No cycles, all actions allowlisted)                 │
│                                                                        │
│  Tasks in Plan:                                                        │
│  1. [sys_health_basic] Collect VPS Health        Target: qwen-vps      │
│  2. [sys_health_basic] Collect Laptop Health     Target: qwen-laptop   │
│  3. [inference] Summarize Health Findings        Target: qwen-laptop   │
│     Dependencies: [1, 2]                                               │
│  4. [controller_step] Compile System Report      Target: Controller    │
│     Dependencies: [3]                                                  │
│                                                                        │
│  [ Edit Plan JSON ]   [ Request Clarification ]   [ Approve & Launch ] │
└────────────────────────────────────────────────────────────────────────┘
```

### Review Controls

- **Approve & Launch**: Confirms the plan, materializes the tasks into the database, and advances the project state to **`READY`**.
- **Edit Plan JSON**: Opens an inline editor allowing you to adjust task names, target nodes, or parameters. When saved, the system increments the revision number and re-validates the graph.
- **Request Clarification / Replan**: Allows you to enter guidance (e.g. *"Include swap memory metrics"*) and request a regenerated plan.
- **Reject**: Rejects the plan and returns the project to `DRAFT`.

---

## 3. The Live Execution Dashboard

Once approved and started (`RUNNING`), the dashboard (`/projects/{id}/orchestrator`) provides real-time visibility and control:

```
┌────────────────────────────────────────────────────────────────────────┐
│  Distributed System Health Report                 [ State: RUNNING ]   │
│  Autonomy: Level 1 (Assisted)                     Elapsed: 00:01:24    │
├────────────────────────────────────────────────────────────────────────┤
│  Controls:  [ Pause ]  [ Replan ]  [ Cancel ]  [ Step (L0) ]          │
├────────────────────────────────────────────────────────────────────────┤
│  Task Execution Graph:                                                 │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ [✓] Collect VPS Health (qwen-vps) — Completed (0.6s)             │  │
│  │ [✓] Collect Laptop Health (qwen-laptop) — Completed (0.5s)        │  │
│  │ [►] Summarize Health Findings (qwen-laptop) — Running (Ollama)   │  │
│  │ [ ] Compile System Report (controller) — Waiting for deps         │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  Pending Approvals (0):                                                │
│  (None. In Level 1, task approvals appear here for your click)         │
│                                                                        │
│  Resource Usage & Budgets:                                             │
│  • Tasks: 3 / 50          • Tokens: 420 / 50,000                       │
│  • Execution: 12.4s       • Retries: 0 / 2                             │
│                                                                        │
│  Live Audit Event Stream:                                              │
│  [16:02:10] Task 'Collect VPS Health' dispatched to qwen-vps            │
│  [16:02:11] Result collected: returncode=0, schema=qc.health.v1        │
│  [16:02:11] Task 'Summarize Findings' dispatched to qwen-laptop        │
└────────────────────────────────────────────────────────────────────────┘
```

### Dashboard Action Buttons

- **Start**: Initiates execution when in `READY`.
- **Pause**: Suspends new task dispatches while allowing currently running tasks to finish.
- **Resume**: Resumes automated execution from `PAUSED`.
- **Replan**: Interrupts execution and invokes the planner to adapt the remaining workflow.
- **Cancel**: Terminates project execution immediately.

---

## 4. Approvals Inbox (Human-in-the-Loop)

In **Autonomy Level 1**, every task dispatch and deliverable acceptance creates an item in the **Pending Approvals** card:

1. **Task Approvals**: Review the task action, assigned node, and input parameters. Click **"Approve"** to send the task to the worker queue, or **"Reject"** to halt dispatch.
2. **Budget Approvals**: If a project reaches a configured resource ceiling (e.g. 50 tasks or 50,000 tokens), execution pauses in `BLOCKED`. Approving the budget notification doubles the limit and resumes execution.
3. **Deliverable Approvals**: When all tasks finish, final reports appear for review. Inspect the markdown output and click **"Accept Deliverable"** to transition the project to **`COMPLETED`**.

---

## 5. Deliverables and Artifact Downloads

When the project reaches `COMPLETED`, the **Project Deliverables** section displays all registered artifacts:

- **System Reports**: Download consolidated markdown summaries (`consolidated_health_report.md`).
- **Raw Telemetry**: Download verified machine JSON files (`result.json`) with intact SHA-256 hashes.
- **Integrity**: Every artifact download includes security headers (`nosniff`) and checksum verification.

---

## 6. Troubleshooting Common Scenarios

- **Task shows "Offline / Waiting for Node"**: Verify that the assigned worker node appears as "online" in the cluster dashboard. The orchestrator will not dispatch tasks to nodes whose heartbeats are older than 60 seconds.
- **Run shows "BLOCKED (Approval Required)"**: Check the **Pending Approvals** card in the dashboard. An operator decision is required before execution can proceed.
- **Task Failed with Timeout / OOM**: The engine will automatically back off and retry up to the configured retry budget. You can click **"Retry Task"** to manually initiate an attempt.
- **Need to Change Autonomy Mid-Project**: You can change the autonomy level at any time in the project controls without restarting the project.
