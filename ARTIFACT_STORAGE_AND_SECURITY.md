# Qwen Collective — Artifact Storage & Security Specification

## 1. Overview

The artifact storage subsystem in Qwen Collective manages the durable persistence, integrity verification, safe previewing, and controlled streaming of all project deliverables and intermediate assets. Because artifacts originate from distributed workers (including sandboxed execution tasks on Linux VPS and local Ollama inference on Linux laptops), **all incoming files are treated as untrusted data**.

---

## 2. Storage Architecture & Extensibility

Artifact physical storage is abstracted through the `ArtifactStorageBackend` base class in [`app/services/artifacts/storage.py`](file:///C:/Users/Matt%20C/.gemini/antigravity-ide/scratch/qwen-collective/app/services/artifacts/storage.py).

### Backend Abstraction
```python
class ArtifactStorageBackend(abc.ABC):
    @abc.abstractmethod
    def store_bytes(self, storage_key: str, data: bytes, expected_sha256: Optional[str] = None, max_size_bytes: int = 52428800) -> Dict[str, Any]:
        """Atomically persist bytes and return verified metadata."""
        pass

    @abc.abstractmethod
    def read_bytes(self, storage_key: str) -> bytes: ...

    @abc.abstractmethod
    def exists(self, storage_key: str) -> bool: ...

    @abc.abstractmethod
    def get_real_path(self, storage_key: str) -> Path: ...

    @abc.abstractmethod
    def verify_integrity(self, storage_key: str, expected_sha256: str) -> Tuple[bool, str]: ...

    @abc.abstractmethod
    def delete(self, storage_key: str) -> bool: ...
```

### Reference Implementation: `LocalFilesystemStorageBackend`
In the controller environment, artifacts are stored under the protected directory defined by `settings.artifacts_dir` (`data/artifacts/`).

**Key Storage Guarantees:**
1. **Atomic Writes**: Incoming bytes are written to a temporary file in the same filesystem root (`tempfile.NamedTemporaryFile(dir=target_dir, delete=False)`), flushed with `os.fsync()`, and moved via an atomic rename (`os.replace()`). This guarantees that concurrent readers or system power interruptions never encounter corrupted or partially written files.
2. **Deterministic Hashing**: The controller independently calculates the SHA-256 hash byte-by-byte during write. If an `expected_sha256` is provided (e.g. by worker manifest), a mismatch causes an immediate `ValueError` and the temporary file is deleted.
3. **Partitioned Subdirectories**: Storage keys are partitioned hierarchically (`data/artifacts/YYYY/MM/art-<id>/<filename>`), preventing directory entry saturation on high-throughput filesystems.

---

## 3. Threat Model & Security Controls

| Threat Category | Potential Attack Vector | Defense Mechanism in Qwen Collective |
|---|---|---|
| **Path Traversal** | Filenames with `../`, `..\\`, or absolute paths (`C:\Windows\...`) | `sanitize_filename()` strips all path components using `Path(name).name` and regex whitelist `[^A-Za-z0-9._\-]`. `_resolve_safe_path()` checks `cand.is_relative_to(allowed_roots)` and raises `PermissionError`. |
| **System Resource Leak** | Requesting `qwen_collective.db`, `.env`, or `secret_key` | `_resolve_safe_path()` unconditionally checks for `.db`, `.env`, `secret_key`, and `qwen_collective` strings, immediately denying access regardless of existence. |
| **Symlink Escapes** | Creating a symlink on Linux pointing to `/etc/passwd` | Storage resolution resolves all paths via `.resolve()` and verifies that the canonical path resides strictly inside `allowed_roots`. |
| **Cross-Project Leakage** | User or task in Project B requesting private artifact of Project A | Server-side authorization check in `check_project_access()`: requires active grant in `artifact_access_grants` or project ownership. UI visibility alone does not grant access. |
| **Malicious Execution** | Uploaded HTML with `<script>`, SVG with XSS, executable binaries | Safe preview generator renders text/code safely without script execution. Downloads force `Content-Disposition: attachment` with `X-Content-Type-Options: nosniff`. |
| **Denial of Service (Storage Exhaustion)** | Huge files or zip bombs uploaded by rogue task | `DEFAULT_MAX_ARTIFACT_SIZE` strictly enforced at 50 MB per file. Upload stream checks byte length and rejects oversized payloads before persistence. |
| **Worker Over-Privilege** | Worker enumerating and downloading all cluster artifacts | Dedicated least-privilege endpoint `GET /api/v1/worker/tasks/{task_id}/artifacts/{artifact_id}` verifies that the requested artifact is an authorized input in `task.input_data_json`. |

---

## 4. Safe Preview Architecture

Previews are generated server-side by `ArtifactService.get_safe_preview()`:
- **Plain Text & Markdown**: Decoded with UTF-8 (`errors="replace"`), truncated safely to 100,000 characters, rendered with safe markdown parser.
- **Code & Config**: (Python, JavaScript, YAML, JSON, SQL, Shell, HTML source) Syntax-highlighted and rendered inside `<pre><code>` blocks with all HTML tags escaped. Never evaluated as live code.
- **JSON**: Formatted with standard 2-space indentation and validated structure.
- **CSV**: Parsed with Python's standard `csv.reader`, rendered into a clean table structure (up to first 100 rows).
- **PDF**: Served with strict `application/pdf` headers for native browser sandboxed viewing.
- **Images**: (PNG, JPEG, GIF, WebP, SVG) Safely served as image media.
- **Unsupported Binaries**: Generates an informative placeholder card displaying file size, SHA-256 fingerprint, and a one-click Download button.

---

## 5. Tailscale & Remote Access Integration

When accessing the Qwen Collective interface remotely over Tailscale HTTPS:
1. Controller endpoints enforce session-token cookie authentication.
2. File streaming utilizes chunked transfer encoding (`aiofiles` / `StreamingResponse`) with strict cache control (`no-cache, no-store, must-revalidate`).
3. Tailscale wireguard encryption ensures transport security between controller and all remote workers and client browsers.
