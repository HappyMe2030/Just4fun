"""
OpenTelemetry setup.

The backend endpoint is fully configurable via the standard OTel env vars,
so no custom "endpoint" config is needed in the app itself:

  OTEL_EXPORTER_OTLP_ENDPOINT   e.g. https://otel-collector.example.com:4318
  OTEL_EXPORTER_OTLP_HEADERS    e.g. api-key=xxxx  (if your backend needs auth)
  OTEL_SERVICE_NAME             defaults to "3dprint-marketplace" below if unset

If OTEL_EXPORTER_OTLP_ENDPOINT is not set, tracing is skipped entirely so the
app still runs fine without a tracing backend configured.
"""
import os


def init_tracing(app, db_module):
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        app.logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set; tracing disabled")
        return

    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.instrumentation.flask import FlaskInstrumentor
    from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor

    service_name = os.environ.get("OTEL_SERVICE_NAME", "3dprint-marketplace")
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    # OTLPSpanExporter reads OTEL_EXPORTER_OTLP_ENDPOINT / _HEADERS itself
    # when not passed explicitly, so the backend stays fully env-configurable.
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    FlaskInstrumentor().instrument_app(app)
    Psycopg2Instrumentor().instrument(enable_commenter=True)

    app.logger.info("OpenTelemetry tracing enabled, exporting to %s", endpoint)
