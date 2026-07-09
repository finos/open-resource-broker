"""OpenTelemetry bootstrap for the Open Resource Broker.

This module is the single call-site for OTel SDK initialisation.
It is designed to be:

  * **No-op safe** — safe to call even when ``opentelemetry-sdk`` is not
    installed, or when ``otel.enabled`` is ``False`` (the default).
  * **Idempotent** — a second call is silently ignored so that tests or
    re-entrant bootstrap paths cannot accidentally install a second
    MeterProvider / TracerProvider.  The flag is reset by
    ``_reset_telemetry_state()`` which ``DIContainer.reset()`` calls for
    test isolation.
  * **Import-guard style** — the SDK import is wrapped in ``try/except
    ImportError``, matching the silent-skip pattern used elsewhere in
    ``core_services.py`` (the SSE handler guard at the bottom of
    ``create_event_bus``).

MeterProvider construction:
  Multiple metric readers are built from ``otel_config.metrics_exporters``:
    - ``"prometheus"`` → ``PrometheusMetricReader()`` (registers against the
      global ``prometheus_client.REGISTRY``; coexists with the existing
      ``/metrics`` FastAPI route).
    - ``"otlp"``       → ``PeriodicExportingMetricReader(
                              OTLPMetricExporter(endpoint=otlp_endpoint)
                          )``.
    - ``"file"``       → ``PeriodicExportingMetricReader(
                              FileMetricExporter(path=resolved_telemetry_dir)
                          )``.  Required for CLI surfaces where the process
      exits before a Prometheus scrape fires.  All three can be active
      simultaneously.

TracerProvider construction:
  When ``traces_exporter == "otlp"`` a ``BatchSpanProcessor`` with an
  ``OTLPSpanExporter`` is installed.
  When ``traces_exporter == "file"`` a ``BatchSpanProcessor`` with a
  ``FileSpanExporter`` is installed.  Both use a ``TraceIdRatioBased``
  sampler from ``traces_sample_rate``.

Auto-instrumentation (programmatic, NOT via the ``opentelemetry-instrument``
CLI wrapper — ORB is a lib+app on PyPI, embedders may not use the wrapper):
  All guarded ``try/except ImportError`` — no crash if the package is absent.
  All idempotent — ``.instrument()`` is idempotent since 1.20+.
  Wired in ``configure_telemetry``; each can be disabled via OtelConfig:
    - SQLAlchemyInstrumentor (spans)
    - BotocoreInstrumentor  (spans; complements, NOT replaces BotocoreMetricsHandler)
    - ClickInstrumentor     (span per CLI command invocation)
    - SystemMetricsInstrumentor (CPU/mem/GC/thread gauges)
    - LoggingInstrumentor   (inject trace_id/span_id into log records)

Shutdown:
  ``shutdown_telemetry()`` is the public flush-on-exit entrypoint.  It
  calls ``MeterProvider.shutdown()`` (cascades force_flush to all readers)
  and ``TracerProvider.shutdown()``.  It must be called at the end of every
  short-lived process (CLI commands, SDK cleanup) to prevent metrics being
  silently discarded when a ``PeriodicExportingMetricReader`` interval has
  not yet fired.  It is idempotent and safe to call when telemetry was
  never configured.

File exporter path resolution (``telemetry_file_dir``):
  Matches the MetricsCollector 3-tier fallback:
    1. ``otel_config.telemetry_file_dir`` (from config or ORB_TELEMETRY_FILE_DIR).
    2. ``~/.orb/work/telemetry``.
    3. ``tempfile.mkdtemp(prefix="orb-telemetry-")``.

Call site: ``bootstrap/services.py`` — immediately after
``register_core_services(container)`` and before any Meter or CQRS handler
is acquired.

Shutdown wiring:
  - ``Application.cleanup()``           → ``bootstrap/__init__.py``
  - SDK ``ORBClient.cleanup()``          → ``sdk/client.py``
  - CLI entrypoint ``main()``            → ``cli/main.py``
  - MCP server entrypoint               → ``interface/mcp/server/handler.py``
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from orb.infrastructure.di.container import DIContainer

# Module-level idempotency flag.  Set to True after the first successful
# (or intentionally skipped) call to configure_telemetry().
_telemetry_configured: bool = False

# References to the providers created by configure_telemetry().  Held here
# so that shutdown_telemetry() can call shutdown() on them without the caller
# needing to pass them explicitly.
_meter_provider: Optional[object] = None
_tracer_provider: Optional[object] = None

# Flag to prevent double-shutdown (shutdown_telemetry() is idempotent).
_telemetry_shutdown: bool = False


def _reset_telemetry_state() -> None:
    """Reset the idempotency flag so the next call re-runs configuration.

    Called by ``DIContainer.reset()`` so each test starts with a clean state.
    Also clears the provider references and the shutdown flag.
    """
    global _telemetry_configured, _meter_provider, _tracer_provider, _telemetry_shutdown
    _telemetry_configured = False
    _meter_provider = None
    _tracer_provider = None
    _telemetry_shutdown = False


def _resolve_telemetry_file_dir(configured: Optional[str]) -> Path:
    """Resolve the telemetry file directory using a 3-tier fallback.

    Tier 1: ``configured`` value (from OtelConfig or ORB_TELEMETRY_FILE_DIR env).
    Tier 2: ``~/.orb/work/telemetry``.
    Tier 3: ``tempfile.mkdtemp(prefix="orb-telemetry-")``.

    Always returns a ``Path`` that exists and is writable.
    """
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.append(Path.home() / ".orb" / "work" / "telemetry")

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            # Quick write-access probe
            (candidate / ".probe").touch()
            (candidate / ".probe").unlink(missing_ok=True)
            return candidate
        except (PermissionError, OSError):
            continue

    return Path(tempfile.mkdtemp(prefix="orb-telemetry-"))


def configure_telemetry(container: "DIContainer") -> None:  # noqa: C901
    """Initialise the OTel SDK from the container's OtelConfig.

    This function is a complete no-op in any of the following situations:
      1. Already called once (idempotency guard).
      2. ``opentelemetry-sdk`` is not installed (guarded ``ImportError``).
      3. ``otel_config.enabled`` is ``False`` (the default).

    When enabled and the SDK is present, it:
      - Builds a ``MeterProvider`` with all configured metric readers.
      - Installs it as the global OTel metrics provider.
      - Builds a ``TracerProvider`` with a ratio sampler and (optionally) an
        OTLP or file span exporter.
      - Installs it as the global OTel tracer provider.
      - Wires enabled auto-instrumentors (SQLAlchemy, botocore, click,
        system-metrics, logging) via programmatic ``.instrument()`` calls.

    Args:
        container: The application DI container (used to resolve AppConfig).
    """
    global _telemetry_configured, _meter_provider, _tracer_provider

    if _telemetry_configured:
        return

    # Mark as configured regardless of what happens below, so we never
    # attempt a partial second initialisation on re-entry.
    _telemetry_configured = True

    # --- guard: SDK may not be installed ---
    try:
        from opentelemetry import metrics, trace  # noqa: F401 (availability probe)
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        # opentelemetry-sdk not installed; silently skip (no-op default).
        return

    # --- resolve OtelConfig from the container ---
    try:
        from orb.config.schemas.app_schema import AppConfig
        from orb.domain.base.ports.configuration_port import ConfigurationPort

        config_port = container.get(ConfigurationPort)
        app_config: AppConfig = config_port.get_typed(AppConfig)  # type: ignore[arg-type]
        otel_config = app_config.observability
    except Exception:
        # Config resolution failed; stay no-op rather than crash.
        return

    if not otel_config.enabled:
        return

    resource = Resource.create({SERVICE_NAME: otel_config.service_name})

    # --- file dir (resolved lazily only if "file" exporter is requested) ---
    _file_dir: Optional[Path] = None

    def _get_file_dir() -> Path:
        nonlocal _file_dir
        if _file_dir is None:
            _file_dir = _resolve_telemetry_file_dir(otel_config.telemetry_file_dir)
        return _file_dir

    # --- MeterProvider ---
    metric_readers: list[object] = []

    for exporter_name in otel_config.metrics_exporters:
        if exporter_name == "prometheus":
            try:
                from opentelemetry.exporter.prometheus import (  # type: ignore[import-not-found]
                    PrometheusMetricReader,
                )

                metric_readers.append(PrometheusMetricReader())
            except ImportError:
                pass  # opentelemetry-exporter-prometheus not installed; skip.

        elif exporter_name == "otlp":
            try:
                from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (  # type: ignore[import-not-found]
                    OTLPMetricExporter,
                )
                from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

                kwargs: dict = {}
                if otel_config.otlp_endpoint:
                    kwargs["endpoint"] = otel_config.otlp_endpoint
                metric_readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(**kwargs)))
            except ImportError:
                pass  # OTLP exporter not installed; skip.

        elif exporter_name == "file":
            try:
                from opentelemetry.exporter.otlp.json.file import (  # type: ignore[import-not-found]
                    FileMetricExporter,
                )
                from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

                metrics_path = _get_file_dir() / "metrics.jsonl"
                metric_readers.append(
                    PeriodicExportingMetricReader(FileMetricExporter(path=metrics_path))
                )
            except ImportError:
                # opentelemetry-exporter-otlp-json-file not installed; skip.
                # NOTE: this package (opentelemetry-exporter-otlp-json-file 0.64b0)
                # has a transitive dependency on opentelemetry-proto-json==0.64b0
                # which is not yet published to PyPI (July 2026).  The guard here
                # ensures ORB continues to function without it.  Add the dep to
                # [monitoring] once proto-json is available on PyPI.
                pass

    meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)  # type: ignore[arg-type]
    metrics.set_meter_provider(meter_provider)
    _meter_provider = meter_provider

    # --- TracerProvider ---
    tracer_provider = None
    try:
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

        sampler = TraceIdRatioBased(otel_config.traces_sample_rate)
        tracer_provider = TracerProvider(resource=resource, sampler=sampler)

        if otel_config.traces_exporter == "otlp":
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import-not-found]
                    OTLPSpanExporter,
                )
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                exporter_kwargs: dict = {}
                if otel_config.otlp_endpoint:
                    exporter_kwargs["endpoint"] = otel_config.otlp_endpoint
                tracer_provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(**exporter_kwargs))
                )
            except ImportError:
                pass  # OTLP span exporter not installed; skip.

        elif otel_config.traces_exporter == "file":
            try:
                from opentelemetry.exporter.otlp.json.file import (  # type: ignore[import-not-found]
                    FileSpanExporter,
                )
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                traces_path = _get_file_dir() / "traces.jsonl"
                tracer_provider.add_span_processor(
                    BatchSpanProcessor(FileSpanExporter(path=traces_path))
                )
            except ImportError:
                # opentelemetry-exporter-otlp-json-file not yet pip-installable
                # (transitive dep on opentelemetry-proto-json==0.64b0 not on PyPI).
                pass

        trace.set_tracer_provider(tracer_provider)
        _tracer_provider = tracer_provider
    except ImportError:
        pass  # Trace SDK not available; metrics-only setup still active.

    # --- Auto-instrumentation (programmatic, guarded, idempotent) ---

    # LoggingInstrumentor: wire first so trace_id/span_id are injected into
    # log records from the very first log statement after bootstrap.
    if otel_config.instrument_logging:
        try:
            from opentelemetry.instrumentation.logging import (  # type: ignore[import-not-found]
                LoggingInstrumentor,
            )

            LoggingInstrumentor().instrument()
        except ImportError:
            pass  # opentelemetry-instrumentation-logging not installed; skip.

    # SQLAlchemyInstrumentor: wire before engines are built (bootstrap order).
    if otel_config.instrument_sqlalchemy:
        try:
            from opentelemetry.instrumentation.sqlalchemy import (  # type: ignore[import-not-found]
                SQLAlchemyInstrumentor,
            )

            SQLAlchemyInstrumentor().instrument()
        except ImportError:
            pass  # opentelemetry-instrumentation-sqlalchemy not installed; skip.

    # BotocoreInstrumentor: spans for boto3/botocore calls.  Complements
    # BotocoreMetricsHandler (which emits classified error/throttle metrics);
    # does NOT replace it.
    if otel_config.instrument_botocore:
        try:
            from opentelemetry.instrumentation.botocore import (  # type: ignore[import-not-found]
                BotocoreInstrumentor,
            )

            BotocoreInstrumentor().instrument()
        except ImportError:
            pass  # opentelemetry-instrumentation-botocore not installed; skip.

    # ClickInstrumentor: span per Click CLI command invocation.
    if otel_config.instrument_click:
        try:
            from opentelemetry.instrumentation.click import (  # type: ignore[import-not-found]
                ClickInstrumentor,
            )

            ClickInstrumentor().instrument()
        except ImportError:
            pass  # opentelemetry-instrumentation-click not installed; skip.

    # SystemMetricsInstrumentor: OS-level CPU/memory/GC/thread gauges.
    # Replaces the dead memory_usage_bytes/cpu_usage_percent gauges from
    # the homegrown MetricsCollector stack.
    if otel_config.instrument_system_metrics:
        try:
            from opentelemetry.instrumentation.system_metrics import (  # type: ignore[import-not-found]
                SystemMetricsInstrumentor,
            )

            SystemMetricsInstrumentor().instrument()
        except ImportError:
            pass  # opentelemetry-instrumentation-system-metrics not installed; skip.


def shutdown_telemetry() -> None:
    """Flush and shut down the OTel providers created by configure_telemetry().

    **This must be called at process exit for all short-lived surfaces** (CLI
    commands, SDK client cleanup, MCP server shutdown).  ``MeterProvider.shutdown()``
    cascades ``force_flush()`` to every reader, draining pending metrics data
    synchronously before the process terminates.  Without it, metrics recorded
    since the last ``PeriodicExportingMetricReader`` tick are silently discarded.

    This function is:
      - **Idempotent**: safe to call multiple times; subsequent calls return
        without doing work.
      - **Safe-when-unconfigured**: no-op if ``configure_telemetry()`` was
        never called, if the SDK is absent, or if telemetry is disabled.
      - **Never raises**: exceptions from the SDK shutdown calls are swallowed
        so that process teardown is never interrupted.
    """
    global _telemetry_shutdown, _meter_provider, _tracer_provider

    if _telemetry_shutdown:
        return

    _telemetry_shutdown = True

    if _meter_provider is not None:
        try:
            _meter_provider.shutdown()  # type: ignore[union-attr]
        except Exception:
            pass  # Shutdown errors must never interrupt process teardown.

    if _tracer_provider is not None:
        try:
            _tracer_provider.shutdown()  # type: ignore[union-attr]
        except Exception:
            pass  # Shutdown errors must never interrupt process teardown.
