"""
Outbound Pull-Based VPS Worker Daemon for Qwen Collective (Milestone 2).
Enforces:
- Self-registration with controller
- Periodic health heartbeats
- Pull-based task leasing
- Sandboxed task execution
- Offline buffering during network loss and automated catchup
"""

import logging
import threading
import time
from typing import Any, Dict, Optional
try:
    import requests
except ImportError:
    import httpx
    class _RequestsCompat:
        Session = httpx.Client
        get = httpx.get
        post = httpx.post
        delete = httpx.delete
        put = httpx.put
    requests = _RequestsCompat()

from vps_agent.buffer import OfflineBuffer
from vps_agent.capabilities import collect_live_telemetry, discover_local_hardware
from vps_agent.config import AgentConfig, load_config, save_worker_token
from vps_agent.executor import execute_task

logger = logging.getLogger("qwen_collective.worker_agent")


def _run_lease_heartbeats(agent: "VPSWorkerAgent", lease_id: str, stop_event: threading.Event) -> None:
    """Maintain per-task lease renewal and node heartbeat in background during execution."""
    interval = min(15, max(5, agent.config.heartbeat_interval))
    while not stop_event.wait(interval):
        # 1. Renew per-task lease heartbeat
        renew_url = f"{agent.config.controller_url}/api/v1/worker/tasks/{lease_id}/heartbeat"
        try:
            r = agent.session.post(renew_url, headers=agent._headers(), timeout=5)
            if r.status_code == 200:
                logger.debug("Renewed heartbeat for lease %s", lease_id)
            elif r.status_code in (403, 404):
                logger.warning("Lease %s heartbeat returned %s; task lease may be stale or revoked", lease_id, r.status_code)
        except Exception as e:
            logger.debug("Lease heartbeat ping failed: %s", e)

        # 2. Also keep node health fresh
        try:
            agent.send_heartbeat()
        except Exception:
            pass


