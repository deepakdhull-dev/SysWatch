from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator

import grpc
import grpc.aio

from ..proto import syswatch_pb2, syswatch_pb2_grpc

if TYPE_CHECKING:
    from ..db.writer import BufferedWriter
    from ..metrics.registry import MetricsRegistry

logger = logging.getLogger(__name__)


@dataclass
class ConnectedAgent:
    agent_id: str
    peer: str
    connected_at: float
    frames_received: int = 0
    frames_dropped: int = 0
    last_frame_at: float = field(default_factory=time.monotonic)

    def uptime_seconds(self) -> float:
        return time.monotonic() - self.connected_at


class MetricServicer(syswatch_pb2_grpc.MetricServiceServicer):
    def __init__(
        self,
        writer: BufferedWriter,
        db_pool: Any,
        metrics: MetricsRegistry,
    ) -> None:
        self._writer = writer
        self._db_pool = db_pool
        self._metrics = metrics

        self._connected: dict[str, ConnectedAgent] = {}

        self._lock = asyncio.Lock()

    async def Register(
        self,
        request: syswatch_pb2.HostInfo,
        context: grpc.aio.ServicerContext,
    ) -> syswatch_pb2.RegisterAck:
        agent_id = request.agent_id.strip()
        peer = context.peer()

        logger.info(
            "Register: agent_id=%r peer=%s hostname=%r os=%r cpu=%r cores=%d threads=%d",
            agent_id,
            peer,
            request.hostname,
            request.os_name,
            request.cpu_model,
            request.cpu_cores,
            request.cpu_threads,
        )

        if not agent_id:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "HostInfo.agent_id must not be empty",
            )
            return syswatch_pb2.RegisterAck(success=False, error="agent_id empty")

        if not request.hostname:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "HostInfo.hostname must not be empty",
            )
            return syswatch_pb2.RegisterAck(success=False, error="hostname empty")

        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO agents (
                        agent_id, hostname, kernel, os_name,
                        cpu_model, cpu_cores, cpu_threads,
                        registered_at, last_seen
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW())
                    ON CONFLICT (agent_id) DO UPDATE SET
                        hostname    = EXCLUDED.hostname,
                        kernel      = EXCLUDED.kernel,
                        os_name     = EXCLUDED.os_name,
                        cpu_model   = EXCLUDED.cpu_model,
                        cpu_cores   = EXCLUDED.cpu_cores,
                        cpu_threads = EXCLUDED.cpu_threads,
                        last_seen   = NOW()
                    """,
                    agent_id,
                    request.hostname,
                    request.kernel,
                    request.os_name,
                    request.cpu_model,
                    request.cpu_cores,
                    request.cpu_threads,
                )
        except Exception as exc:
            logger.error(
                "Register: DB upsert failed for agent_id=%r: %s",
                agent_id,
                exc,
                exc_info=True,
            )
            await context.abort(
                grpc.StatusCode.INTERNAL,
                "Server DB error during registration; retry in a few seconds",
            )
            return syswatch_pb2.RegisterAck(success=False, error="db error")

        received_at = int(time.time() * 1000)

        logger.info(
            "Register: agent_id=%r registered successfully (received_at=%d)",
            agent_id,
            received_at,
        )

        return syswatch_pb2.RegisterAck(
            success=True,
            received_at=received_at,
        )

    async def Stream(
        self,
        request_iterator: AsyncIterator[syswatch_pb2.MetricFrame],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[syswatch_pb2.FrameAck]:
        peer = context.peer()

        agent_id: str | None = None

        first_frame = True
        state: ConnectedAgent | None = None

        try:
            async for frame in request_iterator:
                if first_frame:
                    first_frame = False
                    agent_id = frame.agent_id.strip()

                    if not agent_id:
                        await context.abort(
                            grpc.StatusCode.INVALID_ARGUMENT,
                            "MetricFrame.agent_id must not be empty",
                        )
                        return

                    state = ConnectedAgent(
                        agent_id=agent_id,
                        peer=peer,
                        connected_at=time.monotonic(),
                    )
                    async with self._lock:
                        if agent_id in self._connected:
                            logger.warning(
                                "Stream: agent_id=%r already in connected registry "
                                "(possible duplicate connection from %s). Overwriting.",
                                agent_id,
                                peer,
                            )
                        self._connected[agent_id] = state

                    self._metrics.agents_connected.inc()

                    logger.info(
                        "Stream: agent_id=%r connected from %s",
                        agent_id,
                        peer,
                    )

                if state is not None:
                    state.frames_received += 1
                    state.last_frame_at = time.monotonic()
                    state.frames_dropped += frame.frames_dropped_since_last_connect

                logger.debug(
                    "Stream: frame agent_id=%r frame_id=%r ts=%d "
                    "cpu=%.1f%% ram=%.1f%% dropped=%d",
                    frame.agent_id,
                    frame.frame_id,
                    frame.timestamp,
                    frame.cpu_pct,
                    frame.ram_pct,
                    frame.frames_dropped_since_last_connect,
                )

                stored = False
                error_msg = ""
                try:
                    self._writer.enqueue(frame)
                    stored = True
                    self._metrics.frames_received.labels(agent_id=frame.agent_id).inc()
                except Exception as exc:
                    error_msg = str(exc)
                    logger.error(
                        "Stream: failed to enqueue frame %r from agent %r: %s",
                        frame.frame_id,
                        frame.agent_id,
                        exc,
                    )

                yield syswatch_pb2.FrameAck(
                    frame_id=frame.frame_id,
                    stored=stored,
                    received_at=int(time.time() * 1000),
                    error=error_msg,
                )

        except grpc.aio.AioRpcError as exc:
            logger.warning(
                "Stream: agent_id=%r stream interrupted: %s %s",
                agent_id or "unknown",
                exc.code(),
                exc.details(),
            )

        except asyncio.CancelledError:
            logger.info(
                "Stream: agent_id=%r stream cancelled (server shutdown)",
                agent_id or "unknown",
            )
            raise

        finally:
            if agent_id is not None:
                async with self._lock:
                    self._connected.pop(agent_id, None)
                self._metrics.agents_connected.dec()

                if state is not None:
                    logger.info(
                        "Stream: agent_id=%r disconnected. "
                        "uptime=%.1fs frames_received=%d frames_dropped=%d",
                        agent_id,
                        state.uptime_seconds(),
                        state.frames_received,
                        state.frames_dropped,
                    )

    def connected_agents(self) -> dict[str, ConnectedAgent]:

        return dict(self._connected)

    def agent_is_connected(self, agent_id: str) -> bool:
        return agent_id in self._connected
