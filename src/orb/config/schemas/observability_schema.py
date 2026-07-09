"""Observability (OpenTelemetry) configuration schema.

Standard ``OTEL_*`` environment variables take precedence over file-level
values.  This is the industry-standard "env wins" rule: operators can
override any file setting without rebuilding an image.  The override logic
lives in the ``_apply_otel_env_overrides`` model validator so it runs
automatically after Pydantic validation.

Honoured env vars:
  OTEL_SDK_DISABLED          → ``enabled`` (True when var == "true", else False)
  OTEL_EXPORTER_OTLP_ENDPOINT→ ``otlp_endpoint``
  OTEL_SERVICE_NAME          → ``service_name``
  OTEL_TRACES_SAMPLER_ARG    → ``traces_sample_rate`` (parsed as float)
  ORB_TELEMETRY_FILE_DIR     → ``telemetry_file_dir`` (file exporter output dir)
"""

import os
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OtelConfig(BaseModel):
    """OpenTelemetry configuration.

    ``enabled`` defaults to ``False`` so that the SDK is **never** activated
    unless explicitly opted in.  When disabled, the :func:`configure_telemetry`
    bootstrap call is a complete no-op.

    ``metrics_exporters`` accepts a list so that multiple exporters can be
    active simultaneously:
      - ``"prometheus"`` — wires a ``PrometheusMetricReader`` against the
        global ``prometheus_client.REGISTRY`` (works with the existing
        ``/metrics`` FastAPI route).
      - ``"otlp"`` — wires a ``PeriodicExportingMetricReader`` that pushes
        to the OTLP endpoint specified by ``otlp_endpoint``.
      - ``"file"`` — wires a ``PeriodicExportingMetricReader`` with a
        ``FileMetricExporter`` writing OTLP JSON Lines to ``telemetry_file_dir``.
        Mandatory for CLI surfaces where the process exits before any scrape.

    Multiple entries can coexist in the list (e.g. ``["prometheus", "file"]``).

    ``traces_exporter`` accepts:
      - ``"otlp"`` — ``BatchSpanProcessor(OTLPSpanExporter(...))``.
      - ``"file"`` — ``BatchSpanProcessor(FileSpanExporter(path=...))``.

    **Auto-instrumentation toggles** (all default to ``True`` when
    ``enabled=True`` and the instrumentor package is installed):
      - ``instrument_sqlalchemy`` — SQLAlchemy spans.
      - ``instrument_botocore`` — boto3/botocore spans (complements
        BotocoreMetricsHandler; does NOT replace it).
      - ``instrument_click`` — span per Click CLI command invocation.
      - ``instrument_system_metrics`` — CPU/memory/GC/thread metric gauges.
      - ``instrument_logging`` — injects ``trace_id``/``span_id`` into Python
        log records for log-trace correlation.

    **File exporter path resolution** (``telemetry_file_dir``):
      Follows the same 3-tier permission fallback as MetricsCollector:
        1. ``telemetry_file_dir`` from config (or ``ORB_TELEMETRY_FILE_DIR`` env).
        2. ``~/.orb/work/telemetry``.
        3. A temporary directory (``tempfile.mkdtemp``).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(False, description="Enable OpenTelemetry SDK initialisation")
    metrics_exporters: list[str] = Field(
        default_factory=list,
        description=(
            "Active metrics exporters. Valid values: 'prometheus', 'otlp', 'file'. "
            "Multiple entries are supported simultaneously."
        ),
    )
    traces_exporter: Optional[str] = Field(
        None,
        description="Traces exporter. Valid values: 'otlp', 'file', None (no traces).",
    )
    otlp_endpoint: Optional[str] = Field(
        None,
        description=(
            "Base OTLP endpoint URL (e.g. 'http://localhost:4317'). "
            "Used by both the OTLP metrics exporter and the OTLP span exporter."
        ),
    )
    service_name: str = Field(
        "orb",
        description="OpenTelemetry service.name resource attribute.",
    )
    traces_sample_rate: float = Field(
        0.1,
        description="TraceIdRatioBased sampler argument (0.0–1.0).",
        ge=0.0,
        le=1.0,
    )

    # --- File exporter settings ---
    telemetry_file_dir: Optional[str] = Field(
        None,
        description=(
            "Directory for file-based telemetry output (OTLP JSON Lines). "
            "Used when 'file' is in metrics_exporters or traces_exporter == 'file'. "
            "Resolution order: this field → ~/.orb/work/telemetry → tempdir. "
            "Can also be set via ORB_TELEMETRY_FILE_DIR environment variable."
        ),
    )

    # --- Auto-instrumentation toggles (all default True) ---
    instrument_sqlalchemy: bool = Field(
        True,
        description=(
            "Enable SQLAlchemyInstrumentor (spans). "
            "No-op when opentelemetry-instrumentation-sqlalchemy is not installed."
        ),
    )
    instrument_botocore: bool = Field(
        True,
        description=(
            "Enable BotocoreInstrumentor (spans). "
            "Complements BotocoreMetricsHandler — does NOT replace it. "
            "No-op when opentelemetry-instrumentation-botocore is not installed."
        ),
    )
    instrument_click: bool = Field(
        True,
        description=(
            "Enable ClickInstrumentor (span per CLI command invocation). "
            "No-op when opentelemetry-instrumentation-click is not installed."
        ),
    )
    instrument_system_metrics: bool = Field(
        True,
        description=(
            "Enable SystemMetricsInstrumentor (CPU/memory/GC/thread gauges). "
            "Replaces the dead memory_usage_bytes/cpu_usage_percent gauges. "
            "No-op when opentelemetry-instrumentation-system-metrics is not installed."
        ),
    )
    instrument_logging: bool = Field(
        True,
        description=(
            "Enable LoggingInstrumentor (injects trace_id/span_id into log records). "
            "No-op when opentelemetry-instrumentation-logging is not installed."
        ),
    )

    @model_validator(mode="after")
    def _apply_otel_env_overrides(self) -> "OtelConfig":
        """Apply standard OTEL_* environment variable overrides (env wins).

        This runs after Pydantic has validated the file-sourced values.  Any
        ``OTEL_*`` variable that is set in the environment overrides the
        corresponding field.  Unset variables leave the field unchanged.
        """
        # OTEL_SDK_DISABLED: "true" (case-insensitive) disables the SDK.
        sdk_disabled = os.environ.get("OTEL_SDK_DISABLED", "").strip().lower()
        if sdk_disabled == "true":
            object.__setattr__(self, "enabled", False)

        # OTEL_EXPORTER_OTLP_ENDPOINT overrides otlp_endpoint.
        otlp_env = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if otlp_env:
            object.__setattr__(self, "otlp_endpoint", otlp_env)

        # OTEL_SERVICE_NAME overrides service_name.
        svc_name = os.environ.get("OTEL_SERVICE_NAME", "").strip()
        if svc_name:
            object.__setattr__(self, "service_name", svc_name)

        # OTEL_TRACES_SAMPLER_ARG overrides traces_sample_rate (parsed as float).
        sampler_arg = os.environ.get("OTEL_TRACES_SAMPLER_ARG", "").strip()
        if sampler_arg:
            try:
                rate = float(sampler_arg)
                rate = max(0.0, min(1.0, rate))
                object.__setattr__(self, "traces_sample_rate", rate)
            except ValueError:
                pass  # Ignore unparseable values; keep the file/default value.

        # ORB_TELEMETRY_FILE_DIR overrides telemetry_file_dir.
        file_dir_env = os.environ.get("ORB_TELEMETRY_FILE_DIR", "").strip()
        if file_dir_env:
            object.__setattr__(self, "telemetry_file_dir", file_dir_env)

        return self
