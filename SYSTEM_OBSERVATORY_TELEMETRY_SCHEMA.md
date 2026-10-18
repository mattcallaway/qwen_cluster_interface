# Qwen Collective — System Observatory Telemetry Schema
**Data Dictionary, Units, Formats & Persistence Specification**

This document specifies the exact schemas, units, collection methods, and constraints for all metrics ingested, stored, and displayed by the System Observatory.

---

## 1. Physical Hardware Metrics (`telemetry_samples`)

| Field Name | Type | Unit | Collection Method | Description & Constraints |
|---|---|---|---|---|
| `id` | INTEGER | None | SQLite Autoincrement | Unique sample primary key. |
| `node_id` | TEXT | None | Authenticated Token Binding | Identifier of the reporting physical node (e.g. `controller-local`, `qwen-vps`, `qwen-laptop`). |
| `timestamp` | TEXT | ISO-8601 UTC | Client/Server UTC Clock | Sample collection timestamp (e.g. `2026-10-18T10:15:30.123456+00:00`). |
| `cpu_percent` | REAL | Percent (%) | Windows `GetSystemTimes` / Linux `/proc/stat` | Overall node CPU utilization ($0.0 \le \text{val} \le 100.0$). |
| `per_core_json` | TEXT | JSON Array (%) | Linux `/proc/stat` per-cpu / Windows | JSON list of per-core utilization percentages. |
| `cpu_freq_mhz` | REAL | Megahertz (MHz) | Linux `/proc/cpuinfo` / Windows | Current processor frequency. `None` if unexposed. |
| `load_1m` | REAL | Load Number | Linux `os.getloadavg()` | 1-minute system load average. `None` on Windows. |
| `load_5m` | REAL | Load Number | Linux `os.getloadavg()` | 5-minute system load average. `None` on Windows. |
| `load_15m` | REAL | Load Number | Linux `os.getloadavg()` | 15-minute system load average. `None` on Windows. |
| `ram_total_mb` | INTEGER | Megabytes (MB) | Windows `GlobalMemoryStatusEx` / Linux `/proc/meminfo` | Total physical RAM installed and accessible to OS. |
| `ram_used_mb` | INTEGER | Megabytes (MB) | Total minus Available RAM | Currently allocated physical RAM. |
| `ram_available_mb`| INTEGER | Megabytes (MB) | OS Available Memory metric | Memory immediately available for processes without swapping. |
| `ram_percent` | REAL | Percent (%) | $\frac{\text{Used}}{\text{Total}} \times 100$ | Physical RAM utilization percentage ($0.0 \le \text{val} \le 100.0$). |
| `swap_total_mb` | INTEGER | Megabytes (MB) | Windows Pagefile / Linux `/proc/meminfo` | Configured swap space / paging file capacity. |
| `swap_used_mb` | INTEGER | Megabytes (MB) | Swap total minus free swap | Currently utilized swap space. |
| `swap_percent` | REAL | Percent (%) | $\frac{\text{Swap Used}}{\text{Swap Total}} \times 100$ | Swap utilization percentage. Never combined with RAM %. |
| `disk_total_gb` | REAL | Gigabytes (GB) | `shutil.disk_usage` | Total storage capacity of primary filesystem volume. |
| `disk_used_gb` | REAL | Gigabytes (GB) | Total minus Free Disk | Currently used disk capacity. |
| `disk_free_gb` | REAL | Gigabytes (GB) | `shutil.disk_usage` | Remaining free disk space. |
| `disk_read_mbs` | REAL | MB / sec | Delta of `/proc/diskstats` sectors | Disk read throughput rate. |
| `disk_write_mbs` | REAL | MB / sec | Delta of `/proc/diskstats` sectors | Disk write throughput rate. |
| `disk_iops` | REAL | Operations / sec | Delta of completed I/O requests | Total disk I/O operations per second. |
| `net_rx_kbs` | REAL | KB / sec | Delta of network interface bytes | Inbound network throughput. |
| `net_tx_kbs` | REAL | KB / sec | Delta of network interface bytes | Outbound network throughput. |
| `cpu_temp_c` | REAL | Celsius (°C) | Linux `/sys/class/thermal/` | CPU package temperature. `None` if unsupported (e.g. VPS). |
| `battery_percent` | REAL | Percent (%) | Linux `/sys/class/power_supply/` / Windows | Battery charge level ($0 \le \text{val} \le 100$). `None` on desktop. |
| `is_ac_online` | INTEGER | Flag | Windows `SYSTEM_POWER_STATUS` / Linux | $1 = \text{AC plugged in}, 0 = \text{On Battery}, -1 = \text{Desktop/Unknown}$. |
| `worker_res_mb` | REAL | Megabytes (MB) | Windows `K32GetProcessMemoryInfo` / Linux | Resident set size (RSS) of worker daemon process. |
| `ollama_res_mb` | REAL | Megabytes (MB) | Process RSS probe of `ollama` daemon | Resident memory consumed by the Ollama runtime. |
| `gpu_info_json` | TEXT | JSON Object | `nvidia-smi` CSV query | Contains `{supported, name, utilization_percent, vram_total_mb, vram_used_mb, temperature_c, power_watts}`. |
| `latency_to_controller_ms` | REAL | Milliseconds (ms)| RTT measurement from worker to controller | Network latency to coordinator. $0.0$ for controller loopback. |
| `os_uptime_s` | INTEGER | Seconds (s) | Windows `GetTickCount64` / Linux `/proc/uptime` | System uptime since boot. |
| `worker_service_status` | TEXT | Enum | Process health probe | `running`, `degraded`, `stopped`. |
| `ollama_service_status` | TEXT | Enum | HTTP tag probe | `running`, `unreachable`, `not_installed`. |

