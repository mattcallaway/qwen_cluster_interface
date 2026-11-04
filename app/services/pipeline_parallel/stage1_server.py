import json
import logging
import socket
import threading
import time
from typing import Callable, List, Optional, Set

from .activation_protocol import ActivationProtocol
from .runner import PipelineStageRunner
from .socket_transport import recv_framed_bytes, send_framed_bytes
from .topology import PipelineStageConfig

logger = logging.getLogger(__name__)


class Stage1SocketServer:
    """
    TCP Socket Server for receiving and processing pipeline activation frames.
    Implements wire framing, HMAC authentication, bounded concurrency,
    per-client socket timeouts, explicit overload rejection, and Stage 1 execution.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8990,
        secret_key: bytes = b"qc_wireguard_pipeline_secret_key_32b!",
        stage_config: Optional[PipelineStageConfig] = None,
        stage_compute_fn: Optional[Callable[[List[float], int, int], List[float]]] = None,
        hidden_dim: int = 896,
        max_concurrent_sessions: int = 16,
        client_timeout_s: float = 5.0,
        enforce_ordering: bool = True,
    ):
        self.host = host
        self.port = port
        self.secret_key = secret_key
        self.protocol = ActivationProtocol(secret_key)
        self.hidden_dim = hidden_dim
        self.max_concurrent_sessions = max(1, int(max_concurrent_sessions))
        self.client_timeout_s = float(client_timeout_s)

        if stage_config is None:
            # Default Stage 1 configuration: Layers 12..23 (Total 24 layers)
            self.stage_config = PipelineStageConfig(
                stage_index=1,
                node_id="qwen-laptop",
                model_name="Qwen/Qwen2.5-0.5B-Instruct",
                layer_start=12,
                layer_end=24,
                endpoint_url=f"tcp://{host}:{port}",
                total_layers=24,
            )
        else:
            self.stage_config = stage_config

        self.runner = PipelineStageRunner(
            config=self.stage_config,
            protocol=self.protocol,
            hidden_dim=self.hidden_dim,
            stage_compute_fn=stage_compute_fn,
            enforce_ordering=enforce_ordering,
        )

        self._server_sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._active_client_socks: Set[socket.socket] = set()
        self._client_threads: List[threading.Thread] = []
        self.active_sessions = 0
        self.peak_active_sessions = 0
        self.rejected_sessions = 0
        self.frames_processed = 0
        self.auth_failures = 0
        self.timeout_failures = 0

    def start(self, background: bool = True) -> None:
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(min(64, self.max_concurrent_sessions * 2))
        self._running = True

        if background:
            self._thread = threading.Thread(target=self._serve_loop, daemon=True)
            self._thread.start()
        else:
            self._serve_loop()

    def stop(self) -> None:
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        with self._lock:
            active_socks = list(self._active_client_socks)
            threads = list(self._client_threads)
        for s in active_socks:
            try:
                s.close()
            except Exception:
                pass
        for t in threads:
            if t.is_alive():
                t.join(timeout=1.0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _serve_loop(self) -> None:
        logger.info("Stage 1 Socket Server listening on %s:%d", self.host, self.port)
        while self._running:
            try:
                conn, addr = self._server_sock.accept()
            except (socket.error, OSError):
                break

            with self._lock:
                if self.active_sessions >= self.max_concurrent_sessions:
                    self.rejected_sessions += 1
                    overloaded = True
                else:
                    self.active_sessions += 1
                    if self.active_sessions > self.peak_active_sessions:
                        self.peak_active_sessions = self.active_sessions
                    self._active_client_socks.add(conn)
                    overloaded = False

            if overloaded:
                self._reject_overloaded_client(conn)
                continue

            client_thread = threading.Thread(
                target=self._handle_client, args=(conn, addr), daemon=True
            )
            with self._lock:
                self._client_threads = [t for t in self._client_threads if t.is_alive()]
                self._client_threads.append(client_thread)
            client_thread.start()

    def _reject_overloaded_client(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(0.25)
            # Drain one incoming frame if immediately ready so client can read rejection
            try:
                _ = recv_framed_bytes(conn)
            except Exception:
                pass
            resp = {
                "status": "rejected_overloaded",
                "error": f"Server at max_concurrent_sessions ({self.max_concurrent_sessions})",
                "hmac_verified": False,
            }
            send_framed_bytes(conn, json.dumps(resp).encode("utf-8"))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _handle_client(self, conn: socket.socket, addr: tuple) -> None:
        seen_sessions: Set[str] = set()
        try:
            conn.settimeout(self.client_timeout_s)
            with conn:
                while self._running:
                    try:
                        payload = recv_framed_bytes(conn)
                        if payload is None:
                            break

                        t_start = time.perf_counter()

                        # Cryptographic verification & Stage execution
                        try:
                            egress_result = self.runner.process_egress(payload)
                            t_compute = (time.perf_counter() - t_start) * 1000.0
                            with self._lock:
                                self.frames_processed += 1
                            seen_sessions.add(egress_result["session_id"])

                            response = {
                                "status": "ok",
                                "session_id": egress_result["session_id"],
                                "token_index": egress_result["token_index"],
                                "predicted_token_id": egress_result["predicted_token_id"],
                                "final_stage_index": egress_result["final_stage_index"],
                                "compute_time_ms": round(t_compute, 3),
                                "hmac_verified": True,
                            }
                        except ValueError as ve:
                            with self._lock:
                                self.auth_failures += 1
                            response = {
                                "status": "auth_error",
                                "error": str(ve),
                                "hmac_verified": False,
                            }
                        except Exception as e:
                            response = {
                                "status": "error",
                                "error": str(e),
                                "hmac_verified": False,
                            }

                        resp_bytes = json.dumps(response).encode("utf-8")
                        send_framed_bytes(conn, resp_bytes)

                    except socket.timeout:
                        with self._lock:
                            self.timeout_failures += 1
                        break
                    except (ValueError, ConnectionError) as frame_err:
                        with self._lock:
                            self.auth_failures += 1
                        try:
                            err_resp = {
                                "status": "auth_error",
                                "error": str(frame_err),
                                "hmac_verified": False,
                            }
                            send_framed_bytes(conn, json.dumps(err_resp).encode("utf-8"))
                        except Exception:
                            pass
                        break
                    except (socket.error, OSError):
                        break
        finally:
            for sid in seen_sessions:
                self.runner.release_session(sid)
            with self._lock:
                self._active_client_socks.discard(conn)
                self.active_sessions = max(0, self.active_sessions - 1)
