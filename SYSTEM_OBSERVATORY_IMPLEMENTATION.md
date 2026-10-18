# Qwen Collective — System Observatory Implementation
**Implementation Details, File Architecture & Code Structure**

This document details the code architecture, service design, backend integration points, and UI implementation for the System Observatory.

---

## 1. File Structure and Locations

```text
app/
├── main.py                                  # Lifespan startup/shutdown of Observatory engine
├── migrations.py                            # Schema Migration 10 (Observatory tables & indexes)
├── routes/
│   ├── observatory.py                       # Web & REST API routes (/observatory & /api/v1/observatory/*)
│   └── worker.py                            # Worker telemetry ingestion (POST /api/v1/worker/telemetry)
├── services/
│   ├── worker.py                            # Heartbeat telemetry recording & inference result hook
│   └── observatory/
│       ├── __init__.py                      # Package exports
│       ├── models.py                        # Strict Pydantic schemas for telemetry, rollups, alerts
│       ├── storage.py                       # SQLite persistence, time-series rollups, retention pruning
│       ├── collector.py                     # Native Windows controller collector & worker ingestion
│       ├── analytics.py                     # Capacity-weighted aggregates, model comparisons, insights
│       ├── alerting.py                      # Hysteresis rule evaluation, cooldowns, lifecycle
│       └── engine.py                        # Isolated background sampler & rollup scheduler thread
└── templates/
    ├── base.html                            # Primary navigation link for System Observatory
    ├── observatory.html                     # 6-tab responsive dashboard with real-time Canvas charts
    └── infrastructure/index.html            # Prominent quick-link to Observatory from Infrastructure

vps_agent/
├── agent.py                                 # send_heartbeat() & send_telemetry() outbound push
└── capabilities.py                          # collect_live_telemetry() reading Linux /proc & /sys

tests/
└── test_observatory.py                      # Comprehensive automated test suite (9 test cases)
```

---

## 2. Key Modules & Functional Logic

### 2.1 Schema Migration (`app/migrations.py`)
- **Version**: Schema version `10`.
- **Atomic Execution**: Created within an immediate transaction following the project's migration harness.
- **Tables Added**:
  1. `telemetry_samples`: Raw time-series measurements with indexes on `(node_id, timestamp)` and `(timestamp)`.
  2. `telemetry_rollups_minute`: 1-minute aggregations (`avg/min/max`) with unique constraint on `(node_id, bucket_time)`.
  3. `telemetry_rollups_hour`: 1-hour aggregations with unique constraint on `(node_id, bucket_time)`.
  4. `inference_telemetry`: Detailed Ollama token accounting with indexes on node, model, project, and task.
  5. `observatory_alert_rules`: Health thresholds and hysteresis durations.
  6. `observatory_alerts`: Active and historical alert states (`firing`, `acknowledged`, `resolved`).

### 2.2 Native Windows Controller Collector (`app/services/observatory/collector.py`)
- Uses standard library `ctypes`, `shutil`, `os`, and `time` with zero external pip dependencies.
- **Physical RAM and Swap**: Calls Windows API `GlobalMemoryStatusEx` with `MEMORYSTATUSEX` struct. Extracting total physical memory, available memory, memory load percentage, and paging file capacity.
- **CPU Utilization**: Calls `GetSystemTimes` with `FILETIME` structs, tracking delta between kernel and user execution ticks over sampling intervals.
- **Process Memory**: Calls `K32GetProcessMemoryInfo` on the current FastAPI process handle to track worker and controller RSS resident set size.
- **Power & Battery**: Queries `GetSystemPowerStatus` to determine AC line status (`1` for connected, `0` for battery, `-1` for desktop/unknown) and battery life percentage.
- **GPU Acceleration**: Queries `nvidia-smi` using structured CSV output. If discrete GPU hardware is absent or virtualized, gracefully returns `{supported: False, status_note: "Not supported"}` without error.

### 2.3 Worker Telemetry Ingestion (`app/routes/worker.py` & `app/services/worker.py`)
- **Authentication**: Worker requests must carry valid Bearer tokens validated through `get_authenticated_worker`.
- **Spoofing Protection**: In `submit_worker_telemetry`, the endpoint strictly verifies that `worker["node_id"] == req.node_id`. Any worker attempting to submit metrics for another node is immediately rejected with HTTP `403 Forbidden`.
- **Heartbeat Co-Ingestion**: In `record_worker_heartbeat`, standard 15-second heartbeat pings automatically persist a hardware sample into `telemetry_samples` and trigger alert checks.
- **Authoritative Inference Hook**: In `submit_task_result`, completed inference task payloads are inspected for `execution_class == "Inference"` or Ollama tokens; metrics are automatically extracted and recorded into `inference_telemetry`.

### 2.4 Analytics Engine (`app/services/observatory/analytics.py`)
- **Capacity Weighting Formula**:
  - Unweighted averages distort cluster utilization when small 1 vCPU VPS nodes are averaged equally with high-core desktops.
  - The analytics engine computes core-weighted CPU:
    $$\text{aggregate\_cpu\_pct} = \frac{\sum_{i} (\text{cpu\_pct}_i \times \text{cores}_i)}{\sum_{i} \text{cores}_i}$$
  - And memory capacity weighting:
    $$\text{aggregate\_ram\_pct} = \frac{\sum_{i} \text{ram\_used}_i}{\sum_{i} \text{ram\_total}_i} \times 100$$
- **Legacy Node Isolation**: Nodes in `LEGACY_NODE_IDS = {"node-d35568f5"}` are explicitly filtered out from physical node counts and capacity totals.
- **Model Performance Comparison**: Aggregates throughput (tokens/sec), latency percentiles, load durations, and success rates across models (e.g. `qwen2.5:1.5b` vs `llama3.2:1b`).
- **Deterministic Diagnostics**: Evaluates system conditions (swap paging, CPU temperatures, storage headroom, token throughput) to produce structured recommendations citing specific supporting telemetry.

### 2.5 Alerting Engine (`app/services/observatory/alerting.py`)
- **Hysteresis Implementation**: Memory tracker `_HYSTERESIS_TRACKER[(rule_name, node_id)]` records the timestamp when a threshold was first breached. An alert is only fired if the condition persists beyond `duration_seconds`.
- **Auto-Resolution**: When subsequent samples show metrics returning to safe thresholds, any firing alert is transitioned to `resolved` state.
- **Cooldown Window**: Resolved alerts enter a configurable cooldown period (e.g. 300s) to prevent flapping.

### 2.6 Background Thread (`app/services/observatory/engine.py`)
- Runs as an isolated daemon thread `SystemObservatoryEngine` registered with FastAPI lifespan in `app/main.py`.
- Executes controller sampling every 5 seconds, heartbeat timeouts every 15 seconds, minute rollups every 60 seconds, hourly rollups every 3600 seconds, and retention cleanup every 12 hours.

### 2.7 Frontend Dashboard (`app/templates/observatory.html`)
- Follows the application's dark-slate UI aesthetic and CSS design tokens.
- Fully reactive layout with tab navigation across all 6 core sections.
- Canvas-based charting engine rendering smooth multi-series line graphs with zero external CDN dependencies.
- Auto-refresh toggle (5-second polling) and manual refresh buttons.
- Direct CSV export links for both telemetry and inference data.
