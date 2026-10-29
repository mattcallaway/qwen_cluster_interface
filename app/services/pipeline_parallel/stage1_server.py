import json
import logging
import socket
import threading
import time
from typing import Callable, List, Optional

from .activation_protocol import ActivationProtocol
from .runner import PipelineStageRunner
from .socket_transport import recv_framed_bytes, send_framed_bytes
from .topology import PipelineStageConfig

logger = logging.getLogger(__name__)


class Stage1SocketServer:
    """
    TCP Socket Server for receiving and processing pipeline activation frames.
    Implements wire framing, HMAC authentication, and Stage 1 execution.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8990,
        secret_key: bytes = b"qc_wireguard_pipeline_secret_key_32b!",
        stage_config: Optional[PipelineStageConfig] = None,
        stage_compute_fn: Optional[Callable[[List[float], int, int], List[float]]] = None,
        hidden_dim: int = 896,
    ):
        self.host = host
        self.port = port
        self.secret_key = secret_key
        self.protocol = ActivationProtocol(secret_key)
        self.hidden_dim = hidden_dim

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
        )

        self._server_sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.frames_processed = 0
        self.auth_failures = 0

    def start(self, background: bool = True) -> None:
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(5)
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
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _serve_loop(self) -> None:
        logger.info("Stage 1 Socket Server listening on %s:%d", self.host, self.port)
        while self._running:
            try:
                conn, addr = self._server_sock.accept()
            except (socket.error, OSError):
                break
            client_thread = threading.Thread(
                target=self._handle_client, args=(conn, addr), daemon=True
            )
            client_thread.start()

    def _handle_client(self, conn: socket.socket, addr: tuple) -> None:
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
                        self.frames_processed += 1

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

                except (socket.error, OSError):
                    break