---

## 2. Pre-Computed Rollup Schemas

### 2.1 1-Minute Rollups (`telemetry_rollups_minute`)
- **Bucket format**: `YYYY-MM-DDTHH:MM:00`
- **Unique Constraint**: `(node_id, bucket_time)`
- **Retention**: 30 days
- **Fields**:
  - `sample_count` (INTEGER)
  - `cpu_avg`, `cpu_min`, `cpu_max` (REAL)
  - `ram_used_avg`, `ram_used_min`, `ram_used_max` (REAL)
  - `swap_used_avg`, `swap_used_min`, `swap_used_max` (REAL)
  - `temp_avg`, `temp_max` (REAL)
  - `net_rx_avg`, `net_tx_avg` (REAL)
  - `disk_read_avg`, `disk_write_avg` (REAL)
  - `latency_avg` (REAL)

### 2.2 1-Hour Rollups (`telemetry_rollups_hour`)
- **Bucket format**: `YYYY-MM-DDTHH:00:00`
- **Unique Constraint**: `(node_id, bucket_time)`
- **Retention**: 1 year (365 days)
- **Aggregation Source**: Consolidated from `telemetry_rollups_minute` to prevent unbounded query cost over 30+ day windows.

---

## 3. Model Inference Telemetry (`inference_telemetry`)

