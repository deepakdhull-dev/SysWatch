from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

    from ..proto import syswatch_pb2

logger = logging.getLogger(__name__)

FLUSH_COUNT: int = 5
FLUSH_INTERVAL: float = 2.0
FLUSH_LOOP_SLEEP: float = 0.5
MetricRow = tuple[
    str,
    datetime.datetime,
    str,
    float,
    float,
    int,
    int,
    float,
    int,
    int,
    float,
    float,
    float,
    int,
]
DiskRow = tuple[str, datetime.datetime, str, str, float, int, int]
NetworkRow = tuple[str, datetime.datetime, str, str, int, int, int, int, int, int]
ServiceRow = tuple[str, datetime.datetime, str, str, bool]


class BufferedWriter:
    def __init__(self, pool: asyncpg.Pool, metrics: Any) -> None:  # type: ignore[type-arg]
        self._pool = pool
        self._metrics = metrics
        self._frames: list[MetricRow] = []
        self._disks: list[DiskRow] = []
        self._interfaces: list[NetworkRow] = []
        self._services: list[ServiceRow] = []
        self._last_flush_at: float = time.monotonic()

        self._total_frames_written: int = 0
        self._total_flushes: int = 0
        self._total_flush_errors: int = 0

        self._flush_task: asyncio.Task[None] | None = None

    def _frame_time(self, frame: syswatch_pb2.MetricFrame) -> datetime.datetime:
        return datetime.datetime.fromtimestamp(
            frame.timestamp / 1000.0,
            tz=datetime.timezone.utc,
        )

    def _to_metric_row(self, frame: syswatch_pb2.MetricFrame) -> MetricRow:

        return (
            frame.agent_id,
            self._frame_time(frame),
            frame.frame_id,
            frame.cpu_pct,
            frame.ram_pct,
            frame.ram_used,
            frame.ram_total,
            frame.swap_pct,
            frame.swap_used,
            frame.swap_total,
            frame.load_1m,
            frame.load_5m,
            frame.load_15m,
            frame.frames_dropped_since_last_connect,
        )

    def _to_disk_rows(self, frame: syswatch_pb2.MetricFrame) -> list[DiskRow]:
        t = self._frame_time(frame)
        return [
            (
                frame.agent_id,
                t,
                frame.frame_id,
                disk.mount_point,
                disk.pct,
                disk.used,
                disk.total,
            )
            for disk in frame.disks
        ]

    def _to_network_rows(self, frame: syswatch_pb2.MetricFrame) -> list[NetworkRow]:
        t = self._frame_time(frame)
        return [
            (
                frame.agent_id,
                t,
                frame.frame_id,
                iface.interface_name,
                iface.bytes_in,
                iface.bytes_out,
                iface.packets_in,
                iface.packets_out,
                iface.errors_in,
                iface.errors_out,
            )
            for iface in frame.interfaces
        ]

    def _to_service_rows(self, frame: syswatch_pb2.MetricFrame) -> list[ServiceRow]:

        t = self._frame_time(frame)
        return [
            (
                frame.agent_id,
                t,
                frame.frame_id,
                svc.name,
                svc.active,
            )
            for svc in frame.services
        ]

    def enqueue(self, frame: syswatch_pb2.MetricFrame) -> None:
        self._frames.append(self._to_metric_row(frame))
        self._disks.extend(self._to_disk_rows(frame))
        self._interfaces.extend(self._to_network_rows(frame))
        self._services.extend(self._to_service_rows(frame))

        logger.debug(
            "Enqueued frame %r (buffer: frames=%d disks=%d ifaces=%d svcs=%d)",
            frame.frame_id,
            len(self._frames),
            len(self._disks),
            len(self._interfaces),
            len(self._services),
        )

    def _drain_buffers(
        self,
    ) -> tuple[
        list[MetricRow],
        list[DiskRow],
        list[NetworkRow],
        list[ServiceRow],
    ]:

        frames = self._frames
        disks = self._disks
        interfaces = self._interfaces
        services = self._services

        self._frames = []
        self._disks = []
        self._interfaces = []
        self._services = []

        return frames, disks, interfaces, services

    async def _flush(self) -> None:
        frames, disks, interfaces, services = self._drain_buffers()

        if not frames:
            return

        flush_start = time.monotonic()
        n_frames = len(frames)

        logger.debug(
            "Flushing: frames=%d disks=%d ifaces=%d svcs=%d",
            n_frames,
            len(disks),
            len(interfaces),
            len(services),
        )

        try:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    await conn.copy_records_to_table(
                        "metrics",
                        records=frames,
                        columns=[
                            "agent_id",
                            "time",
                            "frame_id",
                            "cpu_pct",
                            "ram_pct",
                            "ram_used",
                            "ram_total",
                            "swap_pct",
                            "swap_used",
                            "swap_total",
                            "load_1m",
                            "load_5m",
                            "load_15m",
                            "frames_dropped",
                        ],
                    )
                    if disks:
                        await conn.copy_records_to_table(
                            "metrics_disk",
                            records=disks,
                            columns=[
                                "agent_id",
                                "time",
                                "frame_id",
                                "mount_point",
                                "pct",
                                "used",
                                "total",
                            ],
                        )

                    if interfaces:
                        await conn.copy_records_to_table(
                            "metrics_network",
                            records=interfaces,
                            columns=[
                                "agent_id",
                                "time",
                                "frame_id",
                                "interface_name",
                                "bytes_in",
                                "bytes_out",
                                "packets_in",
                                "packets_out",
                                "errors_in",
                                "errors_out",
                            ],
                        )

                    if services:
                        await conn.copy_records_to_table(
                            "metrics_service",
                            records=services,
                            columns=[
                                "agent_id",
                                "time",
                                "frame_id",
                                "service_name",
                                "active",
                            ],
                        )

            elapsed_ms = (time.monotonic() - flush_start) * 1000
            self._total_frames_written += n_frames
            self._total_flushes += 1
            self._last_flush_at = time.monotonic()

            self._metrics.db_flush_latency.observe(elapsed_ms / 1000.0)

            logger.debug(
                "Flush complete: %d frames in %.1fms (total_written=%d total_flushes=%d)",
                n_frames,
                elapsed_ms,
                self._total_frames_written,
                self._total_flushes,
            )

        except Exception as exc:
            self._total_flush_errors += 1
            logger.error(
                "Flush FAILED (frames lost=%d): %s",
                n_frames,
                exc,
                exc_info=True,
            )

    async def _flush_loop(self) -> None:
        logger.info(
            "BufferedWriter flush loop started "
            "(FLUSH_COUNT=%d FLUSH_INTERVAL=%.1fs check_interval=%.1fs)",
            FLUSH_COUNT,
            FLUSH_INTERVAL,
            FLUSH_LOOP_SLEEP,
        )

        while True:
            try:
                await asyncio.sleep(FLUSH_LOOP_SLEEP)

                has_data = bool(self._frames)
                count_trigger = len(self._frames) >= FLUSH_COUNT
                time_trigger = (
                    has_data
                    and (time.monotonic() - self._last_flush_at) >= FLUSH_INTERVAL
                )

                if count_trigger or time_trigger:
                    trigger_name = "count" if count_trigger else "time"
                    logger.debug(
                        "Flush triggered by %s (frames=%d)",
                        trigger_name,
                        len(self._frames),
                    )
                    await self._flush()

            except asyncio.CancelledError:
                logger.debug("Flush loop cancelled")
                raise

            except Exception as exc:
                logger.error("Flush loop unexpected error: %s", exc, exc_info=True)

    async def start(self) -> None:
        self._flush_task = asyncio.create_task(
            self._flush_loop(),
            name="buffered-writer-flush-loop",
        )
        logger.info("BufferedWriter started")

    async def stop(self) -> None:
        if self._flush_task is not None:
            self._flush_task.cancel()
            await asyncio.gather(self._flush_task, return_exceptions=True)
            self._flush_task = None

        remaining = len(self._frames)
        if remaining:
            logger.info(
                "BufferedWriter stopping: flushing %d remaining frames...",
                remaining,
            )
            await self._flush()

        logger.info(
            "BufferedWriter stopped. total_written=%d total_flushes=%d errors=%d",
            self._total_frames_written,
            self._total_flushes,
            self._total_flush_errors,
        )
