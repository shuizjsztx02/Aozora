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
from datetime import UTC, datetime
from typing import Optional

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# T-M5-05ab fix (S1-1): OTLP log exporter — pushes Python logs to OTel Collector
# → Loki. Imported lazily inside setup_telemetry so startup doesn't fail if the
# log SDK is unavailable (the opentelemetry-exporter-otlp package bundles it).
# T-M5-05ab fix (N-1): class name corrected — the actual class in
# opentelemetry-exporter-otlp is OTLPLogExporter, not OTLPLogHandler.
_OTLPLogExporter = None  # populated by setup_telemetry
try:
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
        OTLPLogExporter as _OTLPLogExporterCls,
    )
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    _OTLPLogExporter = _OTLPLogExporterCls
except ImportError:  # pragma: no cover — exporter-otlp ships these, fallback is no-op
    pass

# Global exporters (initialized by setup_telemetry)
_tracer: Optional[trace.Tracer] = None
_meter: Optional[metrics.Meter] = None
_logger: Optional[logging.Logger] = None
_api_metrics: Optional[dict] = None

# T-M5-05ab fix (S1-4): track unique device_id_hash → (last-seen timestamp, app_version)
# so the observable gauge can report "devices seen in the last 24h".
# T-M5-05ab fix (N-5): app_version is retained so the Grafana "Version
# Distribution" panel can break down active devices by client version.
_active_devices: dict[str, tuple[float, str]] = {}
_ACTIVE_DEVICE_WINDOW_SECONDS = 86400


def _observe_active_devices(_options):
    """ObservableGauge callback — one Observation per device active in 24h.

    T-M5-05ab fix (N-5): emits ``app_version`` as a label so Grafana can
    show a version-distribution breakdown. The sum of all observations
    equals the total active device count (CrashFreeRateLow denominator).
    """
    if not _active_devices:
        return
    now = datetime.now(UTC).timestamp()
    cutoff = now - _ACTIVE_DEVICE_WINDOW_SECONDS
    # Opportunistic prune (avoids unbounded growth).
    stale = [k for k, (t, _) in _active_devices.items() if t <= cutoff]
    for k in stale:
        _active_devices.pop(k, None)
    from opentelemetry.metrics import Observation
    for device_id, (_ts, app_version) in _active_devices.items():
        yield Observation(1, {"device_id": device_id, "app_version": app_version})


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

    # T-M5-05ab fix (S1-1 + N-1): attach OTLP log exporter so application logs
    # flow through API → OTel Collector → Loki. Without this, the collector's
    # logs pipeline has no producer and Loki stays empty.
    if _OTLPLogExporter is not None:
        log_exporter = _OTLPLogExporter(endpoint=otlp_endpoint, insecure=True)
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        set_logger_provider(logger_provider)
        otel_handler = LoggingHandler(
            level=logging.NOTSET,
            logger_provider=logger_provider,
        )
        # Attach to the root "aozora" logger so all aozora.* modules are captured.
        logging.getLogger("aozora").addHandler(otel_handler)


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
    # T-M5-05ab fix (S1-5): sync_queue_depth is a Gauge reporting the latest
    # pending-mutations snapshot provided by clients in PushRequest.pending_count.
    # Previously an UpDownCounter with no writer, the metric stayed at 0 forever.
    instruments["sync_queue_depth"] = meter.create_gauge(
        name="sync_queue_depth",
        description="Aggregate pending mutations across clients (latest report)",
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
    # T-M5-05ab fix (S1-4): active_devices_total is now a Gauge reporting the
    # count of unique devices seen in the last 24h. Previously an UpDownCounter
    # that incremented on every heartbeat, which made CrashFreeRateLow divide a
    # 24h window by a cumulative counter (mathematically wrong).
    # The observable callback reads from TelemetryService._active_devices below.
    instruments["active_devices"] = meter.create_observable_gauge(
        name="active_devices_total",
        description="Unique devices with a heartbeat in the last 24h",
        callbacks=[_observe_active_devices],
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
    # D1/D7 retention is computed nightly (00:20+ UTC) by
    # ``src.modules.telemetry.retention``. Gauges (not counters) so a process
    # restart re-sets the same value instead of double counting.
    instruments["business_d1_retained"] = meter.create_gauge(
        name="business_d1_retained_count",
        description="D1-retained users for the latest completed UTC day (set nightly by retention job)",
        unit="1",
    )
    instruments["business_d1_retention_rate"] = meter.create_gauge(
        name="business_d1_retention_rate",
        description="D1 retention percentage for the latest completed UTC day (set nightly by retention job)",
        unit="percent",
    )
    instruments["business_d7_retained"] = meter.create_gauge(
        name="business_d7_retained_count",
        description="D7-retained users for the latest completed UTC day (set nightly by retention job)",
        unit="1",
    )
    instruments["business_d7_retention_rate"] = meter.create_gauge(
        name="business_d7_retention_rate",
        description="D7 retention percentage for the latest completed UTC day (set nightly by retention job)",
        unit="percent",
    )

    instruments["reconciliation_inconsistency"] = meter.create_counter(
        name="reconciliation_inconsistency_total",
        description="Reconciliation audit mismatches — no caller yet; alert commented out in alert-rules.yml (T-M5-05b TODO, depends on reconciliation module)",
        unit="1",
    )
    instruments["feedback"] = meter.create_counter(
        name="feedback_total",
        description="Beta feedback submissions via POST /telemetry/feedback",
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


def record_sync_queue_depth(pending_count: int, user_id: str | None = None) -> None:
    """T-M5-05ab fix (S1-5): report the latest pending-mutations count.

    sync_queue_depth is a Gauge, so ``set`` records the current absolute value;
    Prometheus scrapes the last-set value per scrape window. Labels include
    ``user_id`` (when available) so `sum(sync_queue_depth)` aggregates across
    all clients for the SyncBacklogHigh alert.
    """
    if _api_metrics is None:
        return
    if pending_count >= 0:
        labels = {"user_id": user_id or "unknown"}
        _api_metrics["sync_queue_depth"].set(pending_count, labels)


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


def record_active_device(app_version: str, device_id_hash: str | None = None) -> None:
    """Mark a device as active (last-seen = now).

    T-M5-05ab fix (S1-4): when ``device_id_hash`` is provided, the per-device
    timestamp is stored so the ObservableGauge can report unique-device count.
    T-M5-05ab fix (N-5): ``app_version`` is stored alongside the timestamp
    so the gauge callback can emit it as a label for the Version Distribution
    panel.
    """
    if _api_metrics is None:
        return
    if device_id_hash:
        _active_devices[device_id_hash] = (datetime.now(UTC).timestamp(), app_version)


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


def set_d1_retention(count: int, rate_pct: float) -> None:
    """Set D1 retained-user count and retention rate (percent).

    Written nightly by ``src.modules.telemetry.retention``. Gauges are
    overwritten, so re-running the job (e.g. after a restart) is idempotent.
    """
    if _api_metrics is None:
        return
    _api_metrics["business_d1_retained"].set(count)
    _api_metrics["business_d1_retention_rate"].set(rate_pct)


def set_d7_retention(count: int, rate_pct: float) -> None:
    """Set D7 retained-user count and retention rate (percent).

    See :func:`set_d1_retention` for the idempotency note.
    """
    if _api_metrics is None:
        return
    _api_metrics["business_d7_retained"].set(count)
    _api_metrics["business_d7_retention_rate"].set(rate_pct)


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


def record_feedback() -> None:
    """Record a beta feedback submission via ``POST /telemetry/feedback``."""
    if _api_metrics is None:
        return
    _api_metrics["feedback"].add(1)
