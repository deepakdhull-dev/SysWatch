from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import Any

from .assembler import Assembler
from .collectors import HostInfoCollector
from .config import Config, load_config
from .encoder import Encoder
from .exceptions import ConfigError
from .sampler import DropCounter, Sampler
from .streamer import Streamer


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


logger = logging.getLogger(__name__)


def create_queues(cfg: Config) -> tuple[asyncio.Queue[Any], asyncio.Queue[Any]]:
    encoder_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=32)
    streamer_queue: asyncio.Queue[Any] = asyncio.Queue(
        maxsize=cfg.streamer.send_queue_size
    )
    return encoder_queue, streamer_queue


async def run(cfg: Config) -> None:
    drop_counter = DropCounter()
    encoder_queue, streamer_queue = create_queues(cfg)

    assembler = Assembler(cfg)
    sampler = Sampler(
        cfg,
        assembler=assembler,
        out_queue=encoder_queue,
        drop_counter=drop_counter,
    )
    encoder = Encoder(
        in_queue=encoder_queue,
        out_queue=streamer_queue,
    )
    streamer = Streamer(
        cfg=cfg,
        in_queue=streamer_queue,
        drop_counter=drop_counter,
        host_info_collector=HostInfoCollector(),
    )

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_signal(sig: signal.Signals) -> None:
        logger.info("Received signal %s — initiating graceful shutdown", sig.name)
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, on_signal, sig)

    logger.info(
        "syswatch-agent starting (agent_id=%s, server=%s:%d, frame_interval=%.1fs)",
        cfg.agent.agent_id,
        cfg.server.host,
        cfg.server.port,
        cfg.sampler.frame_interval,
    )

    try:
        await assembler.start()
        logger.info("Assembler started (6 collector tasks running)")
        await sampler.start()
        logger.info(
            "Sampler started (frame_interval=%.1fs)", cfg.sampler.frame_interval
        )
        await encoder.start()
        logger.info("Encoder started")
        await streamer.start()
        logger.info(
            "syswatch-agent fully started. Monitoring %d service(s): %s",
            len(cfg.agent.services),
            ", ".join(cfg.agent.services) if cfg.agent.services else "(none)",
        )
        await shutdown_event.wait()

    except Exception as exc:
        logger.critical("Fatal error during agent startup: %s", exc, exc_info=True)

    finally:
        logger.info("Shutting down pipeline...")
        for component, name in [
            (streamer, "Streamer"),
            (encoder, "Encoder"),
            (sampler, "Sampler"),
            (assembler, "Assembler"),
        ]:
            try:
                await component.stop()
                logger.info("%s stopped", name)
            except Exception as exc:
                logger.error("Error stopping %s: %s", name, exc)
        logger.info("syswatch-agent shutdown complete")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="syswatch-agent-daemon",
        description="syswatch metric collection daemon",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable DEBUG level logging (verbose, for development only)",
    )
    args = parser.parse_args()

    setup_logging(debug=args.debug)
    logger.info("Loading config from /etc/syswatch/agent.yaml")

    try:
        cfg = load_config("/etc/syswatch/agent.yaml")
    except ConfigError as exc:
        logger.critical("%s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.critical("Unexpected error loading config: %s", exc, exc_info=True)
        sys.exit(1)

    logger.debug(
        "Config loaded: agent_id=%s, services=%s",
        cfg.agent.agent_id,
        cfg.agent.services,
    )

    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
