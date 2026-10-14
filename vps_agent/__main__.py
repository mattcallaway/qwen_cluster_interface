"""
Executable Daemon Entrypoint for Qwen Collective VPS Worker Agent (Milestone 2).

Enables running the daemon via:
    python -m vps_agent

Features:
- CLI argument parsing (--config, --once, --validate, --version)
- Structured logging without secret leakage
- Strict configuration validation and nonzero exit on fatal misconfiguration
- Graceful shutdown handling for SIGTERM (systemd stop) and SIGINT (Ctrl+C)
- Bounded recovery backoff to prevent tight restart or re-registration retry loops
- Authentication enforcement before task leasing or result dispatch
"""

import argparse
import logging
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Optional

from vps_agent.agent import VPSWorkerAgent
from vps_agent.config import AgentConfig, load_config, validate_config

__version__ = "1.0.0"

logger = logging.getLogger("qwen_collective.vps_daemon")


def configure_logging(level_name: Optional[str] = None) -> None:
    """Configure systemd journal-friendly structured logging."""
    lvl_str = (level_name or os.getenv("QC_LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, lvl_str, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def parse_args(args=None) -> argparse.Namespace:
    """Parse command line options for daemon execution."""
    parser = argparse.ArgumentParser(
        prog="python -m vps_agent",
        description="Qwen Collective VPS Execution Worker Daemon",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to environment or configuration file (e.g. /etc/qwen-collective/worker.env)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Execute exactly one poll/sync cycle and exit immediately (useful for health checks)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate configuration and exit with status 0 (valid) or 1 (invalid)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"qwen-vps-agent {__version__}",
    )
    return parser.parse_args(args)


def run_daemon(config: AgentConfig, once: bool = False, stop_event: Optional[threading.Event] = None) -> int:
    """
    Main worker daemon execution loop.
    Returns process exit code (0 on normal/clean shutdown, 1 on fatal error).
    """
    if stop_event is None:
        stop_event = threading.Event()

    def handle_signal(signum, frame):
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.info("Received termination signal (%s); initiating graceful shutdown...", sig_name)
        stop_event.set()

    # Register signals only if running in the main thread
    if threading.current_thread() is threading.main_thread():
        try:
            signal.signal(signal.SIGINT, handle_signal)
            signal.signal(signal.SIGTERM, handle_signal)
        except (ValueError, AttributeError):
            pass

    logger.info("==================================================================")
    logger.info("Qwen Collective VPS Worker Daemon starting")
    logger.info("Worker ID       : %s", config.worker_id)
    logger.info("Worker Name     : %s", config.worker_name)
    logger.info("Controller URL  : %s", config.controller_url)
    logger.info("Poll Interval   : %ds", config.poll_interval)
    logger.info("Heartbeat Intvl : %ds", config.heartbeat_interval)
    logger.info("Data Directory  : %s", config.data_dir)
    logger.info("Token File Path : %s (exists=%s)", config.token_file_path, config.token_file_path.exists())
    has_token = bool(config.worker_token) or config.token_file_path.exists()
    has_enr = bool(config.enrollment_token)
    logger.info("Auth Status     : worker_token_ready=%s, enrollment_token_ready=%s", has_token, has_enr)
    logger.info("PID             : %d", os.getpid())
    logger.info("==================================================================")

    agent = VPSWorkerAgent(config=config)

    # Initial registration attempt
    registered = agent.register()
    if not registered and agent.enrollment_failed and not agent.token:
        logger.error(
            "Fatal authentication failure: enrollment token was rejected by controller (HTTP 403) "
            "and no persistent worker token exists."
        )
        if once:
            return 1

    consecutive_failures = 0
    max_backoff = 60.0
    initial_backoff = max(5.0, float(config.poll_interval))

    try:
        while not stop_event.is_set():
            cycle_result = agent.run_cycle()
            status = cycle_result.get("status")

            if once:
                logger.info("Single-cycle execution finished with status: %s", status)
                return 0 if cycle_result.get("registered") else 1

            # Determine sleep duration and backoff behavior
            if cycle_result.get("registered"):
                consecutive_failures = 0
                sleep_seconds = float(config.poll_interval)
            else:
                consecutive_failures += 1
                # Bounded exponential backoff: 5s, 10s, 20s, 40s, max 60s
                sleep_seconds = min(max_backoff, initial_backoff * (2 ** (consecutive_failures - 1)))
                if agent.enrollment_failed and not agent.token:
                    sleep_seconds = max_backoff
                    logger.warning(
                        "Registration paused: enrollment token rejected. "
                        "Waiting %.1fs for operator to configure fresh credentials...",
                        sleep_seconds,
                    )
                else:
                    logger.warning(
                        "Registration/Authentication not established (status: %s). "
                        "Pausing task leasing; backing off for %.1fs before next attempt (retry #%d)...",
                        status,
                        sleep_seconds,
                        consecutive_failures,
                    )

            # Interruptible sleep
            stop_event.wait(sleep_seconds)

    except Exception as e:
        logger.exception("Unexpected exception in daemon execution loop: %s", e)
        return 1
    finally:
        logger.info("VPS worker daemon stopped cleanly. Cleaning up resources.")

    return 0


def main(args=None) -> int:
    """CLI entrypoint for python -m vps_agent."""
    cli_args = parse_args(args)
    configure_logging()

    # 1. Load configuration
    try:
        config = load_config(config_file=cli_args.config)
    except Exception as e:
        logger.error("Failed to load worker configuration: %s", e)
        return 1

    # 2. Re-configure logging if log level was specified in config/env
    configure_logging()

    # 3. Validate configuration
    is_valid, error_msg = validate_config(config)
    if not is_valid:
        logger.error("Configuration validation error: %s", error_msg)
        return 1

    if cli_args.validate:
        logger.info("Configuration is valid.")
        return 0

    # 4. Run worker daemon
    return run_daemon(config=config, once=cli_args.once)


if __name__ == "__main__":
    sys.exit(main())
