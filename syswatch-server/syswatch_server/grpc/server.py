from __future__ import annotations

import logging
from concurrent import futures
from pathlib import Path
from typing import TYPE_CHECKING, Any

import grpc
import grpc.aio

from ..exceptions import CertificateError
from ..proto import syswatch_pb2_grpc

if TYPE_CHECKING:
    from .servicer import MetricServicer

logger = logging.getLogger(__name__)


class GrpcServer:
    def __init__(self, cfg: Any, servicer: MetricServicer) -> None:
        self._cfg = cfg
        self._servicer = servicer
        self._server: grpc.aio.Server | None = None

    def read_cert_file(self, path: str, label: str) -> bytes:
        p = Path(path)
        if not p.exists():
            raise CertificateError(
                f"mTLS {label} not found at {path}. "
                "Run install.sh (server path) to generate all certificates, "
                "or check that /etc/syswatch/server/ is intact."
            )
        try:
            return p.read_bytes()
        except PermissionError as exc:
            raise CertificateError(
                f"Cannot read mTLS {label} at {path}: permission denied. "
                "The server process must run as a user with read access to "
                "/etc/syswatch/server/."
            ) from exc

    def build_credentials(self):
        tls = self._cfg.grpc.tls

        ca_cert = self.read_cert_file(tls.ca_cert, "CA certificate")
        server_key = self.read_cert_file(tls.server_key, "server private key")
        server_cert = self.read_cert_file(tls.server_cert, "server certificate")

        logger.debug(
            "Loaded mTLS credentials: ca=%s server_cert=%s server_key=%s",
            tls.ca_cert,
            tls.server_cert,
            tls.server_key,
        )

        return grpc.ssl_server_credentials(
            private_key_certificate_chain_pairs=[(server_key, server_cert)],
            root_certificates=ca_cert,
            require_client_auth=True,
        )

    async def start(self):
        credentials = self.build_credentials()
        self._server = grpc.aio.server(
            futures.ThreadPoolExecutor(
                max_workers=self._cfg.grpc.max_workers, thread_name_prefix="grpc-worker"
            )
        )
        syswatch_pb2_grpc.add_MetricServiceServicer_to_server(
            self._servicer, self._server
        )

        bind_address = f"{self._cfg.grpc.host}:{self._cfg.grpc.port}"
        self._server.add_secure_port(bind_address, credentials)
        await self._server.start()

        logger.info(
            "gRPC server listening on %s (mTLS=on, require_client_auth=True, max_workers=%d)",
            bind_address,
            self._cfg.grpc.max_workers,
        )

    async def stop(self, grace: float = 5.0):
        if self._server is None:
            logger.debug("GrpcServer.stop() called but server was not running")
            return
        logger.info("gRPC server stopping (grace period=%.1fs)...", grace)
        await self._server.stop(grace)
        self._server = None
        logger.info("gRPC server stopped")

    async def wait_for_termination(self):
        if self._server is None:
            return
        await self._server.wait_for_termination()
