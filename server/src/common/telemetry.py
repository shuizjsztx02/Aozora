"""T-M5-05a: OpenTelemetry integration for traces, metrics, and logs.

Provides:
- ``tracer`` for distributed tracing (exported to Tempo via OTLP)
- ``meter`` for API metrics (exported to Prometheus via OTLP)
- ``logger`` for structured logs (exported to Loki via OTLP)

Integration points:
- Request ID middleware adds request_id as span attribute
- Metrics record API latency, request count, error rate
- Logs are structured JSON with trace context for Loki ingestion
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Global exporters (initialized by setup_telemetry)
_tracer: Optional[trace.Tracer] = None
_meter: Optional[metrics.Meter] = None
_logger: Optional[logging.Logger] = None
_api_metrics: Optional[dict] = None


def setup_telemetry(
    service_name: str = "aozora-api",
    otlp_endpoint: str = "http://otel-collector:4317",
    enable_tracing: bool = True,
    enable_metrics: bool = True,
) -> None:
    """Initialize OpenTelemetry SDK with OTLP exporters.

    Args:
        service_name: Service identifier for telemetry data
        otlp_endpoint: OTLP collector endpoint (gRPC)
        enable_tracing: Enable distributed tracing (Tempo)
        enable_metrics: Enable metrics collection (Prometheus)
    """
    global _tracer, _meter, _logger, _api_metrics

    # Resource identifies this service in telemetry backend
    resource = Resource.create({
        "service.name": service_name,
        "service.version": os.getenv("AOZORA_VERSION", "0.1.0"),
        "deployment.environment": os.getenv("ENV", "development"),
    })

    # Tracing setup (Tempo)
    if enable_tracing:
        trace_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
        trace.set_tracer_provider(tracer_provider)
        _tracer = trace.get_tracer(__name__)

    # Metrics setup (Prometheus)
    if enable_metrics:
        metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True)
        metric_reader = PeriodicExportingMetricReader(
            metric_exporter,
            export_interval_millis=10000,  # Export every 10s
        )
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[metric_reader],
        )
        metrics.set_meter_provider(meter_provider)
        _meter = metrics.get_meter(__name__)
        # Pre-instantiate standard API metrics so they can be recorded per-request
        _api_metrics = create_api_metrics()

    # Logger setup (Loki) - uses standard Python logging with OTLP handler
    # Loki ingestion is handled by Promtail/OTel Collector reading stdout
    _logger = logging.getLogger("aozora.telemetry")


def get_tracer() -> trace.Tracer:
    """Get the global tracer instance."""
    if _tracer is None:
        # Fallback to no-op tracer if not initialized
        return trace.get_tracer(__name__)
    return _tracer


def get_meter() -> metrics.Meter:
    """Get the global meter instance."""
    if _meter is None:
        return metrics.get_meter(__name__)
    return _meter


def get_telemetry_logger() -> logging.Logger:
    """Get the telemetry-aware logger."""
    if _logger is None:
        return logging.getLogger("aozora.telemetry")
    return _logger


# Convenience metrics (created after meter is available)
def create_api_metrics() -> dict:
    """Create standard API metrics instruments.

    Returns:
        Dict of metric instruments for use in request handling
    """
    meter = get_meter()

    instruments: dict = {
        "request_counter": meter.create_counter(
            name="api_requests_total",
            description="Total API requests",
            unit="1",
        ),
        "error_counter": meter.create_counter(
            name="api_errors_total",
            description="Total API errors",
            unit="1",
        ),
        "request_duration": meter.create_histogram(
            name="api_request_duration_ms",
            description="API request duration in milliseconds",
            unit="ms",
        ),
    }

    # ── T-M5-05b: domain-level counters for Grafana dashboards ──────────
    # Each instrument is created once at startup; call sites use the
    # ``record_*`` helpers below, which no-op if telemetry is disabled.

    instruments["sync_push_counter"] = meter.create_counter(
        name="sync_push_total",
        description="Total /sync/push requests",
        unit="1",
    )
    instruments["sync_conflict_counter"] = meter.create_counter(
        name="sync_conflict_total",
        description="Sync mutations that hit a conflict",
        unit="1",
    )
    instruments["sync_queue_depth"] = meter.create_up_down_counter(
        name="sync_queue_depth",
        description="Aggregate pending mutations across clients",
        unit="1",
    )
    instruments["sync_duration"] = meter.create_histogram(
        name="sync_push_duration_ms",
        description="/sync/push latency in milliseconds",
        unit="ms",
    )

    instruments["ai_gateway_counter"] = meter.create_counter(
        name="ai_gateway_requests_total",
        description="Total AI gateway requests (labels: status, degraded, provider)",
        unit="1",
    )
    instruments["ai_gateway_duration"] = meter.create_histogram(
        name="ai_gateway_duration_ms",
        description="AI gateway latency in milliseconds",
        unit="ms",
    )
    instruments["ai_gateway_tokens"] = meter.create_counter(
        name="ai_gateway_tokens_total",
        description="Tokens consumed via AI gateway (labels: kind=input|output)",
        unit="1",
    )

    instruments["crash_counter"] = meter.create_counter(
        name="crash_reports_total",
        description="Crash reports received via POST /telemetry/crash",
        unit="1",
    )
    instruments["active_devices"] = meter.create_up_down_counter(
        name="active_devices_total",
        description="Active device count (reported by clients at startup)",
        unit="1",
    )

    instruments["business_signup"] = meter.create_counter(
        name="business_signup_total",
        description="New user signups",
        unit="1",
    )
    instruments["business_transaction"] = meter.create_counter(
        name="business_transaction_total",
        description="Transactions recorded",
        unit="1",
    )
    instruments["business_export"] = meter.create_counter(
        name="business_export_total",
        description="Data exports generated",
        unit="1",
    )
    instruments["business_d1_retained"] = meter.create_counter(
        name="business_d1_retained_total",
        description="Users retained after 1 day",
        unit="1",
    )
    instruments["business_d7_retained"] = meter.create_counter(
        name="business_d7_retained_total",
        description="Users retained after 7 days",
        unit="1",
    )

    instruments["reconciliation_inconsistency"] = meter.create_counter(
        name="reconciliation_inconsistency_total",
        description="Reconciliation audit mismatches",
        unit="1",
    )
    instruments["duplicate_entry"] = meter.create_counter(
        name="duplicate_entry_total",
        description="Duplicate entries detected (dedup triggered)",
        unit="1",
    )

    return instruments


def record_request(method: str, path: str, status_code: int, duration_ms: float) -> None:
    """Record a completed HTTP request in the API metrics instruments.

    Safe to call before ``setup_telemetry`` runs — it becomes a no-op until
    ``_api_metrics`` is populated, so request handling during early startup
    is not affected.
    """
    if _api_metrics is None:
        return
    labels = {"method": method, "path": path}
    _api_metrics["request_counter"].add(1, {**labels, "status": str(status_code)})
    if status_code >= 500:
        _api_metrics["error_counter"].add(1, labels)
    _api_metrics["request_duration"].record(duration_ms, labels)


# ── T-M5-05b: domain-level recording helpers ────────────────────────────
# All no-op when telemetry is disabled. Call sites in routes.py / service.py
# import these by name so they don't need to know about the underlying
# instrument instances.

def record_sync_push(status: str = "ok", error_class: str = "none", duration_ms: float = 0.0) -> None:
    if _api_metrics is None:
        return
    _api_metrics["sync_push_counter"].add(1, {"status": status, "error_class": error_class})
    if duration_ms:
        _api_metrics["sync_duration"].record(duration_ms, {"status": status})


def record_sync_conflict() -> None:
    if _api_metrics is None:
        return
    _api_metrics["sync_conflict_counter"].add(1)


def record_ai_gateway(
    status: str = "200",
    degraded: bool = False,
    provider: str = "unknown",
    duration_ms: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    if _api_metrics is None:
        return
    labels = {"status": status, "degraded": "true" if degraded else "false", "provider": provider}
    _api_metrics["ai_gateway_counter"].add(1, labels)
    if duration_ms:
        _api_metrics["ai_gateway_duration"].record(duration_ms, {"provider": provider})
    if input_tokens:
        _api_metrics["ai_gateway_tokens"].add(input_tokens, {"provider": provider, "kind": "input"})
    if output_tokens:
        _api_metrics["ai_gateway_tokens"].add(output_tokens, {"provider": provider, "kind": "output"})


def record_crash_report(app_version: str) -> None:
    if _api_metrics is None:
        return
    _api_metrics["crash_counter"].add(1, {"app_version": app_version})


def record_active_device(app_version: str) -> None:
    if _api_metrics is None:
        return
    _api_metrics["active_devices"].add(1, {"app_version": app_version})


def record_signup() -> None:
    if _api_metrics is None:
        return
    _api_metrics["business_signup"].add(1)


def record_transaction() -> None:
    if _api_metrics is None:
        return
    _api_metrics["business_transaction"].add(1)


def record_export() -> None:
    if _api_metrics is None:
        return
    _api_metrics["business_export"].add(1)


def record_d1_retained() -> None:
    """Record a user retained after 1 day.

    TODO(T-M5-05b): wire to a scheduled task that checks D1 retention.
    """
    if _api_metrics is None:
        return
    _api_metrics["business_d1_retained"].add(1)


def record_d7_retained() -> None:
    """Record a user retained after 7 days.

    TODO(T-M5-05b): wire to a scheduled task that checks D7 retention.
    """
    if _api_metrics is None:
        return
    _api_metrics["business_d7_retained"].add(1)


def record_reconciliation_inconsistency(count: int = 1) -> None:
    """Record reconciliation audit mismatches.

    TODO(T-M5-05b): wire to the server-side reconciliation job once
    the reconciliation module is implemented (currently test-only).
    """
    if _api_metrics is None:
        return
    _api_metrics["reconciliation_inconsistency"].add(count)


def record_duplicate_entry(count: int = 1) -> None:
    if _api_metrics is None:
        return
    _api_metrics["duplicate_entry"].add(count)
