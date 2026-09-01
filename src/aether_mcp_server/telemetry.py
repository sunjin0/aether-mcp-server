"""Optional standard OpenTelemetry tracing for the MCP HTTP boundary."""
import os
from typing import Any

from opentelemetry import trace
from opentelemetry.context import attach, detach
from opentelemetry.propagate import extract
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry._logs import set_logger_provider
import logging

_trace_provider: TracerProvider | None = None
_log_provider: LoggerProvider | None = None


def configure_tracing() -> None:
    global _trace_provider, _log_provider
    endpoint = os.getenv("AETHER_OTLP_TRACES_URL", "").strip()
    if os.getenv("AETHER_OTLP_TRACES_ENABLED", "false").lower() not in {"1", "true", "yes", "on"} or not endpoint:
        return
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "aether-mcp-server")}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _trace_provider = provider
    log_provider = LoggerProvider(resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "aether-mcp-server")}))
    logs_endpoint = os.getenv("AETHER_OTLP_LOGS_URL", "").strip()
    if not logs_endpoint:
        logs_endpoint = endpoint.replace("/v1/traces", "/v1/logs")
    log_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=logs_endpoint)))
    set_logger_provider(log_provider)
    _log_provider = log_provider
    logging.getLogger().addHandler(LoggingHandler(level=logging.NOTSET, logger_provider=log_provider))


def shutdown_tracing() -> None:
    """Flush exporters during graceful process shutdown without affecting disabled mode."""
    if _trace_provider is not None:
        _trace_provider.shutdown()
    if _log_provider is not None:
        _log_provider.shutdown()


class OTelMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.tracer = trace.get_tracer("aether.mcp.http")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        carrier = {"traceparent": headers.get("traceparent", "")} if headers.get("traceparent") else {}
        token = attach(extract(carrier))
        try:
            with self.tracer.start_as_current_span("mcp.http", attributes={
                "http.request.method": scope.get("method", ""),
                "url.path": scope.get("path", ""),
            }) as span:
                async def traced_send(message: dict[str, Any]) -> None:
                    if message.get("type") == "http.response.start":
                        span.set_attribute("http.response.status_code", message.get("status", 0))
                    await send(message)
                await self.app(scope, receive, traced_send)
        finally:
            detach(token)
