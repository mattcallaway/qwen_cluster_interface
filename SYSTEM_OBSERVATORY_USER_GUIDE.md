# Qwen Collective — System Observatory User Guide
**Operator Handbook & Control-Room Interface Reference**

## 1. Navigating to the System Observatory

You can access the System Observatory in two ways:
1. Click **System Observatory** in the primary sidebar navigation menu.
2. Direct URL: `http://127.0.0.1:8000/observatory` (or via your Tailscale HTTPS URL: `https://desktop-44h6qvp.tailb5d90b.ts.net/observatory`).
3. Or click the **🔭 System Observatory** button at the top of the **Infrastructure** dashboard.

---

## 2. Using the Six Principal Tabs

### Tab 1 — Overview
- **Live Summary Widgets**:
  - **Connected Nodes**: Count of online nodes versus total registered physical nodes.
  - **Cluster CPU**: Defensible capacity-weighted utilization across all online processor cores.
  - **Tokens Generated (24h)**: Authoritative token synthesis counter over the rolling 24-hour window.
  - **Task Success Rate**: Percentage of completed vs failed worker operations.
- **Physical Machines Health**: Real-time progress bars showing individual CPU and RAM usage, plus online/offline/degraded status badges.
- **Cluster Resource Balance**: Visualizes aggregate physical RAM and aggregate OS swap partition usage.

### Tab 2 — Machines
- **Machine Selector**: Switch between `controller-local`, `qwen-vps`, `qwen-laptop`, and `desktop-worker-01`.
- **Hardware Telemetry Panels**:
  - **CPU Metrics**: Current utilization %, per-core breakdown, Linux load averages, and worker process CPU share.
  - **Memory & Swap**: Physical RAM used/available/total, plus active swap allocation.
  - **GPU Acceleration**: NVIDIA GPU name, utilization, VRAM usage, temperature, and power draw (or explicit *Not supported* note).
  - **Storage & Headroom**: Mounted filesystem capacity, free disk space, and read/write I/O throughput.
  - **Power & Thermal**: CPU temperature, AC line status, and battery percentage (crucial for laptops).
  - **OS & Worker Service**: Kernel/OS version, system uptime, and worker daemon resident memory (RSS).
- **Interactive Live Chart**: Real-time multi-series line chart updating every 5 seconds tracking CPU %, RAM %, and Swap %.

### Tab 3 — Models & Tokens
- **Model Performance Comparison**: Side-by-side comparison between `qwen2.5:1.5b` and `llama3.2:1b`, highlighting which model delivers faster synthesis (tokens/sec) and faster weight loading.
- **Inference Telemetry Table**: Authoritative execution metrics per model, including prompt tokens, generated tokens, total tokens, throughput, average latency, and load duration.
- **Token Accounting & Cost Ledger**: Token volume broken down across Today, Last 7 Days, Last 30 Days, and Lifetime. Clearly notes $0.00 external API billing for local execution.

### Tab 4 — Tasks & Projects
- **Worker Execution Efficiency**: Historical performance of each worker node, including task leases, successful completions, errors, timeouts, success rate %, and total useful work duration.
- **Project Orchestrator Telemetry**: Tracks active project runs, plan tasks (accepted vs failed), and task retry overhead percentage.

### Tab 5 — Errors & Alerts
- **Active Alerts Banner**: High-priority alert notices with severity indicators (`critical`, `warning`, `info`). Click **Acknowledge** to mark an alert as reviewed.
- **Deterministic System Insights**: Rule-based diagnostics identifying developing bottlenecks (e.g. swap thrashing on low-memory nodes, elevated temperatures, storage headroom limits).
- **Configured Health Alert Rules**: Table of rules, metric names, trigger thresholds, and hysteresis durations.

### Tab 6 — History & Analytics
- **Resolution Selector**: Switch between Raw High-Frequency Samples, 1-Minute Rollups (30-day retention), and 1-Hour Rollups (1-year retention).
- **Historical Cluster Chart**: Interactive Canvas visualization of long-term compute trends.
- **CSV Data Export**: One-click download buttons for `qwen_observatory_telemetry_*.csv` and `qwen_observatory_inference_*.csv`.

---

## 3. Configuration & Auto-Refresh Controls

- **Auto-Refresh Toggle**: Located in the top-right corner. Checked by default; polls the controller every 5 seconds. Uncheck to freeze the display for detailed inspection.
- **Manual Refresh**: Click 🔄 **Refresh** to perform an immediate data update.
- **CSV Exports**: Use the **Export Telemetry CSV** and **Export Tokens CSV** buttons to extract authoritative data for external reporting or auditing.
