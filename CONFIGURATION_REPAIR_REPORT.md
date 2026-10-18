# Qwen Collective — Configuration Defect & Repair Report

## 1. Problem Statement

Execution of the pre-flight worker configuration validation command on `qwen-vps`:

```bash
python -m vps_agent --config /etc/qwen-collective/worker.env --validate
```

Failed with the fatal exception:

```text
Permission denied: '/opt/qwen-collective/source/worker_data'
```

Despite `/etc/qwen-collective/worker.env` explicitly specifying:

```bash
QC_WORKER_DATA_DIR=/opt/qwen-collective/data
```

---

## 2. Root Cause Analysis (7 Investigative Inquiries)

### 2.1 When and how `--config` is parsed
- **Finding**: `--config <path>` was parsed via `argparse` in `vps_agent/__main__.py` and passed to `load_config(config_file=Path(args.config))`.
- **Defect**: While the command-line argument was parsed correctly, `load_config()` relied on `python-dotenv`'s `load_dotenv()` function to read the file into `os.environ`.

### 2.2 Whether environment variables are loaded before `AgentConfig` is constructed
- **Finding**: In the previous revision, `requirements.txt` omitted `python-dotenv`. On a clean system or venv lacking `python-dotenv`, the `try: from dotenv import load_dotenv` block failed silently via `except ImportError: pass`.
- **Consequence**: `worker.env` was **never parsed or loaded into the process environment**. When `AgentConfig()` was instantiated, `os.getenv("QC_WORKER_DATA_DIR")` returned `None`.

### 2.3 Whether the process environment overrides values in the configuration file
- **Finding**: In the previous design, `AgentConfig` fields evaluated `os.getenv()` at instantiation time. If the environment file failed to load, ambient environment variables took precedence, and if none were set, default fallbacks were applied.

### 2.4 Whether `load_config()` creates directories or performs filesystem operations before resolving configured paths
- **Finding**: In the previous revision, `load_config()` unconditionally executed:
  ```python
  config = AgentConfig()
  config.data_dir.mkdir(parents=True, exist_ok=True)
  if os.name != "nt":
      os.chmod(config.data_dir, 0o700)
  ```
- **Consequence**: Because `QC_WORKER_DATA_DIR` was never loaded, `data_dir` defaulted to `./worker_data`. Since the daemon was invoked from `/opt/qwen-collective/source` (which is owned by `root:root` mode 0755), calling `config.data_dir.mkdir()` as the unprivileged `qwen-worker` user immediately triggered `PermissionError: [Errno 13] Permission denied: '/opt/qwen-collective/source/worker_data'`.
- **Architectural Flaw**: Filesystem mutations occurred **during configuration loading**, prior to validation or daemon startup.

### 2.5 Whether systemd's `EnvironmentFile=` behavior differs from manual CLI invocation
- **Finding**: Under systemd, `EnvironmentFile=/etc/qwen-collective/worker.env` is parsed directly by PID 1 and injected into the process environment before `execve()`. In contrast, manual CLI invocation `python -m vps_agent --config ...` relies solely on the application code to parse the file. When `load_dotenv` failed, manual CLI broke completely while systemd would have partially masked the Python parsing bug.

### 2.6 Whether the daemon correctly resolves relative paths
- **Finding**: `./worker_data` resolved relative to the process current working directory (`CWD`), which in systemd or manual execution is `/opt/qwen-collective/source`. This violated the architectural requirement that the worker must never attempt to store runtime state inside the read-only application repository.

### 2.7 Whether configuration validation has unintended filesystem side effects
- **Finding**: Running `--validate` called `load_config()`, which attempted directory creation and permission modification. A read-only validation check must never mutate the host filesystem.

---

## 3. Engineering Remediation

### 3.1 Zero-Dependency Environment File Parser
Implemented `parse_env_file(path: Path) -> dict[str, str]` in `vps_agent/config.py`:
- Parses standard `KEY=VALUE` pairs without requiring external third-party libraries.
- Handles comments (`#`), inline whitespace, single/double quotes, and `export ` prefixes.
- Operates purely in-memory with zero filesystem mutations.

### 3.2 Decoupled Configuration Loading from Filesystem Mutation
- Removed all `mkdir()` and `chmod()` calls from `load_config()`.
- Created `ensure_runtime_directories(config: AgentConfig)`:
  - Invoked **strictly during daemon execution** in `run_daemon()`.
  - Never invoked during static configuration validation (`--validate`).

### 3.3 Safe Fallback & Source Tree Guard
- Implemented `get_default_data_dir() -> Path`:
  - Priority 1: `QC_WORKER_DATA_DIR` from environment/file.
  - Priority 2 (Linux): `/opt/qwen-collective/data` if on Linux or directory exists.
  - Priority 3 (Windows): `~/.qwen-collective/data`.
  - **Never defaults to `./worker_data` inside the source checkout**.
- Added strict source repository validation guard in `validate_config()`:
  - Rejects any configuration where `data_dir` resides within `source_dir` or its subdirectories:
  ```python
  if resolved_data == source_dir or source_dir in resolved_data.parents:
      return False, f"Insecure data directory: QC_WORKER_DATA_DIR '{resolved_data}' cannot be inside the source repository."
  ```

### 3.4 Support for Validation with Pending Credentials
- Updated `validate_config(config, allow_pending_credentials=False)`:
  - When `--validate` is passed via CLI, `allow_pending_credentials=True` allows the administrator to verify path validity, service settings, and syntax before credentials have been provisioned.
  - Normal daemon execution (`run_daemon`) strictly enforces `allow_pending_credentials=False` and rejects startup without valid tokens.

### 3.5 Dependency Management
- Added `python-dotenv>=1.0.0` and `requests>=2.31.0` to `requirements.txt`.

---

## 4. Verification & Regression Testing

The following automated regression tests were added in `tests/test_vps_daemon.py`:

1. `test_parse_env_file_zero_dependency`: Verifies parsing of quotes, exports, and comments without external libraries.
2. `test_load_config_precedence_and_no_side_effects`: Asserts that `load_config()` does not create directories and respects file overrides.
3. `test_validate_config_rejects_data_dir_inside_source`: Verifies that placing runtime directories in the git checkout is rejected.
4. `test_validate_config_allows_pending_credentials`: Asserts that `--validate` succeeds without tokens when configuration is structurally valid.
5. `test_daemon_cli_validate_flag`: Tests end-to-end CLI exit codes (0 for valid/pending, 1 for invalid URL, 1 for insecure paths).
6. `test_ensure_runtime_directories`: Verifies directory creation occurs on startup with proper permissions.

All 16 tests in `tests/test_vps_daemon.py` and all 314 tests across the full suite passed with 0 failures.
