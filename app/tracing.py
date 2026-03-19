"""OpenTelemetry tracing setup for FastAPI."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import get_settings

logger = logging.getLogger(__name__)


def setup_tracing(app: FastAPI) -> None:
    """Initialize OpenTelemetry tracing and instrument FastAPI."""
    settings = get_settings()
    if not settings.enable_tracing:
        logger.info("Tracing disabled by configuration")
        return

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": settings.app_version,
            "deployment.environment": "prod",
        }
    )

    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    if settings.otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("Tracing enabled with OTLP exporter")
    else:
        logger.info("Tracing enabled without exporter (local spans only)")

    FastAPIInstrumentor.instrument_app(app)