| Field Name | Type | Unit | Collection Method | Description |
|---|---|---|---|---|
| `id` | TEXT | None | Primary Key | Telemetry record UUID (`inf-tel-...`). |
| `request_id` | TEXT | None | Worker Engine / Caller | Unique inference request correlation ID. |
| `run_id` | TEXT | None | Project Orchestrator | FK to `orchestration_runs(id)` if triggered by an autonomous project. |
| `project_id` | TEXT | None | Project Registry | FK to `projects(id)` if attributed to a project. |
| `task_id` | TEXT | None | Task Registry | FK to `tasks(id)` if leased by a worker. |
| `node_id` | TEXT | None | Worker Token | Physical node hosting the engine (`qwen-laptop`, `desktop-worker-01`). |
| `model` | TEXT | None | Ollama Tag | Model identifier (e.g. `qwen2.5:1.5b`, `llama3.2:1b`). |
| `provider` | TEXT | None | Runtime Engine | Runtime provider (default: `ollama`). |
| `status` | TEXT | Enum | Result Outcome | `success`, `error`, `timeout`. |
| `prompt_tokens` | INTEGER | Tokens | Ollama `prompt_eval_count` | Exact count of input prompt tokens processed. |
| `gen_tokens` | INTEGER | Tokens | Ollama `eval_count` | Exact count of output tokens generated. |
| `total_tokens` | INTEGER | Tokens | Input + Output | Authoritative total token count. |
| `prompt_eval_duration_ms` | REAL | Milliseconds | Ollama `prompt_eval_duration` / 1e6 | Time spent ingesting and evaluating the prompt. |
| `eval_duration_ms` | REAL | Milliseconds | Ollama `eval_duration` / 1e6 | Time spent sampling and generating tokens. |
| `load_duration_ms` | REAL | Milliseconds | Ollama `load_duration` / 1e6 | Time spent loading model weights into memory/VRAM. |
| `total_duration_ms` | REAL | Milliseconds | Ollama `total_duration` / 1e6 | End-to-end execution duration. |
| `tokens_per_second` | REAL | Tokens / sec | $\frac{\text{gen\_tokens}}{\text{eval\_duration\_s}}$ | Actual token generation speed during synthesis. |
| `time_to_first_token_ms` | REAL | Milliseconds | Streaming timer / Load duration | Time elapsed until first token was produced. |
| `error_category` | TEXT | String | Exception classification | Classified error (e.g. `timeout`, `oom`, `unreachable`). |
| `retry_count` | INTEGER | Count | Task attempt number | Number of retry attempts required. |
| `created_at` | TEXT | ISO-8601 UTC | Clock | Request creation timestamp. |
| `completed_at` | TEXT | ISO-8601 UTC | Clock | Completion timestamp. |

---

## 4. Alerting & Rules (`observatory_alerts` & `observatory_alert_rules`)

### 4.1 Alert Rules (`observatory_alert_rules`)
- `id` (TEXT PRIMARY KEY)
- `name` (TEXT UNIQUE): Machine-readable rule key (e.g. `cpu_sustained_high`, `swap_pressure_warning`).
- `category` (TEXT): `cpu`, `memory`, `swap`, `temperature`, `storage`, `worker`, `model`, `controller`.
- `metric_name` (TEXT): Name of the metric evaluated (e.g. `cpu_percent`, `ram_percent`, `heartbeat_age_s`).
- `threshold` (REAL): Numerical trigger threshold.
- `operator` (TEXT): Comparison operator (`>=`, `>`, `<=`, `<`, `==`, `!=`).
- `duration_seconds` (INTEGER): Minimum sustained duration before triggering (hysteresis protection).
- `severity` (TEXT): `info`, `warning`, `critical`.
- `is_enabled` (INTEGER): $1 = \text{enabled}, 0 = \text{disabled}$.

### 4.2 Alert Instances (`observatory_alerts`)
- `id` (TEXT PRIMARY KEY): Unique alert instance UUID (`alt-...`).
- `rule_name` (TEXT): Associated rule name.
- `node_id` (TEXT): Physical node affected (or `cluster`).
- `severity` (TEXT): `info`, `warning`, `critical`.
- `title` (TEXT): Human-readable alert summary.
- `description` (TEXT): Detailed explanation including current value, threshold, and duration.
- `metrics_json` (TEXT): JSON snapshot of metrics at time of triggering.
- `state` (TEXT): `firing`, `acknowledged`, `resolved`.
- `fired_at` (TEXT): Timestamp when hysteresis condition was satisfied.
- `acknowledged_at` (TEXT): Timestamp when acknowledged by operator.
- `acknowledged_by` (TEXT): User ID or operator identity.
- `resolved_at` (TEXT): Timestamp when metric returned to safe range.
- `cooldown_until` (TEXT): Timestamp until which repeated alerts from this rule are suppressed.
