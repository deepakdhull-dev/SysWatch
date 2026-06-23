from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from asyncio.events import get_running_loop

from .assembler import Assembler
from .collectors import HostInfoCollector
from .config import Config, load_config
from .encoder import Encoder
from .sampler import DropCounter, Sampler
from .streamer import Streamer


def setup_logging(debug=False):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


logger = logging.getLogger(__name__)


def create_queues(cfg):
    encoder_queue = asyncio.Queue(maxsize=32)
    streamer_queue = asyncio.Queue(maxsize=cfg.streamer.send_queue_size)
    return encoder_queue, streamer_queue


async def run(cfg, debug=False):
    drop_counter = DropCounter()
    encoder_queue, streamer_queue = create_queues(cfg)
    host_info_collector = HostInfoCollector()
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
        host_info_collector=host_info_collector,
    )

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_signal(sig):
        logger.info("Received signal %s — initiating graceful shutdown", sig.name)
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, on_signal, sig)
    logger.info(
        "syswatch-agent starting up (agent_id=%s, server=%s:%d, frame_interval=%.1fs)",
        cfg.agent.agent_id,
        cfg.server.host,
        cfg.server.port,
        cfg.sampler.frame_interval,
    )

    try:
        await assembler.start()
        logger.info(f"Assembler started(6 collectors task running)")
        await sampler.start()
        logger.info(f"Sampler started (frame interval={cfg.sampler.frame_interval} s")
        await encoder.start()
        logger.info(f"Encoder started")
        await streamer.start()
        logger.info(
            "syswatch-agent fully started. Monitoring %d service(s): %s",
            len(cfg.agent.services),
            ", ".join(cfg.agent.services) if cfg.agent.services else "(none)",
        )

        await shutdown_event.wait()

    except Exception as exc:
        logger.critical(f"Fatal error during agent startup:{exc}", exc_info=True)

    finally:
        logger.info("Shutting doen pipeline...")
        try:
            await streamer.stop()
            logger.info("Streamer stopped")
        except Exception as e:
            logger.error(f"Error stopping Streamer:{e}")

        try:
            await encoder.stop()
            logger.info("Encoder stopped")
        except Exception as exc:
            logger.error("Error stopping encoder: %s", exc)

        try:
            await sampler.stop()
            logger.info("Sampler stopped")
        except Exception as exc:
            logger.error("Error stopping sampler: %s", exc)

        try:
            await assembler.stop()
            logger.info("Assembler stopped")
        except Exception as exc:
            logger.error("Error stopping assembler: %s", exc)

        logger.info("syswatch-agent shutdown complete")


def main(debug=False):
    setup_logging(debug=debug)
    logger.info("loading condig from /etc/syswatch/agent.yaml")
    try:
        cfg = load_config("/etc/syswatch/agent.yaml")
    except FileNotFoundError as e:
        logger.critical(
            f"{e}\n Has the agent been installed? Run install.sh with server provided zip."
        )
        sys.exit(1)
    except Exception as exc:
        logger.critical("Failed to load config: %s", exc, exc_info=True)
        sys.exit(1)

    logger.debug(
        "Config loaded: agent_id=%s, services=%s",
        cfg.agent.agent_id,
        cfg.agent.services,
    )

    try:
        asyncio.run(run(cfg, debug=debug))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="syswatch-agent",
        description="syswatch metric collection agent",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable DEBUG level logging (very verbose, for development only)",
    )
    args = parser.parse_args()
    main(debug=args.debug)
