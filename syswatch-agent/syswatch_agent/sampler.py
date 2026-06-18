from __future__ import annotations

import asyncio
import logging
import time

from .assembler import Assembler
from .collectors import MetricFrame
from .config import Config

logger = logging.getLogger(__name__)


class Sampler:
    def __init__(self, cfg: Config, assembler, drop_counter, out_queue):
        self._cfg = cfg
        self._assembler = assembler
        self._drop_counter = drop_counter
        self._out_queue = out_queue
        self._tasks = None
        self._frames_emitted = 0
        self._frame_dropped_queue_full = 0

    async def start(self):
        self._tasks = asyncio.create_task(self.run(), name="sampler-loop")
        logger.info(
            f"Sampler started (frame interval={self._cfg.sampler.frame_interval})"
        )

    async def stop(self):
        if self._tasks:
            self._tasks.cancel()
            await asyncio.gather(self._tasks, return_exceptions=True)
        logger.info(
            f"Sampler stopped. emmited {self._frames_emitted} frames. dropped (queue full) {self._frame_dropped_queue_full} frames"
        )

    async def run(self):
        interval = self._cfg.sampler.frame_interval
        while True:
            cycle_start = time.monotonic()
            try:
                dropped = self._drop_counter.get()
                frame = await self._assembler.assemble_frame(frames_dropped=dropped)
                if frame is None:
                    logger.debug(
                        "Sampler: assembler returned None frame, skipping cycle"
                    )
                else:
                    try:
                        self._out_queue.put_nowait(frame)
                        self._frames_emitted += 1
                        self._drop_counter.reset()
                        logger.debug(
                            f"Sampler emiited frame {frame.frame_id} (dropped since connect: {dropped})"
                        )
                    except asyncio.QueueFull:
                        self._frame_dropped_queue_full += 1
                        logger.warning(
                            f"Sampler: encoder queue full, dropping frame {frame.frame_id} (total sampler side drops:{self._frame_dropped_queue_full})"
                        )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Sampler loop error: {e}", exc_info=True)
            elapsed_time = time.monotonic() - cycle_start
            sleep_time = max(0.0, interval - elapsed_time)
            await asyncio.sleep(sleep_time)


class DropCounter:
    def __init__(self):
        self._count = 0

    def increment(self):
        self._count += 1

    def reset(self):
        self._count = 0

    def get(self):
        return self._count

    def get_and_reset(self):
        val = self._count
        self._count = 0
        return val
