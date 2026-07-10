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
  3-tier fallback:
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
from typing import TYPE_CHECKING, Optional, TextIO

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

# File handles for the SDK-native ConsoleMetricExporter / ConsoleSpanExporter
# fallbacks when the dedicated OTLP-JSON-file exporter is not installed.
# Stored here so they can be closed deterministically in shutdown_telemetry()
# and _reset_telemetry_state() rather than leaking for the process lifetime.
_metrics_file_handle: Optional[TextIO] = None
_traces_file_handle: Optional[TextIO] = None

# Flag to prevent double-shutdown (shutdown_telemetry() is idempotent).
_telemetry_shutdown: bool = False


def _reset_telemetry_state() -> None:
    """Reset the idempotency flag so the next call re-runs configuration.

    Called by ``DIContainer.reset()`` so each test starts with a clean state.
    Also clears the provider references, the shutdown flag, and closes any
    open file handles so they are not leaked across test boundaries.
    """
    global \
        _telemetry_configured, \
        _meter_provider, \
        _tracer_provider, \
        _telemetry_shutdown, \
        _metrics_file_handle, \
        _traces_file_handle
    # Close file handles before clearing references so the OS releases the
    # descriptors.  Guarded against double-close (e.g. if shutdown_telemetry()
    # already ran) — closing an already-closed file is a no-op here because we
    # null the reference immediately after.
    if _metrics_file_handle is not None:
        try:
            _metrics_file_handle.close()
        except Exception:
            pass  # Closing errors must not prevent state reset.
        _metrics_file_handle = None
    if _traces_file_handle is not None:
        try:
            _traces_file_handle.close()
        except Exception:
            pass  # Closing errors must not prevent state reset.
        _traces_file_handle = None
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
    # attempt a partial second initialisation on re-entry.  (Read back in the
    # re-entry guard above and in _reset_telemetry_state; the flow analyser
    # does not see the cross-function read.)
    _telemetry_configured = True  # lgtm[py/unused-global-variable]

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
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

            metrics_path = _get_file_dir() / "metrics.jsonl"
            try:
                # Preferred: dedicated OTLP-JSON file exporter.
                # IMPORTANT: opentelemetry-exporter-otlp-json-file==0.64b0
                # transitively requires opentelemetry-proto-json==0.64b0, which
                # is NOT published on PyPI (verified July 2026).  The
                # ImportError guard below activates the SDK-native fallback
                # until that package ships.  To upgrade: add
                # "opentelemetry-exporter-otlp-json-file>=0.64b0,<1.0" to the
                # [monitoring] extra in pyproject.toml once proto-json is on
                # PyPI, and this guard will transparently prefer it.
                from opentelemetry.exporter.otlp.json.file import (  # type: ignore[import-not-found]
                    FileMetricExporter,
                )

                _metric_file_exporter: object = FileMetricExporter(path=metrics_path)
            except ImportError:
                # SDK-native fallback: ConsoleMetricExporter redirected to a
                # file handle with a compact (single-line) JSON formatter.
                # ConsoleMetricExporter is part of opentelemetry-sdk (already a
                # required dep) and calls out.flush() on every export(), so
                # data is durably written before shutdown_telemetry() returns.
                # Format: one JSON object per line (JSONL) — readable by any
                # standard JSON Lines tooling.
                # The handle is stored in _metrics_file_handle so that
                # shutdown_telemetry() and _reset_telemetry_state() can close
                # it deterministically, preventing a resource leak.
                from opentelemetry.sdk.metrics.export import ConsoleMetricExporter

                global _metrics_file_handle
                _metrics_fh = open(  # noqa: SIM115,WPS515
                    metrics_path, "a", encoding="utf-8"
                )
                try:
                    _metric_file_exporter = ConsoleMetricExporter(
                        out=_metrics_fh,
                        formatter=lambda md: md.to_json(indent=None) + "\n",
                    )
                except Exception:
                    # Exporter construction failed: close the handle we just
                    # opened so a partial init cannot leak a descriptor.
                    _metrics_fh.close()
                    raise
                # Only retain the handle for lifecycle close once the exporter
                # owns it successfully.
                _metrics_file_handle = _metrics_fh

            metric_readers.append(
                PeriodicExportingMetricReader(_metric_file_exporter)  # type: ignore[arg-type]
            )

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
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            traces_path = _get_file_dir() / "traces.jsonl"
            try:
                # Preferred: dedicated OTLP-JSON file exporter.
                # See the metrics "file" branch above for the note on why
                # opentelemetry-exporter-otlp-json-file is guarded here.
                from opentelemetry.exporter.otlp.json.file import (  # type: ignore[import-not-found]
                    FileSpanExporter,
                )

                _span_file_exporter: object = FileSpanExporter(path=traces_path)
            except ImportError:
                # SDK-native fallback: ConsoleSpanExporter redirected to a
                # file handle with a compact (single-line) JSON formatter.
                # Each span is exported as one JSON object on its own line.
                # The handle is stored in _traces_file_handle so that
                # shutdown_telemetry() and _reset_telemetry_state() can close
                # it deterministically, preventing a resource leak.
                from opentelemetry.sdk.trace.export import ConsoleSpanExporter

                global _traces_file_handle
                _traces_fh = open(  # noqa: SIM115,WPS515
                    traces_path, "a", encoding="utf-8"
                )
                try:
                    _span_file_exporter = ConsoleSpanExporter(
                        out=_traces_fh,
                        formatter=lambda span: span.to_json(indent=None) + "\n",
                    )
                except Exception:
                    # Exporter construction failed: close the handle we just
                    # opened so a partial init cannot leak a descriptor.
                    _traces_fh.close()
                    raise
                # Only retain the handle for lifecycle close once the exporter
                # owns it successfully.
                _traces_file_handle = _traces_fh

            tracer_provider.add_span_processor(
                BatchSpanProcessor(_span_file_exporter)  # type: ignore[arg-type]
            )

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
    # Replaces the dead memory_usage_bytes/cpu_usage_percent gauges that
    # were previously emitted by the homegrown metrics stack.
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
    global \
        _telemetry_shutdown, \
        _meter_provider, \
        _tracer_provider, \
        _metrics_file_handle, \
        _traces_file_handle

    if _telemetry_shutdown:
        return

    _telemetry_shutdown = True  # lgtm[py/unused-global-variable]

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

    # Close file handles opened for the SDK-native Console*Exporter fallbacks.
    # Shutdown() above cascades force_flush() to all readers, so data is
    # already written by the time we reach here.  Closing the handle releases
    # the OS file descriptor and is idempotent (nulled immediately after close).
    if _metrics_file_handle is not None:
        try:
            _metrics_file_handle.close()
        except Exception:
            pass  # Closing errors must not interrupt process teardown.
        _metrics_file_handle = None
    if _traces_file_handle is not None:
        try:
            _traces_file_handle.close()
        except Exception:
            pass  # Closing errors must not interrupt process teardown.
        _traces_file_handle = None
