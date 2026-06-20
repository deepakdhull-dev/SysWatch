from __future__ import annotations

import asyncio
import logging
import random
import time

import grpc

from .config import Config
from .proto import *
from .sampler import DropCounter

logger = logging.getLogger(__name__)


class Streamer:
    def __init__(self, cfg, drop_counter, in_queue, host_info_collector):
        self._cfg = cfg
        self._in_queue = in_queue
        self._host_info_collector = host_info_collector
        self._drop_counter = drop_counter

        self._attempt = 0

        self._frames_sent = 0
        self._ack_received = 0
        self._reconnect_count = 0

    async def start(self):
        self._task = asyncio.create_task(
            self.connect_loop(), name="streamer-connect-loop"
        )
        logger.info(f"Streamer connected")

    async def stop(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            logger.info(
                f"Streamer stopped. sent {self._frames_sent} frames. acks:{self._ack_received}. Reconnects: {self._reconnect_count}"
            )

    def build_channel(self):
        certs = self._cfg.certs
        for path, label in [
            (certs.ca_cert, "CA cert"),
            (certs.client_cert, "client cert"),
            (certs.client_key, "client key"),
        ]:
            try:
                open(path).close()
            except FileNotFoundError:
                raise FileNotFoundError(
                    f"mTLS {label} not found at {path}. Generate certs and place them in /etc/syswatch/certs/."
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
        logger.debug(f"building mTLS channel to {target}")
        return grpc.aio.secure_channel(target, credentials)

    async def connect_loop(self):
        while True:
            channel = None
            try:
                channel = self.build_channel()
                stub = syswatch_pb2_grpc.MetricServiceStub(channel)
                logger.info(
                    f"connecting to {self._cfg.server.host}:{self._cfg.server.port} (attempt: {self._attempt})"
                )
                await self.stream_session(stub)
                logger.warning("Stream session ended unexpectedly, reconnecting...")
            except asyncio.CancelledError:
                logger.info("Streamer connect loop cancelled")
                raise

            except Exception as e:
                logger.error(
                    f"Stream session failed (attempt:{self._attempt}): {type(e).__name__}: {e}"
                )

            finally:
                if channel is not None:
                    await channel.close()
            sleep = self.backoff_sleep()
            logger.info(f"reconnecting in {sleep} s (attempy: {self._attempt})")
            self._drop_counter.increment()
            await asyncio.sleep(sleep)
            self._attempt += 1
            self._reconnect_count += 1

    def backoff_sleep(self):
        cfg = self._cfg.streamer
        exponential = cfg.backoff_base * (2**self._attempt)
        capped = min(cfg.backoff_cap, exponential)
        jitter = random.uniform(0, cfg.backoff_jitter)
        return capped + jitter

    async def stream_session(self, stub):
        await self.register(stub)
        call = stub.Stream()
        send_task = asyncio.create_task(self.send_loop(call), name="streamer-send")
        rec_task = asyncio.create_task(self.rec_loop(call), name="streamer-receive")

        try:
            done, pending = await asyncio.wait(
                [send_task, rec_task],
                return_when=asyncio.FIRST_EXCEPTION,
            )

            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc

            raise RuntimeError("Stream task ended unexpectedly")

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

    async def register(self, stub):
        raw_info = self._host_info_collector.collect()
        host_info = syswatch_pb2.HostInfo(
            hostname=raw_info["hostname"],
            kernel=raw_info["kernel"],
            cpu_model=raw_info["cpu_model"],
            os_name=raw_info["os_name"],
            cpu_cores=raw_info["cpu_cores"],
            cpu_threads=raw_info["cpu_threads"],
            agent_id=self._cfg.agent.agent_id,
        )

        logger.info(f"registering agent {self._cfg.agent.agent_id} with server...")
        try:
            ack = await stub.Register(host_info)
        except grpc.RpcError as e:
            raise RuntimeError(f"Registration failed: {e.code()}: {e.details()}") from e
        if not ack.success:
            raise RuntimeError(f"Server rejected registration: {ack.error}")
        self._attempt = 0
        logger.info(f"Agent registered successfully (server_time={ack.received_at})")

    async def send_loop(self, call):
        while True:
            try:
                proto_frame = await self._in_queue.get()
                try:
                    await call.write(proto_frame)
                    self._frames_sent += 1
                    logger.debug(
                        f"streamer sent frame {proto_frame.frame_id} (total sent: {self._frames_sent}"
                    )

                except grpc.RpcError as e:
                    self._drop_counter.increment()
                    logger.error(
                        f"Streamer: write failed for frame {proto_frame.frame_id} : {e.code()} {e.details()}"
                    )
                    raise
                finally:
                    self._in_queue.task_done()
            except asyncio.CancelledError:
                raise
            except grpc.RpcError:
                raise

    async def rec_loop(self, call):
        while True:
            try:
                ack = await call.read()
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
                logger.error("Streamer: recv failed: %s %s", exc.code(), exc.details())
                raise
