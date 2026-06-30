from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

import uvicorn

from .alerts import AlertSender
from .config import Config, load_config
from .db import BufferedWriter, close_pool, create_pool
from .exceptions import SyswatchServerError
from .grpc import GrpcServer, MetricServicer
from .metrics import MetricsRegistry, start_metrics_server
from .observability.tracing import setup_tracing, shutdown_tracing
from .pki.ca import CertificateAuthority, CertPaths
from .web import create_app, instrument_fastapi_app
from .web.auth import AuthManager

logger = logging.getLogger(__name__)


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)


async def run(cfg: Config) -> None:

    setup_tracing(cfg)

    metrics = MetricsRegistry()
    start_metrics_server(port=cfg.server.metrics_port)

    logger.info("Connecting to database...")
    db_pool = await create_pool(cfg)

    ca_paths = CertPaths(
        ca_cert=cfg.pki.ca_cert,
        ca_key=cfg.pki.ca_key,
        server_cert=cfg.tls.server_cert,
        server_key=cfg.tls.server_key,
    )
    ca = CertificateAuthority.load(ca_paths)

    days_left = ca.days_until_ca_expiry()
    if days_left < 30:
        logger.warning("CA certificate expires in %d days", days_left)

    auth = AuthManager(cfg)

    writer = BufferedWriter(pool=db_pool, metrics=metrics)
    await writer.start()

    alert_sender = AlertSender(alertmanager_url=cfg.alertmanager.url)
    await alert_sender.start()

    servicer = MetricServicer(writer=writer, db_pool=db_pool, metrics=metrics)
    grpc_server = GrpcServer(cfg=cfg, servicer=servicer)
    await grpc_server.start()

    app = create_app(
        cfg=cfg,
        db_pool=db_pool,
        ca=ca,
        servicer=servicer,
        metrics=metrics,
        alert_sender=alert_sender,
        auth=auth,
    )
    instrument_fastapi_app(app)

    uvicorn_config = uvicorn.Config(
        app,
        host=cfg.server.http_host,
        port=cfg.server.http_port,
        log_level="info",
        access_log=False,
    )
    uvicorn_server = uvicorn.Server(uvicorn_config)
    uvicorn_server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal(sig: signal.Signals) -> None:
        logger.info("Received %s — shutting down", sig.name)
        shutdown_event.set()
        uvicorn_server.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _on_signal, sig)

    logger.info(
        "syswatch-server started: gRPC=%s:%d web=http://%s:%d metrics=:%d",
        cfg.server.grpc_host,
        cfg.server.grpc_port,
        cfg.server.http_host,
        cfg.server.http_port,
        cfg.server.metrics_port,
    )

    try:
        await uvicorn_server.serve()
    finally:
        logger.info("Shutting down subsystems...")

        await grpc_server.stop(grace=5.0)
        await writer.stop()
        await alert_sender.stop()
        await close_pool(db_pool)
        shutdown_tracing()

        logger.info("syswatch-server shutdown complete")


def main() -> None:

    parser = argparse.ArgumentParser(
        prog="syswatch-server-daemon",
        description="syswatch telemetry server (gRPC + web UI)",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("SYSWATCH_CONFIG", "/etc/syswatch/server/config.yaml"),
        help="Path to config.yaml (default: /etc/syswatch/server/config.yaml).",
    )
    parser.add_argument("--debug", action="store_true", help="Enable DEBUG logging.")
    args = parser.parse_args()

    setup_logging(debug=args.debug)
    logger.info("Loading config from %s", args.config)

    try:
        cfg = load_config(args.config)
    except SyswatchServerError as exc:
        logger.critical("%s", exc)
        sys.exit(1)

    try:
        asyncio.run(run(cfg))
    except SyswatchServerError as exc:
        logger.critical("Fatal: %s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
