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
import time
from typing import Any, Dict, Optional
import requests

from vps_agent.buffer import OfflineBuffer
from vps_agent.capabilities import discover_local_hardware
from vps_agent.config import AgentConfig, load_config
from vps_agent.executor import execute_task

logger = logging.getLogger("qwen_collective.worker_agent")


class VPSWorkerAgent:
    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or load_config()
        self.buffer = OfflineBuffer(self.config.buffer_db_path)
        self.session = requests.Session()
        self.token = self.config.worker_token
        self.is_registered = False
        self.last_heartbeat = 0.0

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def register(self) -> bool:
        """Register the worker node and obtain/verify authentication token."""
        url = f"{self.config.controller_url}/api/v1/worker/register"
        hardware_info = discover_local_hardware()
        payload = {
            "node_id": self.config.worker_id,
            "name": self.config.worker_name,
            "role": self.config.worker_role,
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
            "agent_version": hardware_info["agent_version"],
        }

        try:
            resp = self.session.post(url, json=payload, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if "worker_token" in data:
                    self.token = data["worker_token"]
                    logger.info("Provisioned new worker token for node %s", self.config.worker_id)
                self.is_registered = True
                logger.info("Successfully registered worker node %s with controller", self.config.worker_id)
                return True
            else:
                logger.error("Registration failed with status %s: %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.warning("Could not contact controller for registration: %s", e)
            return False

    def send_heartbeat(self) -> bool:
        """Send health heartbeat to the controller."""
        url = f"{self.config.controller_url}/api/v1/worker/heartbeat"
        hardware_info = discover_local_hardware()
        payload = {
            "node_id": self.config.worker_id,
            "ram_available_mb": hardware_info["ram_available_mb"],
            "disk_free_gb": hardware_info["disk_free_gb"],
            "running_jobs_count": 0,
        }

        try:
            resp = self.session.post(url, json=payload, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                self.last_heartbeat = time.time()
                return True
            elif resp.status_code == 401:
                logger.warning("Heartbeat returned 401 Unauthorized; re-registering...")
                self.register()
            return False
        except Exception as e:
            logger.debug("Heartbeat ping failed: %s", e)
            return False

    def flush_offline_buffer(self) -> int:
        """Drain buffered submissions to the controller once connectivity is restored."""
        pending = self.buffer.get_pending_submissions(limit=10)
        flushed_count = 0
        for item in pending:
            task_id = item["task_id"]
            url = f"{self.config.controller_url}/api/v1/worker/tasks/{task_id}/submit"
            try:
                resp = self.session.post(url, json=item["payload"], headers=self._headers(), timeout=10)
                if resp.status_code == 200:
                    self.buffer.remove_submission(item["id"])
                    flushed_count += 1
                else:
                    logger.warning("Buffered task %s submission rejected: %s", task_id, resp.status_code)
                    break
            except Exception as e:
                logger.debug("Could not flush buffered submission for task %s: %s", task_id, e)
                break
        return flushed_count

    def poll_and_execute_task(self) -> Optional[Dict[str, Any]]:
        """Lease one task from controller, execute it, and submit results."""
        url = f"{self.config.controller_url}/api/v1/worker/tasks/lease"
        payload = {
            "worker_id": self.config.worker_id,
            "supported_categories": ["python_execution", "approved_shell", "file_processing", "monitoring"],
            "lease_duration_seconds": self.config.lease_duration,
        }

        try:
            resp = self.session.post(url, json=payload, headers=self._headers(), timeout=10)
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

        # Execute task
        exec_outcome = execute_task(task, timeout_seconds=self.config.default_task_timeout)

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
        }

        # Submit to controller or buffer offline
        submit_url = f"{self.config.controller_url}/api/v1/worker/tasks/{task_id}/submit"
        submitted = False
        try:
            submit_resp = self.session.post(submit_url, json=submission, headers=self._headers(), timeout=10)
            if submit_resp.status_code == 200:
                submitted = True
                logger.info("Submitted task %s results to controller", task_id)
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
        """Perform one complete poll/heartbeat/sync cycle."""
        # 1. Ensure registration
        if not self.is_registered:
            self.register()

        # 2. Flush any offline buffered results
        flushed = self.flush_offline_buffer()

        # 3. Heartbeat if due
        now = time.time()
        if now - self.last_heartbeat >= self.config.heartbeat_interval:
            self.send_heartbeat()

        # 4. Poll and execute available task
        executed_task = self.poll_and_execute_task()

        return {
            "registered": self.is_registered,
            "flushed_buffer_count": flushed,
            "executed_task": executed_task is not None,
            "pending_buffer_count": self.buffer.count_pending(),
        }
