from __future__ import annotations

import logging

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)

logger = logging.getLogger(__name__)
DEFAULT_METRICS_PORT: int = 9091
DB_FLUSH_LATENCY_BUCKETS: tuple[float, ...] = (
    0.001,
    0.005,
    0.010,
    0.025,
    0.050,
    0.100,
    0.250,
    0.500,
    1.000,
    2.500,
    5.000,
)


class MetricsRegistry:
    def __init__(self) -> None:

        self.frames_received: Counter = Counter(
            "syswatch_frames_received_total",
            "Total MetricFrames received from agents via gRPC Stream().",
            ["agent_id"],
        )

        self.agents_connected: Gauge = Gauge(
            "syswatch_agents_connected",
            "Number of agents currently streaming metrics via gRPC.",
        )

        self.grpc_register_total: Counter = Counter(
            "syswatch_grpc_register_total",
            "Total gRPC Register() calls, labelled by outcome.",
            ["status"],  # "success" | "error"
        )
        self.agent_frames_dropped_total: Counter = Counter(
            "syswatch_agent_frames_dropped_total",
            "Total frames agents reported dropping (MetricFrame.frames_dropped_since_last_connect).",
            ["agent_id"],
        )

        self.db_flush_latency: Histogram = Histogram(
            "syswatch_db_flush_latency_seconds",
            "Duration of each BufferedWriter COPY flush to TimescaleDB.",
            buckets=DB_FLUSH_LATENCY_BUCKETS,
        )

        self.db_flush_errors_total: Counter = Counter(
            "syswatch_db_flush_errors_total",
            "Total failed TimescaleDB COPY flush operations (each = data loss).",
        )
        self.db_frames_written_total: Counter = Counter(
            "syswatch_db_frames_written_total",
            "Total MetricFrames successfully written to TimescaleDB.",
        )

        logger.info(
            "MetricsRegistry initialised: %d metrics registered",
            7,
        )

    def record_register(self, success: bool) -> None:
        status = "success" if success else "error"
        self.grpc_register_total.labels(status=status).inc()

    def record_agent_drops(self, agent_id: str, count: int) -> None:
        if count > 0:
            self.agent_frames_dropped_total.labels(agent_id=agent_id).inc(count)

    def record_flush(
        self, latency_seconds: float, frames_written: int, error: bool
    ) -> None:

        self.db_flush_latency.observe(latency_seconds)
        if error:
            self.db_flush_errors_total.inc()
        else:
            self.db_frames_written_total.inc(frames_written)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP server
# ─────────────────────────────────────────────────────────────────────────────


def start_metrics_server(port: int = DEFAULT_METRICS_PORT) -> None:
    start_http_server(port)
    logger.info(
        port,
        port,
    )
