"""
System Observatory Background Engine.
Maintains continuous local controller sampling, heartbeat evaluation,
rollup generation, and retention pruning in an isolated, lightweight thread.
"""

import logging
import threading
import time
from typing import Optional

from app.services.observatory import alerting, collector, storage

logger = logging.getLogger("qwen_collective.observatory.engine")

_STOP_EVENT: threading.Event = threading.Event()
_THREAD: Optional[threading.Thread] = None


def _background_loop(stop_event: threading.Event) -> None:
    logger.info("System Observatory background telemetry engine started.")

    # Seed baseline rules
    try:
        storage.seed_default_alert_rules()
    except Exception as e:
        logger.warning("Could not seed default alert rules: %s", e)

    last_local_sample = 0.0
    last_heartbeat_check = 0.0
    last_minute_rollup = 0.0
    last_hourly_rollup = 0.0
    last_retention_prune = 0.0

    while not stop_event.wait(1.0):
        now = time.time()

        # 1. Local Windows controller sampling (every 5 seconds)
        if now - last_local_sample >= 5.0:
            try:
                sample = collector.collect_controller_telemetry()
                storage.record_telemetry_sample("controller-local", sample)
                alerting.evaluate_telemetry_for_alerts("controller-local", sample)
                last_local_sample = now
            except Exception as e:
                logger.debug("Local controller telemetry sample error: %s", e)

        # 2. Cluster worker heartbeat checks (every 15 seconds)
        if now - last_heartbeat_check >= 15.0:
            try:
                alerting.evaluate_cluster_heartbeats()
                last_heartbeat_check = now
            except Exception as e:
                logger.debug("Cluster heartbeat check error: %s", e)

        # 3. Minute rollups (every 60 seconds)
        if now - last_minute_rollup >= 60.0:
            try:
                storage.generate_minute_rollups()
                last_minute_rollup = now
            except Exception as e:
                logger.debug("Minute rollup error: %s", e)

        # 4. Hourly rollups (every 3600 seconds)
        if now - last_hourly_rollup >= 3600.0:
            try:
                storage.generate_hourly_rollups()
                last_hourly_rollup = now
            except Exception as e:
                logger.debug("Hourly rollup error: %s", e)

        # 5. Retention policy enforcement (every 12 hours)
        if now - last_retention_prune >= 43200.0:
            try:
                storage.enforce_retention_policy()
                last_retention_prune = now
            except Exception as e:
                logger.debug("Retention cleanup error: %s", e)

    logger.info("System Observatory background telemetry engine stopped.")


def start_background() -> None:
    """Start the observatory background sampler and rollup worker thread."""
    global _THREAD, _STOP_EVENT
    if _THREAD and _THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _THREAD = threading.Thread(
        target=_background_loop,
        args=(_STOP_EVENT,),
        daemon=True,
        name="SystemObservatoryEngine",
    )
    _THREAD.start()


def stop_background() -> None:
    """Stop the observatory background engine."""
    global _STOP_EVENT, _THREAD
    _STOP_EVENT.set()
    if _THREAD and _THREAD.is_alive():
        _THREAD.join(timeout=3.0)
        _THREAD = None
