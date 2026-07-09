"""Extended tests for the OTel bootstrap — multi-surface export, flush-on-exit,
auto-instrumentation wiring, and OtelConfig new fields.

Scope:
  A. File exporter flush-on-exit regression (CRITICAL):
       Configure telemetry with a file exporter, emit a counter, call
       shutdown_telemetry(), assert data flushed to the file.  Proves CLI
       metrics survive process exit when shutdown is called.

  B. configure_telemetry wires each instrumentor when enabled + SDK present;
       is a no-op when the instrumentor is absent (ImportError guard exercised).

  C. OtelConfig new fields + env precedence (ORB_TELEMETRY_FILE_DIR).

  D. shutdown_telemetry idempotency + safe-when-unconfigured.

  E. File exporter path resolution (_resolve_telemetry_file_dir).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

import orb.bootstrap.telemetry as telemetry_module
from orb.bootstrap.telemetry import (
    _reset_telemetry_state,
    _resolve_telemetry_file_dir,
    configure_telemetry,
    shutdown_telemetry,
)
from orb.config.schemas.observability_schema import OtelConfig
from orb.domain.base.ports.logging_port import LoggingPort
from orb.infrastructure.di.container import DIContainer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_container_with_otel(otel_config: OtelConfig) -> DIContainer:
    """Build a minimal DIContainer whose ConfigurationPort returns otel_config."""
    container = DIContainer()

    mock_logger = Mock(spec=LoggingPort)
    container.register_instance(LoggingPort, mock_logger)

    mock_app_config = MagicMock()
    mock_app_config.observability = otel_config

    from orb.domain.base.ports.configuration_port import ConfigurationPort

    mock_config_port = MagicMock(spec=ConfigurationPort)
    mock_config_port.get_typed.return_value = mock_app_config
    container.register_instance(ConfigurationPort, mock_config_port)

    return container


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_telemetry():
    """Reset telemetry state before and after every test for isolation."""
    _reset_telemetry_state()
    yield
    _reset_telemetry_state()
    # Un-instrument any auto-instrumentors wired during the test.
    for pkg in [
        "opentelemetry.instrumentation.logging",
        "opentelemetry.instrumentation.sqlalchemy",
        "opentelemetry.instrumentation.botocore",
        "opentelemetry.instrumentation.click",
        "opentelemetry.instrumentation.system_metrics",
    ]:
        mod = sys.modules.get(pkg)
        if mod:
            for cls_name in [
                "LoggingInstrumentor",
                "SQLAlchemyInstrumentor",
                "BotocoreInstrumentor",
                "ClickInstrumentor",
                "SystemMetricsInstrumentor",
            ]:
                cls = getattr(mod, cls_name, None)
                if cls:
                    try:
                        cls().uninstrument()
                    except Exception:
                        pass


# ===========================================================================
# A. File exporter flush-on-exit regression
# ===========================================================================


class TestFileExporterFlushOnExit:
    """Verify that shutdown_telemetry() flushes metrics to the file exporter."""

    @pytest.mark.skipif(
        "opentelemetry.exporter.otlp.json.file" not in sys.modules
        and not any("opentelemetry_exporter_otlp_json_file" in str(p) for p in sys.path),
        reason="opentelemetry-exporter-otlp-json-file not installed (transitive dep not yet on PyPI)",
    )
    def test_file_exporter_receives_metrics_after_shutdown(self, tmp_path):
        """Regression: CLI metrics must survive process exit when shutdown is called.

        Strategy:
          1. Build a real MeterProvider with a PeriodicExportingMetricReader
             backed by FileMetricExporter writing to a temp file.
          2. Create a counter on the meter and add 1.
          3. Call shutdown_telemetry() → force_flush cascades to the reader.
          4. Read the JSONL file — must contain at least one line with data.
        """
        # This test runs against the real SDK.  We set providers directly on
        # the module globals so that shutdown_telemetry() can call shutdown()
        # on them without a full DI container bootstrap.
        try:
            from opentelemetry import metrics as otel_metrics
            from opentelemetry.exporter.otlp.json.file import FileMetricExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        except ImportError:
            pytest.skip("Required OTel packages not installed")

        metrics_path = tmp_path / "metrics.jsonl"

        exporter = FileMetricExporter(path=metrics_path)
        reader = PeriodicExportingMetricReader(exporter, export_interval_millis=60_000)
        resource = Resource.create({SERVICE_NAME: "orb-test"})
        provider = MeterProvider(resource=resource, metric_readers=[reader])

        # Install as global and register with the shutdown machinery.
        otel_metrics.set_meter_provider(provider)
        telemetry_module._meter_provider = provider
        telemetry_module._telemetry_configured = True
        telemetry_module._telemetry_shutdown = False

        # Record a metric.
        meter = otel_metrics.get_meter("orb.test")
        counter = meter.create_counter("test.shutdown.counter")
        counter.add(42)

        # Flush — export_interval is 60 s so without shutdown() nothing would write.
        shutdown_telemetry()

        # The file must exist and contain valid JSON Lines.
        assert metrics_path.exists(), "metrics.jsonl was not created by FileMetricExporter"
        content = metrics_path.read_text(encoding="utf-8").strip()
        assert content, "metrics.jsonl is empty after shutdown_telemetry()"

        lines = [ln for ln in content.splitlines() if ln.strip()]
        assert lines, "metrics.jsonl has no non-empty lines"

        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict), f"Expected JSON object, got: {type(parsed)}"

    def test_shutdown_calls_meter_provider_shutdown(self):
        """shutdown_telemetry() calls MeterProvider.shutdown()."""
        mock_mp = Mock()
        telemetry_module._meter_provider = mock_mp
        telemetry_module._telemetry_configured = True
        telemetry_module._telemetry_shutdown = False

        shutdown_telemetry()

        mock_mp.shutdown.assert_called_once()

    def test_shutdown_calls_tracer_provider_shutdown(self):
        """shutdown_telemetry() calls TracerProvider.shutdown()."""
        mock_tp = Mock()
        telemetry_module._tracer_provider = mock_tp
        telemetry_module._telemetry_configured = True
        telemetry_module._telemetry_shutdown = False

        shutdown_telemetry()

        mock_tp.shutdown.assert_called_once()

    def test_shutdown_calls_both_providers(self):
        """shutdown_telemetry() calls shutdown() on both meter and tracer providers."""
        mock_mp = Mock()
        mock_tp = Mock()
        telemetry_module._meter_provider = mock_mp
        telemetry_module._tracer_provider = mock_tp
        telemetry_module._telemetry_configured = True
        telemetry_module._telemetry_shutdown = False

        shutdown_telemetry()

        mock_mp.shutdown.assert_called_once()
        mock_tp.shutdown.assert_called_once()

    def test_shutdown_swallows_provider_exceptions(self):
        """Exceptions from provider.shutdown() must not propagate."""
        mock_mp = Mock()
        mock_mp.shutdown.side_effect = RuntimeError("boom")
        telemetry_module._meter_provider = mock_mp
        telemetry_module._telemetry_configured = True
        telemetry_module._telemetry_shutdown = False

        # Must not raise.
        shutdown_telemetry()


# ===========================================================================
# D. shutdown_telemetry idempotency + safe-when-unconfigured
# ===========================================================================


class TestShutdownTelemetry:
    """Tests for shutdown_telemetry() behaviour."""

    def test_safe_when_never_configured(self):
        """shutdown_telemetry() is a no-op when configure_telemetry was never called."""
        # _meter_provider and _tracer_provider are None (reset by fixture).
        assert telemetry_module._meter_provider is None
        assert telemetry_module._tracer_provider is None
        # Must not raise.
        shutdown_telemetry()

    def test_safe_when_providers_are_none(self):
        """shutdown_telemetry() is safe when providers are None regardless of flags."""
        telemetry_module._telemetry_configured = True
        telemetry_module._meter_provider = None
        telemetry_module._tracer_provider = None
        telemetry_module._telemetry_shutdown = False

        shutdown_telemetry()  # Must not raise.

    def test_idempotent_second_call_is_noop(self):
        """A second call to shutdown_telemetry() must not call provider.shutdown() again."""
        mock_mp = Mock()
        telemetry_module._meter_provider = mock_mp
        telemetry_module._telemetry_configured = True
        telemetry_module._telemetry_shutdown = False

        shutdown_telemetry()
        shutdown_telemetry()

        mock_mp.shutdown.assert_called_once()

    def test_shutdown_flag_set_after_call(self):
        """shutdown_telemetry() sets _telemetry_shutdown to True."""
        telemetry_module._telemetry_configured = True
        telemetry_module._telemetry_shutdown = False

        shutdown_telemetry()

        assert telemetry_module._telemetry_shutdown is True

    def test_reset_clears_shutdown_flag(self):
        """_reset_telemetry_state() resets _telemetry_shutdown for test isolation."""
        shutdown_telemetry()
        assert telemetry_module._telemetry_shutdown is True

        _reset_telemetry_state()
        assert telemetry_module._telemetry_shutdown is False

    def test_reset_clears_provider_refs(self):
        """_reset_telemetry_state() clears _meter_provider and _tracer_provider."""
        telemetry_module._meter_provider = Mock()
        telemetry_module._tracer_provider = Mock()

        _reset_telemetry_state()

        assert telemetry_module._meter_provider is None
        assert telemetry_module._tracer_provider is None


# ===========================================================================
# C. OtelConfig new fields + env precedence
# ===========================================================================


class TestOtelConfigNewFields:
    """Tests for OtelConfig extensions."""

    def test_file_accepted_in_metrics_exporters(self):
        """'file' is a valid entry in metrics_exporters."""
        cfg = OtelConfig(enabled=True, metrics_exporters=["file"])
        assert "file" in cfg.metrics_exporters

    def test_file_accepted_as_traces_exporter(self):
        """'file' is a valid traces_exporter value."""
        cfg = OtelConfig(enabled=True, traces_exporter="file")
        assert cfg.traces_exporter == "file"

    def test_telemetry_file_dir_field_default_none(self):
        """telemetry_file_dir defaults to None (resolved lazily)."""
        cfg = OtelConfig()
        assert cfg.telemetry_file_dir is None

    def test_telemetry_file_dir_field_set(self):
        """telemetry_file_dir is preserved when set."""
        cfg = OtelConfig(telemetry_file_dir="/tmp/orb-tel")
        assert cfg.telemetry_file_dir == "/tmp/orb-tel"

    def test_instrument_toggles_default_true(self):
        """All instrument_* toggles default to True."""
        cfg = OtelConfig()
        assert cfg.instrument_sqlalchemy is True
        assert cfg.instrument_botocore is True
        assert cfg.instrument_click is True
        assert cfg.instrument_system_metrics is True
        assert cfg.instrument_logging is True

    def test_instrument_toggles_can_be_disabled(self):
        """Instrument toggles can be individually disabled."""
        cfg = OtelConfig(
            instrument_sqlalchemy=False,
            instrument_botocore=False,
            instrument_click=False,
            instrument_system_metrics=False,
            instrument_logging=False,
        )
        assert cfg.instrument_sqlalchemy is False
        assert cfg.instrument_botocore is False
        assert cfg.instrument_click is False
        assert cfg.instrument_system_metrics is False
        assert cfg.instrument_logging is False

    def test_orb_telemetry_file_dir_env_override(self, monkeypatch):
        """ORB_TELEMETRY_FILE_DIR env var overrides telemetry_file_dir."""
        monkeypatch.setenv("ORB_TELEMETRY_FILE_DIR", "/env/override/path")
        cfg = OtelConfig(telemetry_file_dir="/config/path")
        assert cfg.telemetry_file_dir == "/env/override/path"

    def test_orb_telemetry_file_dir_env_sets_from_none(self, monkeypatch):
        """ORB_TELEMETRY_FILE_DIR sets telemetry_file_dir when config is None."""
        monkeypatch.setenv("ORB_TELEMETRY_FILE_DIR", "/env/path")
        cfg = OtelConfig()
        assert cfg.telemetry_file_dir == "/env/path"

    def test_orb_telemetry_file_dir_env_unset_preserves_config(self, monkeypatch):
        """Unset ORB_TELEMETRY_FILE_DIR leaves telemetry_file_dir unchanged."""
        monkeypatch.delenv("ORB_TELEMETRY_FILE_DIR", raising=False)
        cfg = OtelConfig(telemetry_file_dir="/config/path")
        assert cfg.telemetry_file_dir == "/config/path"

    def test_multiple_exporters_including_file(self):
        """Multiple exporters including 'file' are preserved."""
        cfg = OtelConfig(enabled=True, metrics_exporters=["prometheus", "file", "otlp"])
        assert cfg.metrics_exporters == ["prometheus", "file", "otlp"]


# ===========================================================================
# E. File exporter path resolution
# ===========================================================================


class TestResolveFileDir:
    """Tests for _resolve_telemetry_file_dir()."""

    def test_uses_configured_dir_when_writable(self, tmp_path):
        """Returns the configured path when it is writable."""
        target = tmp_path / "telemetry"
        result = _resolve_telemetry_file_dir(str(target))
        assert result == target
        assert result.exists()

    def test_falls_back_when_configured_unwritable(self, tmp_path, monkeypatch):
        """Falls back to tier-2 (~/.orb/work/telemetry) when configured dir is unwritable."""
        fallback = tmp_path / "fallback_home_orb_work_telemetry"
        fallback.mkdir(parents=True, exist_ok=True)

        # Patch Path.home() so tier-2 uses our tmp_path, not the real home.
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

        # Configured dir doesn't exist and is under a non-writable parent: point
        # to a path whose parent won't be creatable (simulate by patching mkdir).
        original_mkdir = Path.mkdir

        call_count = [0]

        def _flaky_mkdir(self, *args, **kwargs):
            if "bad_dir" in str(self) and call_count[0] < 2:
                call_count[0] += 1
                raise PermissionError("simulated permission error")
            original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", _flaky_mkdir)

        result = _resolve_telemetry_file_dir(str(tmp_path / "bad_dir" / "sub"))
        # Should have landed on one of the fallback paths.
        assert result.exists()

    def test_falls_back_to_tempdir_when_all_else_fails(self, monkeypatch):
        """Falls back to a temporary directory when all candidates fail."""
        import tempfile

        temp_sentinel = Path(tempfile.mkdtemp(prefix="orb-test-tel-"))

        # Patch Path.mkdir to always raise PermissionError.
        monkeypatch.setattr(
            Path, "mkdir", lambda *a, **kw: (_ for _ in ()).throw(PermissionError("denied"))
        )
        # Patch tempfile.mkdtemp to return our sentinel.
        monkeypatch.setattr(tempfile, "mkdtemp", lambda **kw: str(temp_sentinel))

        result = _resolve_telemetry_file_dir("/non/existent/path")
        assert result == temp_sentinel

    def test_none_input_skips_to_tier2(self, tmp_path, monkeypatch):
        """None input skips tier-1 and goes to tier-2."""
        # Redirect tier-2 to our tmp_path.
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = _resolve_telemetry_file_dir(None)
        # Should be under tmp_path (which acts as "home" here).
        assert result.exists()


# ===========================================================================
# B. Auto-instrumentation wiring
# ===========================================================================


class TestAutoInstrumentation:
    """configure_telemetry wires enabled instrumentors; skips absent ones."""

    def _make_enabled_config(self, **kwargs) -> OtelConfig:
        defaults = dict(
            enabled=True,
            instrument_sqlalchemy=False,
            instrument_botocore=False,
            instrument_click=False,
            instrument_system_metrics=False,
            instrument_logging=False,
        )
        defaults.update(kwargs)
        return OtelConfig(**defaults)

    def test_logging_instrumentor_called_when_enabled(self):
        """LoggingInstrumentor().instrument() is called when instrument_logging=True."""
        cfg = self._make_enabled_config(instrument_logging=True)
        container = _make_container_with_otel(cfg)

        mock_instance = Mock()
        mock_cls = Mock(return_value=mock_instance)

        with (
            patch("opentelemetry.sdk.metrics.MeterProvider"),
            patch("opentelemetry.sdk.trace.TracerProvider"),
            patch("opentelemetry.metrics.set_meter_provider"),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.sampling.TraceIdRatioBased"),
            patch.dict(
                sys.modules,
                {"opentelemetry.instrumentation.logging": Mock(LoggingInstrumentor=mock_cls)},
            ),
        ):
            configure_telemetry(container)

        mock_instance.instrument.assert_called_once()

    def test_logging_instrumentor_skipped_when_disabled(self):
        """LoggingInstrumentor is not called when instrument_logging=False."""
        cfg = self._make_enabled_config(instrument_logging=False)
        container = _make_container_with_otel(cfg)

        mock_instance = Mock()
        mock_cls = Mock(return_value=mock_instance)

        with (
            patch("opentelemetry.sdk.metrics.MeterProvider"),
            patch("opentelemetry.sdk.trace.TracerProvider"),
            patch("opentelemetry.metrics.set_meter_provider"),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.sampling.TraceIdRatioBased"),
            patch.dict(
                sys.modules,
                {"opentelemetry.instrumentation.logging": Mock(LoggingInstrumentor=mock_cls)},
            ),
        ):
            configure_telemetry(container)

        mock_instance.instrument.assert_not_called()

    def test_sqlalchemy_instrumentor_called_when_enabled(self):
        """SQLAlchemyInstrumentor().instrument() is called when instrument_sqlalchemy=True."""
        cfg = self._make_enabled_config(instrument_sqlalchemy=True)
        container = _make_container_with_otel(cfg)

        mock_instance = Mock()
        mock_cls = Mock(return_value=mock_instance)

        with (
            patch("opentelemetry.sdk.metrics.MeterProvider"),
            patch("opentelemetry.sdk.trace.TracerProvider"),
            patch("opentelemetry.metrics.set_meter_provider"),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.sampling.TraceIdRatioBased"),
            patch.dict(
                sys.modules,
                {"opentelemetry.instrumentation.sqlalchemy": Mock(SQLAlchemyInstrumentor=mock_cls)},
            ),
        ):
            configure_telemetry(container)

        mock_instance.instrument.assert_called_once()

    def test_botocore_instrumentor_called_when_enabled(self):
        """BotocoreInstrumentor().instrument() is called when instrument_botocore=True."""
        cfg = self._make_enabled_config(instrument_botocore=True)
        container = _make_container_with_otel(cfg)

        mock_instance = Mock()
        mock_cls = Mock(return_value=mock_instance)

        with (
            patch("opentelemetry.sdk.metrics.MeterProvider"),
            patch("opentelemetry.sdk.trace.TracerProvider"),
            patch("opentelemetry.metrics.set_meter_provider"),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.sampling.TraceIdRatioBased"),
            patch.dict(
                sys.modules,
                {"opentelemetry.instrumentation.botocore": Mock(BotocoreInstrumentor=mock_cls)},
            ),
        ):
            configure_telemetry(container)

        mock_instance.instrument.assert_called_once()

    def test_click_instrumentor_called_when_enabled(self):
        """ClickInstrumentor().instrument() is called when instrument_click=True."""
        cfg = self._make_enabled_config(instrument_click=True)
        container = _make_container_with_otel(cfg)

        mock_instance = Mock()
        mock_cls = Mock(return_value=mock_instance)

        with (
            patch("opentelemetry.sdk.metrics.MeterProvider"),
            patch("opentelemetry.sdk.trace.TracerProvider"),
            patch("opentelemetry.metrics.set_meter_provider"),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.sampling.TraceIdRatioBased"),
            patch.dict(
                sys.modules,
                {"opentelemetry.instrumentation.click": Mock(ClickInstrumentor=mock_cls)},
            ),
        ):
            configure_telemetry(container)

        mock_instance.instrument.assert_called_once()

    def test_system_metrics_instrumentor_called_when_enabled(self):
        """SystemMetricsInstrumentor().instrument() is called when instrument_system_metrics=True."""
        cfg = self._make_enabled_config(instrument_system_metrics=True)
        container = _make_container_with_otel(cfg)

        mock_instance = Mock()
        mock_cls = Mock(return_value=mock_instance)

        with (
            patch("opentelemetry.sdk.metrics.MeterProvider"),
            patch("opentelemetry.sdk.trace.TracerProvider"),
            patch("opentelemetry.metrics.set_meter_provider"),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.sampling.TraceIdRatioBased"),
            patch.dict(
                sys.modules,
                {
                    "opentelemetry.instrumentation.system_metrics": Mock(
                        SystemMetricsInstrumentor=mock_cls
                    )
                },
            ),
        ):
            configure_telemetry(container)

        mock_instance.instrument.assert_called_once()

    def test_absent_instrumentor_package_does_not_raise(self):
        """configure_telemetry does not raise when an instrumentor package is absent."""
        cfg = self._make_enabled_config(
            instrument_logging=True,
            instrument_sqlalchemy=True,
            instrument_botocore=True,
            instrument_click=True,
            instrument_system_metrics=True,
        )
        container = _make_container_with_otel(cfg)

        # Remove all instrumentation modules to simulate absent packages.
        absent_modules = {
            "opentelemetry.instrumentation.logging": None,
            "opentelemetry.instrumentation.sqlalchemy": None,
            "opentelemetry.instrumentation.botocore": None,
            "opentelemetry.instrumentation.click": None,
            "opentelemetry.instrumentation.system_metrics": None,
        }

        with (
            patch("opentelemetry.sdk.metrics.MeterProvider"),
            patch("opentelemetry.sdk.trace.TracerProvider"),
            patch("opentelemetry.metrics.set_meter_provider"),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.sampling.TraceIdRatioBased"),
            patch.dict(sys.modules, absent_modules),
        ):
            # Must complete without raising.
            configure_telemetry(container)

    def test_all_disabled_no_instrumentors_called(self):
        """When all instrument_* are False, no instrumentors are called."""
        cfg = self._make_enabled_config()  # all False by default in helper
        container = _make_container_with_otel(cfg)

        mock_instances = {}
        mock_modules = {}
        for name, cls_name in [
            ("opentelemetry.instrumentation.logging", "LoggingInstrumentor"),
            ("opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor"),
            ("opentelemetry.instrumentation.botocore", "BotocoreInstrumentor"),
            ("opentelemetry.instrumentation.click", "ClickInstrumentor"),
            ("opentelemetry.instrumentation.system_metrics", "SystemMetricsInstrumentor"),
        ]:
            inst = Mock()
            mock_instances[cls_name] = inst
            mock_modules[name] = Mock(**{cls_name: Mock(return_value=inst)})

        with (
            patch("opentelemetry.sdk.metrics.MeterProvider"),
            patch("opentelemetry.sdk.trace.TracerProvider"),
            patch("opentelemetry.metrics.set_meter_provider"),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.sampling.TraceIdRatioBased"),
            patch.dict(sys.modules, mock_modules),
        ):
            configure_telemetry(container)

        for cls_name, inst in mock_instances.items():
            inst.instrument.assert_not_called(), f"{cls_name}.instrument was unexpectedly called"


# ===========================================================================
# Additional file exporter integration tests (no real OTel SDK required)
# ===========================================================================


class TestFileExporterWiring:
    """configure_telemetry wires file readers/processors when 'file' in config."""

    def _make_enabled_config(self, **kwargs) -> OtelConfig:
        defaults = dict(
            enabled=True,
            instrument_sqlalchemy=False,
            instrument_botocore=False,
            instrument_click=False,
            instrument_system_metrics=False,
            instrument_logging=False,
        )
        defaults.update(kwargs)
        return OtelConfig(**defaults)

    def test_file_metric_reader_created(self, tmp_path):
        """When 'file' in metrics_exporters, a PeriodicExportingMetricReader is added."""
        cfg = self._make_enabled_config(
            metrics_exporters=["file"],
            telemetry_file_dir=str(tmp_path),
        )
        container = _make_container_with_otel(cfg)

        mock_file_exporter_cls = Mock(return_value=Mock())
        mock_file_module = Mock(FileMetricExporter=mock_file_exporter_cls)

        created_readers = []

        def capture_periodic(exporter):
            obj = Mock()
            created_readers.append(exporter)
            return obj

        with (
            patch("opentelemetry.sdk.metrics.MeterProvider"),
            patch("opentelemetry.sdk.trace.TracerProvider"),
            patch("opentelemetry.metrics.set_meter_provider"),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.sampling.TraceIdRatioBased"),
            patch.dict(
                sys.modules,
                {"opentelemetry.exporter.otlp.json.file": mock_file_module},
            ),
            patch(
                "opentelemetry.sdk.metrics.export.PeriodicExportingMetricReader",
                side_effect=capture_periodic,
            ),
        ):
            configure_telemetry(container)

        assert mock_file_exporter_cls.called, "FileMetricExporter constructor was not called"
        call_kwargs = mock_file_exporter_cls.call_args
        assert call_kwargs is not None
        # Should have been called with path= argument.
        if call_kwargs.kwargs:
            assert "path" in call_kwargs.kwargs

    def test_file_span_exporter_created(self, tmp_path):
        """When traces_exporter='file', a BatchSpanProcessor is created with FileSpanExporter."""
        cfg = self._make_enabled_config(
            traces_exporter="file",
            telemetry_file_dir=str(tmp_path),
        )
        container = _make_container_with_otel(cfg)

        mock_file_span_cls = Mock(return_value=Mock())
        mock_file_module = Mock(FileSpanExporter=mock_file_span_cls)

        created_processors = []

        def capture_processor(exporter):
            created_processors.append(exporter)
            return Mock()

        with (
            patch("opentelemetry.sdk.metrics.MeterProvider"),
            patch("opentelemetry.sdk.trace.TracerProvider"),
            patch("opentelemetry.metrics.set_meter_provider"),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.sampling.TraceIdRatioBased"),
            patch.dict(
                sys.modules,
                {"opentelemetry.exporter.otlp.json.file": mock_file_module},
            ),
            patch(
                "opentelemetry.sdk.trace.export.BatchSpanProcessor",
                side_effect=capture_processor,
            ),
        ):
            configure_telemetry(container)

        assert mock_file_span_cls.called, "FileSpanExporter constructor was not called"

    def test_file_exporter_import_error_does_not_crash(self, tmp_path):
        """ImportError from file exporter module is silently skipped."""
        cfg = self._make_enabled_config(
            metrics_exporters=["file"],
            traces_exporter="file",
            telemetry_file_dir=str(tmp_path),
        )
        container = _make_container_with_otel(cfg)

        with (
            patch("opentelemetry.sdk.metrics.MeterProvider"),
            patch("opentelemetry.sdk.trace.TracerProvider"),
            patch("opentelemetry.metrics.set_meter_provider"),
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("opentelemetry.sdk.trace.sampling.TraceIdRatioBased"),
            patch.dict(
                sys.modules,
                # Setting to None causes ImportError on import.
                {"opentelemetry.exporter.otlp.json.file": None},
            ),
        ):
            # Must complete without raising even when the file exporter is absent.
            configure_telemetry(container)
