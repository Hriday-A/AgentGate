"""OpenTelemetry tracing for runs, tasks, agent nodes, tools and model calls.

Off by default (spans are no-ops). Enable by pointing at an OTLP collector, e.g. the Jaeger in
docker-compose:  OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
or print spans to the terminal with AGENTGATE_TRACE_CONSOLE=1.
"""
from __future__ import annotations

import atexit
import os

from opentelemetry import trace

tracer = trace.get_tracer("agentgate")
_configured = False


def setup(service_name: str = "agentgate-runner") -> bool:
    """Install an exporting tracer provider if configured. Returns whether tracing is on."""
    global _configured
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    console = os.environ.get("AGENTGATE_TRACE_CONSOLE")
    if _configured or not (endpoint or console):
        return _configured

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    if console:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    atexit.register(provider.shutdown)  # flush buffered spans on exit
    _configured = True
    return True
