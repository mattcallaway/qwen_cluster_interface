# Qwen Collective — System Observatory Architecture
**Real-Time Monitoring, Historical Telemetry, Performance Analytics & Cluster Health**

## 1. Executive Summary & Design Principles

The **System Observatory** is the centralized monitoring, telemetry, analytics, and alerting platform integrated natively into the Qwen Collective control plane. It provides end-to-end operational visibility across the distributed AI collective:
- **Windows Controller (`controller-local`)**: Core FastAPI application, coordinator, scheduler, time-series storage, local resource sampler, and Observatory dashboard.
- **Linux VPS (`qwen-vps`)**: Authenticated Linode Ubuntu worker executing sandboxed tools, file operations, web retrieval, and scheduled jobs.
- **Linux Laptop (`qwen-laptop`)**: Authenticated Ubuntu inference node running local Ollama, serving `qwen2.5:1.5b` and `llama3.2:1b`.
- **Windows Companion Worker (`desktop-worker-01`)**: Authenticated compute and local inference worker running `qwen2.5:1.5b`.
- **Legacy Record Isolation (`node-d35568f5`)**: Historical record strictly excluded from physical capacity aggregates to prevent double-counting.

### Core Design Principles
1. **Authoritative, Real Telemetry Only**: Every displayed metric originates from an authenticated, identifiable hardware or engine sensor, includes an ISO UTC timestamp, and has a documented collection method. No simulated values or fabricated zeros are rendered.
2. **Unsupported Sensor Transparency**: When a hardware sensor (such as CPU temperature on a virtualized VPS or dedicated GPU on an integrated laptop) is not present, the system explicitly reports **Not supported** or **Unavailable** rather than misleading zero readings.
3. **Capacity-Weighted Cluster Aggregation**: Cluster aggregate CPU and RAM are calculated with defensible capacity weighting across online physical nodes:
   $$\text{Cluster CPU \%} = \frac{\sum_{i \in \text{Online}} (\text{CPU}_i \times \text{Cores}_i)}{\sum_{i \in \text{Online}} \text{Cores}_i}$$
   $$\text{Cluster RAM \%} = \frac{\sum_{i \in \text{Online}} \text{RAM\_used}_i}{\sum_{i \in \text{Online}} \text{RAM\_total}_i} \times 100$$
   Physical RAM and OS swap partitions are tracked separately and are never combined into a single ambiguous percentage.
4. **Zero New Public Ports**: Workers push metrics over established outbound authenticated HTTP transport. No open listening ports are introduced on Linux worker machines.
5. **Non-Interfering Overhead**: Bounded timeouts, non-blocking telemetry reporting, and lightweight sampling ensure monitoring never starves inference or task leases.

---

## 2. High-Level Architecture Diagram

```text
+----------------------------------------------------------------------------------------------------+
|                                    WINDOWS CONTROLLER (Localhost)                                  |
|                                                                                                    |
|  +--------------------+     +----------------------------------+     +--------------------------+  |
|  | Controller Local   |     |    Worker Ingestion Endpoint     |     |  Inference Accounting    |  |
|  | Hardware Sampler   |     |  (POST /api/v1/worker/telemetry) |     |  (Authoritative Ollama)  |  |
|  | (psapi, kernel32)  |     |  (POST /api/v1/worker/heartbeat) |     |                          |  |
|  +---------+----------+     +-----------------+----------------+     +------------+-------------+  |
|            |                                  |                                   |                |
|            +----------------------------------+-----------------------------------+                |
|                                               |                                                    |
|                                               v                                                    |
|                         +-----------------------------------------------+                          |
|                         |            SQLite Persistence Engine          |                          |
|                         |  - telemetry_samples (Raw 7-day retention)    |                          |
|                         |  - telemetry_rollups_minute (30-day)          |                          |
|                         |  - telemetry_rollups_hour (1-year)            |                          |
|                         |  - inference_telemetry (Engine counters)      |                          |
|                         |  - observatory_alerts & rules                 |                          |
|                         +---------------------+-------------------------+                          |
|                                               |                                                    |
|            +----------------------------------+-----------------------------------+                |
|            |                                  |                                   |                |
|            v                                  v                                   v                |
|  +--------------------+     +----------------------------------+     +--------------------------+  |
|  | Analytics Engine   |     | Alerting Engine (Hysteresis)     |     | System Insights Engine   |  |
|  | Capacity weighting |     | Firing / Acknowledged / Resolved |     | Deterministic diagnostics|  |
|  +---------+----------+     +-----------------+----------------+     +------------+-------------+  |
|            |                                  |                                   |                |
|            +----------------------------------+-----------------------------------+                |
|                                               |                                                    |
|                                               v                                                    |
|                         +-----------------------------------------------+                          |
|                         |            Observatory Web & REST API         |                          |
|                         |      (/observatory & /api/v1/observatory/*)   |                          |
|                         |  [Overview] [Machines] [Models] [Tasks]       |                          |
|                         |  [Errors & Alerts] [History & Analytics]      |                          |
|                         +-----------------------------------------------+                          |
+----------------------------------------------------------------------------------------------------+
           ^                                                              ^
           | Bearer Token Authenticated Pull/Push                         | Bearer Token
           |                                                              |
+----------+---------------+                                   +----------+---------------+
|     LINUX VPS WORKER     |                                   |   LINUX LAPTOP WORKER    |
|       (`qwen-vps`)       |                                   |      (`qwen-laptop`)     |
|                          |                                   |                          |
|  +--------------------+  |                                   |  +--------------------+  |
|  | Hardware Collector |  |                                   |  | Hardware Collector |  |
|  | /proc/stat         |  |                                   |  | /proc/stat, /sys/  |  |
|  | /proc/meminfo      |  |                                   |  | thermal & battery  |  |
|  | /proc/diskstats    |  |                                   |  | Ollama 1.5B & 1B   |  |
|  +--------------------+  |                                   |  +--------------------+  |
+--------------------------+                                   +--------------------------+
```