class VPSWorkerAgent:
    def __init__(self, config: Optional[AgentConfig] = None, buffer: Optional[OfflineBuffer] = None):
        self.config = config or load_config()
        self.buffer = buffer if buffer is not None else OfflineBuffer(self.config.buffer_db_path)
        self.session = requests.Session()
        self.token = self.config.worker_token
        self.is_registered = False
        self.last_heartbeat = 0.0
        self.auth_failed = False
        self.enrollment_failed = False
        self.consecutive_auth_failures = 0

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.config.enrollment_token:
            headers["X-Enrollment-Token"] = self.config.enrollment_token
        return headers

    def register(self) -> bool:
        """Register the worker node and obtain/verify authentication token."""
        if self.enrollment_failed and not self.token:
            logger.warning(
                "Enrollment token was rejected (HTTP 403); aborting registration attempt until a fresh token is configured."
            )
            return False

        url = f"{self.config.controller_url}/api/v1/worker/register"
        hardware_info = discover_local_hardware()
        payload = {
            "node_id": self.config.worker_id,
            "name": self.config.worker_name,
            "role": self.config.worker_role,
            "enrollment_token": self.config.enrollment_token if not self.token else None,
            "hostname": hardware_info["hostname"],
            "os_name": hardware_info["os_name"],
            "os_version": hardware_info["os_version"],
            "architecture": hardware_info["architecture"],
            "cpu_count": hardware_info["cpu_count"],
            "ram_total_mb": hardware_info["ram_total_mb"],
            "ram_available_mb": hardware_info["ram_available_mb"],
            "disk_total_gb": hardware_info["disk_total_gb"],
            "disk_free_gb": hardware_info["disk_free_gb"],
            "installed_runtimes": hardware_info["installed_runtimes"],
            "declared_capabilities": hardware_info["declared_capabilities"],
            "available_models": hardware_info.get("available_models", []),
            "agent_version": hardware_info["agent_version"],
        }

        try:
            resp = self.session.post(url, json=payload, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if "worker_token" in data:
                    self.token = data["worker_token"]
                    self.config.worker_token = self.token
                    save_worker_token(self.config, self.token)
                    # Clear single-use enrollment token from memory and scrub config file
                    self.config.enrollment_token = ""
                    from vps_agent.config import scrub_enrollment_token
                    scrub_enrollment_token()
                    logger.info("Provisioned and securely persisted new worker token for node %s", self.config.worker_id)
                self.is_registered = True
                self.auth_failed = False
                self.enrollment_failed = False
                self.consecutive_auth_failures = 0
                logger.info("Successfully registered worker node %s with controller", self.config.worker_id)
                return True
            elif resp.status_code == 401:
                self.is_registered = False
                self.auth_failed = True
                self.consecutive_auth_failures += 1
                logger.error("Controller rejected registration credentials (HTTP 401: unauthorized).")
                return False
            elif resp.status_code == 403:
                self.is_registered = False
                self.auth_failed = True
                self.consecutive_auth_failures += 1
                if self.config.enrollment_token and not self.token:
                    self.enrollment_failed = True
                    self.config.enrollment_token = ""
                    logger.error(
                        "Controller rejected enrollment token (HTTP 403: %s). Discarding single-use enrollment token.",
                        resp.text[:200],
                    )
                else:
                    logger.error("Controller rejected registration (HTTP 403: %s).", resp.text[:200])
                return False
            else:
                logger.error("Registration failed with status %s: %s", resp.status_code, resp.text[:200])
                return False
        except Exception as e:
            logger.warning("Could not contact controller for registration: %s", e)
            return False

    def send_heartbeat(self) -> bool:
        """Send health heartbeat and real hardware telemetry to the controller."""
        if not self.is_registered or not self.token:
            return False

        url = f"{self.config.controller_url}/api/v1/worker/heartbeat"
        telemetry = collect_live_telemetry()
        payload = {
            "node_id": self.config.worker_id,
            "cpu_percent": telemetry.get("cpu_percent"),
            "ram_total_mb": telemetry.get("ram_total_mb"),
            "ram_used_mb": telemetry.get("ram_used_mb"),
            "ram_available_mb": telemetry.get("ram_available_mb"),
            "swap_total_mb": telemetry.get("swap_total_mb"),
            "swap_used_mb": telemetry.get("swap_used_mb"),
            "swap_percent": telemetry.get("swap_percent"),
            "disk_free_gb": telemetry.get("disk_free_gb"),
            "cpu_temp_c": telemetry.get("cpu_temp_c"),
            "battery_percent": telemetry.get("battery_percent"),
            "is_ac_online": telemetry.get("is_ac_online", -1),
            "worker_res_mb": telemetry.get("worker_res_mb"),
            "running_jobs_count": 0,
            "available_models": telemetry.get("available_models", []),
            "gpu": telemetry.get("gpu"),
        }

        try:
            resp = self.session.post(url, json=payload, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                self.last_heartbeat = time.time()
                return True
            elif resp.status_code in (401, 403):
                logger.warning("Heartbeat returned HTTP %s; worker token invalid or revoked. Marking unregistered.", resp.status_code)
                self.is_registered = False
                self.auth_failed = True
                self.consecutive_auth_failures += 1
                return False
            return False
        except Exception as e:
            logger.debug("Heartbeat ping failed: %s", e)
            return False

    def send_telemetry(self) -> bool:
        """Push full structured hardware telemetry to the dedicated observatory endpoint."""
        if not self.is_registered or not self.token:
            return False

        url = f"{self.config.controller_url}/api/v1/worker/telemetry"
        telemetry = collect_live_telemetry()
        payload = {
            "node_id": self.config.worker_id,
            "cpu_percent": telemetry.get("cpu_percent"),
            "load_1m": telemetry.get("load_1m"),
            "load_5m": telemetry.get("load_5m"),
            "load_15m": telemetry.get("load_15m"),
            "ram_total_mb": telemetry.get("ram_total_mb"),
            "ram_used_mb": telemetry.get("ram_used_mb"),
            "ram_available_mb": telemetry.get("ram_available_mb"),
            "ram_percent": telemetry.get("ram_percent"),
            "swap_total_mb": telemetry.get("swap_total_mb"),
            "swap_used_mb": telemetry.get("swap_used_mb"),
            "swap_percent": telemetry.get("swap_percent"),
            "disk_total_gb": telemetry.get("disk_total_gb"),
            "disk_used_gb": telemetry.get("disk_used_gb"),
            "disk_free_gb": telemetry.get("disk_free_gb"),
            "cpu_temp_c": telemetry.get("cpu_temp_c"),
            "battery_percent": telemetry.get("battery_percent"),
            "is_ac_online": telemetry.get("is_ac_online", -1),
            "worker_res_mb": telemetry.get("worker_res_mb"),
            "gpu": telemetry.get("gpu"),
        }

        try:
            resp = self.session.post(url, json=payload, headers=self._headers(), timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.debug("Telemetry push failed: %s", e)
            return False

    def flush_offline_buffer(self) -> int:
        """Drain buffered submissions to the controller according to explicit acknowledgment semantics.

        Semantics:
        - 200 with recorded / already_processed / reconciled_conflict: remove from buffer (acknowledged).
        - 400 / 422: invalid payload; quarantine with error rather than blocking FIFO delivery queue.
        - 401 / 403: auth failure; suspend retries until re-registered.
        - 5xx: controller unavailable; retain and retry later.
        - Network drop: retain and retry later.
        - Unknown 200 ack: retain for review.
        """
        if not self.is_registered or not self.token:
            logger.debug("Skipping buffer flush: worker not registered and authenticated")
            return 0

        pending = self.buffer.get_pending_submissions(limit=10)
        flushed_count = 0
        for item in pending:
            task_id = item["task_id"]
            url = f"{self.config.controller_url}/api/v1/worker/tasks/{task_id}/submit"
            try:
                resp = self.session.post(url, json=item["payload"], headers=self._headers(), timeout=10)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except Exception:
                        data = {}
                    ack_status = data.get("status")
                    if ack_status in ("recorded", "already_processed", "reconciled_conflict"):
                        self.buffer.remove_submission(item["id"])
                        flushed_count += 1
                        logger.info("Cleared buffer submission #%s for task %s on terminal ack '%s'", item["id"], task_id, ack_status)
                    else:
                        logger.warning("Unrecognized 200 ack status '%s' for task %s; retaining for review", ack_status, task_id)
                        break
                elif resp.status_code in (401, 403):
                    logger.warning("Authentication rejected (HTTP %s) while flushing buffer; suspending retries", resp.status_code)
                    self.is_registered = False
                    self.auth_failed = True
                    self.consecutive_auth_failures += 1
                    break
                elif resp.status_code in (400, 422):
                    logger.error("Controller rejected buffer submission #%s as invalid payload (HTTP %s); quarantining", item["id"], resp.status_code)
                    self.buffer.quarantine_submission(item["id"], reason=f"HTTP_{resp.status_code}:{resp.text[:200]}")
                elif resp.status_code >= 500:
                    logger.warning("Controller 5xx error (%s) flushing task %s; retaining for retry", resp.status_code, task_id)
                    break
                else:
                    logger.warning("Unexpected status %s flushing task %s; retaining for review", resp.status_code, task_id)
                    break
            except Exception as e:
                logger.debug("Could not flush buffered submission for task %s (retaining for retry): %s", task_id, e)
                break
        return flushed_count

    def poll_and_execute_task(self) -> Optional[Dict[str, Any]]:
        """Lease one task from controller, execute it, and submit results."""
        if not self.is_registered or not self.token:
            logger.warning("Task polling denied: worker is not registered and authenticated")
            return None

        url = f"{self.config.controller_url}/api/v1/worker/tasks/lease"
        hardware_info = discover_local_hardware()
        categories = [
            "test_task", "health_check", "diagnostic", "capability_probe", "ping",
            "python_execution", "approved_shell", "file_processing", "monitoring",
        ]
        if "model_inference" in hardware_info.get("declared_capabilities", []):
            categories.extend(["inference", "model_inference", "local_inference"])
        payload = {
            "worker_id": self.config.worker_id,
            "supported_categories": categories,
            "lease_duration_seconds": self.config.lease_duration,
            "available_models": hardware_info.get("available_models", []),
            "available_ram_mb": hardware_info.get("ram_available_mb"),
        }

        try:
            resp = self.session.post(url, json=payload, headers=self._headers(), timeout=10)
            if resp.status_code in (401, 403):
                logger.warning("Task lease poll returned HTTP %s; worker token invalid or revoked. Marking unregistered.", resp.status_code)
                self.is_registered = False
                self.auth_failed = True
                self.consecutive_auth_failures += 1
                return None
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("status") != "leased" or "task" not in data:
                return None
            task = data["task"]
        except Exception as e:
            logger.debug("Could not poll controller for tasks: %s", e)
            return None

        task_id = task["task_id"]
        lease_id = task["lease_id"]
        idempotency_key = task["idempotency_key"]
        logger.info("Claimed lease for task %s (Lease: %s)", task_id, lease_id)

        # Execute task with independent concurrent lease/node heartbeating
        stop_heartbeat = threading.Event()
        hb_thread = threading.Thread(
            target=_run_lease_heartbeats,
            args=(self, lease_id, stop_heartbeat),
            daemon=True,
        )
        hb_thread.start()

        try:
            exec_outcome = execute_task(task, timeout_seconds=self.config.default_task_timeout)
        finally:
            stop_heartbeat.set()
            hb_thread.join(timeout=2.0)

        # Prepare submission payload
        submission = {
            "task_id": task_id,
            "lease_id": lease_id,
            "idempotency_key": idempotency_key,
            "status": exec_outcome["status"],
            "exit_code": exec_outcome["exit_code"],
            "runtime_seconds": exec_outcome["runtime_seconds"],
            "stdout_summary": exec_outcome["stdout_summary"],
            "stderr_summary": exec_outcome["stderr_summary"],
            "error_category": exec_outcome.get("error_category"),
            "result_payload": exec_outcome.get("result_payload", {}),
            "deliverable_ids": [],
            "artifacts": exec_outcome.get("result_payload", {}).get("artifacts", []),
        }

        # Submit to controller or buffer offline
        submit_url = f"{self.config.controller_url}/api/v1/worker/tasks/{task_id}/submit"
        submitted = False
        try:
            submit_resp = self.session.post(submit_url, json=submission, headers=self._headers(), timeout=10)
            if submit_resp.status_code == 200:
                try:
                    data = submit_resp.json()
                except Exception:
                    data = {}
                ack_status = data.get("status")
                if ack_status in ("recorded", "already_processed", "reconciled_conflict"):
                    submitted = True
                    logger.info("Submitted task %s results to controller (ack: %s)", task_id, ack_status)
                else:
                    logger.warning("Unexpected 200 ack status '%s' for task %s; buffering for review", ack_status, task_id)
            elif submit_resp.status_code in (401, 403):
                logger.warning(
                    "Task submission returned HTTP %s (auth failure); buffering locally and marking unregistered.",
                    submit_resp.status_code,
                )
                self.is_registered = False
                self.auth_failed = True
                self.consecutive_auth_failures += 1
                self.buffer.buffer_submission(
                    task_id=task_id,
                    lease_id=lease_id,
                    idempotency_key=idempotency_key,
                    submission_payload=submission,
                )
                return exec_outcome
            elif submit_resp.status_code in (400, 422):
                logger.error("Controller rejected task %s submission as invalid (HTTP %s); buffering and quarantining", task_id, submit_resp.status_code)
                self.buffer.buffer_submission(
                    task_id=task_id,
                    lease_id=lease_id,
                    idempotency_key=idempotency_key,
                    submission_payload=submission,
                )
                pending = self.buffer.get_pending_submissions(limit=1)
                for p in pending:
                    if p["idempotency_key"] == idempotency_key:
                        self.buffer.quarantine_submission(p["id"], reason=f"HTTP_{submit_resp.status_code}:{submit_resp.text[:200]}")
                return exec_outcome
        except Exception as e:
            logger.warning("Submission failed, buffering locally: %s", e)

        if not submitted:
            self.buffer.buffer_submission(
                task_id=task_id,
                lease_id=lease_id,
                idempotency_key=idempotency_key,
                submission_payload=submission,
            )

        return exec_outcome

    def run_cycle(self) -> Dict[str, Any]:
        """
        Perform one complete poll/heartbeat/sync cycle.
        Strictly enforces authentication gating:
        Failed registration immediately halts execution before heartbeat,
        buffer submission, or task leasing.
        """
        # 1. Enforce authentication and registration before any activity
        if not self.is_registered:
            if self.enrollment_failed and not self.token:
                logger.warning("Cannot run cycle: enrollment token was rejected (HTTP 403) and no worker token exists.")
                return {
                    "registered": False,
                    "flushed_buffer_count": 0,
                    "executed_task": False,
                    "pending_buffer_count": self.buffer.count_pending(),
                    "status": "enrollment_rejected",
                }
            reg_ok = self.register()
            if not reg_ok:
                return {
                    "registered": False,
                    "flushed_buffer_count": 0,
                    "executed_task": False,
                    "pending_buffer_count": self.buffer.count_pending(),
                    "status": "registration_failed",
                }

        # 2. Flush offline buffer (only when registered)
        flushed = self.flush_offline_buffer()
        if not self.is_registered:
            return {
                "registered": False,
                "flushed_buffer_count": flushed,
                "executed_task": False,
                "pending_buffer_count": self.buffer.count_pending(),
                "status": "auth_lost_during_buffer_flush",
            }

        # 3. Heartbeat if due (only when registered)
        now = time.time()
        if now - self.last_heartbeat >= self.config.heartbeat_interval:
            self.send_heartbeat()
            if not self.is_registered:
                return {
                    "registered": False,
                    "flushed_buffer_count": flushed,
                    "executed_task": False,
                    "pending_buffer_count": self.buffer.count_pending(),
                    "status": "auth_lost_during_heartbeat",
                }

        # 4. Poll and execute task (strictly authenticated)
        executed_task = self.poll_and_execute_task()

        return {
            "registered": self.is_registered,
            "flushed_buffer_count": flushed,
            "executed_task": executed_task is not None,
            "pending_buffer_count": self.buffer.count_pending(),
            "status": "active" if self.is_registered else "unregistered",
        }
