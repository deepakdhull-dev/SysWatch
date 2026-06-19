from __future__ import annotations

import asyncio
import logging

from syswatch_agent.proto import syswatch_pb2

from .collectors import DiskUsage, MetricFrame, NetworkInterface, ServiceStatus

logger = logging.getLogger(__name__)


class Encoder:
    def __init__(self, in_queue, out_queue):
        self._in_queue = in_queue
        self._out_queue = out_queue
        self._tasks = None
        self._frames_encoded = 0
        self._dropped_count = 0

    async def start(self):
        self._tasks = asyncio.create_task(self.run(), name="Encoder-loop")
        logger.info("Encoder started")

    async def stop(self):
        if self._tasks:
            self._tasks.cancel()
            await asyncio.gather(self._tasks, return_exceptions=True)
            logger.info(
                f"Encoder stopped. Encoded: {self._frames_encoded}. Dropped (queue full): {self._dropped_count}."
            )

    async def run(self):
        while True:
            try:
                frame = await self._in_queue.get()
                try:
                    proto_frame = self.encode_frame(frame)
                    try:
                        self._out_queue.put_nowait(proto_frame)
                        self._frames_encoded += 1
                        logger.debug(
                            f"Encoder: encoded frame {frame.frame_id} → proto ({proto_frame.ByteSize()} bytes)"
                        )

                    except asyncio.QueueFull:
                        self._dropped_count += 1
                        logger.warning(
                            f"Encoder: streamer queue full, dropping encoded frame {frame.frame_id}. Total encoder-side drops: {self._dropped_count}"
                        )
                except Exception as e:
                    logger.error(
                        f"Encoder: failed to encode frame {frame.frame_id}:{e}",
                        exc_info=True,
                    )
                finally:
                    self._in_queue.task_done()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Encoder loop error: {e}", exc_info=True)

    def encode_frame(self, frame):
        proto_frame = syswatch_pb2.MetricFrame(
            agent_id=frame.agent_id,
            frame_id=frame.frame_id,
            timestamp=frame.timestamp,
            cpu_pct=frame.cpu_pct,
            ram_pct=frame.ram_pct,
            ram_used=frame.ram_used,
            ram_total=frame.ram_total,
            swap_pct=frame.swap_pct,
            swap_total=frame.swap_total,
            swap_used=frame.swap_used,
            load_1m=frame.load_1m,
            load_5m=frame.load_5m,
            load_15m=frame.load_15m,
            frames_dropped_since_last_connect=frame.frames_dropped_since_last_connect,
        )

        proto_frame.interfaces.extend(
            [self.encode_network(iface) for iface in frame.interfaces]
        )

        proto_frame.disks.extend([self.encode_disk(dsk) for dsk in frame.disks])

        proto_frame.services.extend(
            [self.encode_service(svc) for svc in frame.services]
        )

        return proto_frame

    def encode_network(self, iface):
        return syswatch_pb2.NetworkInterface(
            interface_name=iface.interface_name,
            bytes_in=iface.bytes_in,
            bytes_out=iface.bytes_out,
            packets_in=iface.packets_in,
            packets_out=iface.packets_out,
            errors_in=iface.errors_in,
            errors_out=iface.errors_out,
        )

    def encode_disk(self, disk):
        return syswatch_pb2.DiskUsage(
            mount_point=disk.mount_point,
            pct=disk.pct,
            used=disk.used,
            total=disk.total,
        )

    def encode_service(self, svc):
        return syswatch_pb2.ServiceStatus(
            name=svc.name,
            active=svc.active,
        )