---

## 3. Component Breakdown

### 3.1 Controller Telemetry Collector (`app/services/observatory/collector.py`)
- **Execution Environment**: Runs natively inside the Windows controller process on `controller-local`.
- **Collection Techniques**:
  - Memory: Windows `GlobalMemoryStatusEx` via `ctypes` extracting physical RAM total/avail/used and paging file swap total/used.
  - CPU: Windows `GetSystemTimes` via `ctypes` calculating kernel vs user ticks between sampling intervals.
  - Disk: `shutil.disk_usage` querying controller storage volume headroom.
  - Power: Windows `GetSystemPowerStatus` detecting AC power connection and battery status.
  - Process RSS: Windows `K32GetProcessMemoryInfo` measuring memory footprint of the controller FastAPI service.
  - GPU: Non-blocking probe with `nvidia-smi` structured CSV query; detects discrete GPU name, utilization, VRAM usage, temperature, and power draw.
- **Sampling Frequency**: Runs every 5.0 seconds in the background thread.

### 3.2 Worker Telemetry Collectors (`vps_agent/capabilities.py` & `vps_agent/agent.py`)
- **Execution Environment**: Runs inside the Python worker daemon on Linux VPS, Linux laptop, and companion desktop.
- **Collection Techniques**:
  - Linux Memory & Swap: Parses `/proc/meminfo` for `MemTotal`, `MemAvailable`, `SwapTotal`, and `SwapFree`.
  - Linux CPU: Reads `os.getloadavg()` and `/proc/stat`.
  - Linux Thermal: Discovers `/sys/class/thermal/thermal_zone*/temp` millidegrees readings.
  - Linux Battery & AC Power: Reads `/sys/class/power_supply/` for battery percentage and AC online adapter state.
  - Linux Process Resident Memory: Reads resident pages from `/proc/self/statm`.
  - Model Availability: Probes local Ollama instance tags at `http://127.0.0.1:11434/api/tags`.
- **Transmission Mechanism**: Pushes telemetry via authenticated HTTP payload in `POST /api/v1/worker/telemetry` or piggybacked inside the 15-second heartbeat in `POST /api/v1/worker/heartbeat`.

### 3.3 Storage Layer (`app/services/observatory/storage.py`)
- **Database Engine**: Native SQLite database (`qwen_collective.db`).
- **Table Structure**:
  - `telemetry_samples`: Raw high-frequency node samples (CPU, RAM, swap, disk, network, power, temp, GPU, RSS, latency).
  - `telemetry_rollups_minute`: Consolidated 1-minute buckets (avg/min/max) for responsive 30-day charting.
  - `telemetry_rollups_hour`: Consolidated 1-hour buckets for 1-year historical analytics.
  - `inference_telemetry`: Request-level record of prompt tokens, generated tokens, total tokens, evaluation duration, load duration, and tokens per second.
  - `observatory_alerts`: Append-only and state-managed active and historical alerts.
  - `observatory_alert_rules`: Configurable alert thresholds with hysteresis and severity settings.

### 3.4 Analytics & Diagnostic Engine (`app/services/observatory/analytics.py`)
- **Cluster Aggregation**: Dynamically aggregates online nodes using capacity weighting while strictly isolating legacy records.
- **Model Performance & Comparison**: Summarizes Ollama execution counters; produces side-by-side comparison tables (Qwen vs. Llama) contrasting throughput, latency, and load times.
- **Token Accounting**: Aggregates token volume today, last 7 days, last 30 days, and lifetime; attributes usage to models, nodes, projects, and tasks; clarifies zero external billing fees for local execution.
- **System Insights**: Generates deterministic recommendations from telemetry observations (e.g. swap thrashing on low-memory nodes, thermal throttling, storage headroom constraints).

### 3.5 Alerting Engine (`app/services/observatory/alerting.py`)
- **Hysteresis Evaluation**: Enforces configured duration thresholds before triggering alerts (e.g., CPU must exceed 90% for >= 30 seconds to prevent alert storms from transient spikes).
- **Cooldown & Deduplication**: Prevents alert spam by enforcing cooldown periods on resolved alerts.
- **State Management**: Lifecycle tracking through `firing`, `acknowledged` (with user attribution), and `resolved` (auto-resolution when metrics normalize).

---

## 4. Observatory Control Room Layout

The System Observatory dashboard (`/observatory`) is structured into six dedicated operational tabs:
1. **Overview**: Executive cluster health cards, capacity-weighted CPU & RAM, live machine health status bars, and cluster resource balance.
2. **Machines**: Machine selector with deep-dive hardware cards (CPU, Memory, GPU, Storage, Power/Thermal, OS/Worker Service) and live Canvas trend charts.
3. **Models & Tokens**: Side-by-side model comparison (Qwen vs Llama), per-model metrics table, token accounting breakdown, and private compute cost accounting.
4. **Tasks & Projects**: Worker task execution efficiency table (successes, errors, timeouts, useful-time ratio) and Project Orchestrator metrics.
5. **Errors & Alerts**: Active alert banner with one-click acknowledgment, deterministic system insights and recommendations, and configured alert rules table.
6. **History & Analytics**: Time-range and resolution selectors (Raw, 1-minute, 1-hour), historical trend charts, and CSV export buttons.
