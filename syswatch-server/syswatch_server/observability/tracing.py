from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

_provider: TracerProvider | None = None


def setup_tracing(cfg: Any) -> trace.Tracer:
    global _provider

    if not cfg.tracing.enabled:
        logger.info("Tracing disabled (cfg.tracing.enabled=False)")
        return trace.get_tracer("syswatch_server")

    resource = Resource.create(
        {
            SERVICE_NAME: cfg.tracing.service_name,
            "service.version": "0.1.0",
            "deployment.environment": "production",
        }
    )
    _provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(_provider)

    exporter = OTLPSpanExporter(
        endpoint=cfg.tracing.otlp_endpoint,
        insecure=True,
    )
    processor = BatchSpanProcessor(exporter)
    _provider.add_span_processor(processor)

    AsyncPGInstrumentor().instrument(sanitize_query=True)

    logger.info(
        "OTel tracing configured: service=%r endpoint=%s",
        cfg.tracing.service_name,
        cfg.tracing.otlp_endpoint,
    )

    return trace.get_tracer("syswatch_server")


def instrument_fastapi(app: Any) -> None:
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="metrics,health",
    )
    logger.info("FastAPI auto-instrumentation applied")


def shutdown_tracing() -> None:

    global _provider

    if _provider is None:
        return

    logger.info("Flushing OTel spans before shutdown...")

    try:
        _provider.force_flush(timeout_millis=5000)
        _provider.shutdown()
        logger.info("OTel TracerProvider shut down cleanly")

    except Exception as exc:
        logger.error("OTel shutdown error (non-fatal): %s", exc)

    finally:
        _provider = None


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)
