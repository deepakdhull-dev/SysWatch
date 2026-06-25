from __future__ import annotations

import asyncio
import logging
import random

import grpc

from .config import Config
from .exceptions import CertificateError
from .proto import syswatch_pb2, syswatch_pb2_grpc
from .sampler import DropCounter

logger = logging.getLogger(__name__)


class Streamer:
    def __init__(
        self,
        cfg: Config,
        drop_counter: DropCounter,
        in_queue: asyncio.Queue[object],
        host_info_collector: object,
    ) -> None:
        self._cfg = cfg
        self._in_queue = in_queue
        self._host_info_collector = host_info_collector
        self._drop_counter = drop_counter

        self._attempt = 0
        self._task: asyncio.Task[None] | None = None

        self._frames_sent = 0
        self._ack_received = 0
        self._reconnect_count = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(
            self.connect_loop(), name="streamer-connect-loop"
        )
        logger.info("Streamer started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            logger.info(
                "Streamer stopped. sent=%d acks=%d reconnects=%d",
                self._frames_sent,
                self._ack_received,
                self._reconnect_count,
            )

    def build_channel(self) -> grpc.aio.Channel:
        certs = self._cfg.certs
        for path, label in [
            (certs.ca_cert, "CA cert"),
            (certs.client_cert, "client cert"),
            (certs.client_key, "client key"),
        ]:
            try:
                open(path).close()
            except FileNotFoundError:
                raise CertificateError(
                    f"mTLS {label} not found at {path}. "
                    "Reinstall the agent bundle provided by the server."
                )

        with open(certs.ca_cert, "rb") as f:
            ca_cert_bytes = f.read()
        with open(certs.client_key, "rb") as f:
            client_key_bytes = f.read()
        with open(certs.client_cert, "rb") as f:
            client_cert_bytes = f.read()

        credentials = grpc.ssl_channel_credentials(
            root_certificates=ca_cert_bytes,
            private_key=client_key_bytes,
            certificate_chain=client_cert_bytes,
        )

        target = f"{self._cfg.server.host}:{self._cfg.server.port}"
        logger.debug("Building mTLS channel to %s", target)
        return grpc.aio.secure_channel(target, credentials)

    async def connect_loop(self) -> None:
        while True:
            channel = None
            try:
                channel = self.build_channel()
                stub = syswatch_pb2_grpc.MetricServiceStub(channel)
                logger.info(
                    "Connecting to %s:%d (attempt %d)",
                    self._cfg.server.host,
                    self._cfg.server.port,
                    self._attempt,
                )
                await self.stream_session(stub)
                logger.warning("Stream session ended unexpectedly, reconnecting...")

            except asyncio.CancelledError:
                logger.info("Streamer connect loop cancelled")
                raise

            except CertificateError:
                # Non-recoverable: no point retrying without certs.
                raise

            except Exception as exc:
                logger.error(
                    "Stream session failed (attempt %d): %s: %s",
                    self._attempt,
                    type(exc).__name__,
                    exc,
                )

            finally:
                if channel is not None:
                    await channel.close()

            sleep = self._backoff_sleep()
            logger.info("Reconnecting in %.1fs (attempt %d)", sleep, self._attempt)
            self._drop_counter.increment()
            await asyncio.sleep(sleep)
            self._attempt += 1
            self._reconnect_count += 1

    def _backoff_sleep(self) -> float:
        cfg = self._cfg.streamer
        exponential = cfg.backoff_base * (2**self._attempt)
        capped = min(cfg.backoff_cap, exponential)
        jitter = random.uniform(0, cfg.backoff_jitter)
        return capped + jitter

    async def stream_session(self, stub: syswatch_pb2_grpc.MetricServiceStub) -> None:
        await self._register(stub)
        call = stub.Stream()
        send_task = asyncio.create_task(self._send_loop(call), name="streamer-send")
        rec_task = asyncio.create_task(self._rec_loop(call), name="streamer-receive")

        try:
            done, _ = await asyncio.wait(
                [send_task, rec_task],
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
            raise RuntimeError("Stream task ended unexpectedly without exception")

        finally:
            for task in [send_task, rec_task]:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
            try:
                await call.done_writing()
            except Exception:
                pass

    async def _register(self, stub: syswatch_pb2_grpc.MetricServiceStub) -> None:
        raw_info = self._host_info_collector.collect()  # type: ignore[union-attr]
        host_info = syswatch_pb2.HostInfo(
            hostname=raw_info["hostname"],
            kernel=raw_info["kernel"],
            cpu_model=raw_info["cpu_model"],
            os_name=raw_info["os_name"],
            cpu_cores=raw_info["cpu_cores"],
            cpu_threads=raw_info["cpu_threads"],
            agent_id=self._cfg.agent.agent_id,
        )

        logger.info("Registering agent %s with server...", self._cfg.agent.agent_id)
        try:
            ack = await stub.Register(host_info)
        except grpc.RpcError as exc:
            raise RuntimeError(
                f"Registration failed: {exc.code()}: {exc.details()}"  # type: ignore[union-attr]
            ) from exc

        if not ack.success:
            raise RuntimeError(f"Server rejected registration: {ack.error}")

        self._attempt = 0
        logger.info("Agent registered successfully (server_time=%d)", ack.received_at)

    async def _send_loop(self, call: object) -> None:
        while True:
            try:
                proto_frame = await self._in_queue.get()
                try:
                    await call.write(proto_frame)  # type: ignore[union-attr]
                    self._frames_sent += 1
                    logger.debug(
                        "Streamer sent frame %s (total=%d)",
                        proto_frame.frame_id,  # type: ignore[union-attr]
                        self._frames_sent,
                    )
                except grpc.RpcError as exc:
                    self._drop_counter.increment()
                    logger.error(
                        "Streamer: write failed for frame %s: %s %s",
                        proto_frame.frame_id,  # type: ignore[union-attr]
                        exc.code(),  # type: ignore[union-attr]
                        exc.details(),  # type: ignore[union-attr]
                    )
                    raise
                finally:
                    self._in_queue.task_done()
            except asyncio.CancelledError:
                raise
            except grpc.RpcError:
                raise

    async def _rec_loop(self, call: object) -> None:
        while True:
            try:
                ack = await call.read()  # type: ignore[union-attr]
                if ack == grpc.aio.EOF:
                    logger.warning("Streamer: server closed stream (EOF)")
                    return
                self._ack_received += 1
                if ack.stored:
                    logger.debug(
                        "Ack: frame %s stored at %d", ack.frame_id, ack.received_at
                    )
                else:
                    logger.warning(
                        "Ack: frame %s NOT stored: %s", ack.frame_id, ack.error
                    )
            except asyncio.CancelledError:
                raise
            except grpc.RpcError as exc:
                logger.error(
                    "Streamer: recv failed: %s %s",
                    exc.code(),  # type: ignore[union-attr]
                    exc.details(),  # type: ignore[union-attr]
                )
                raise
