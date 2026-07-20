"""Unit tests for orb.bootstrap.telemetry.

Covers:
- _reset_telemetry_state() resets all state fields
- _resolve_telemetry_file_dir() — configured tier, home tier, tempfile fallback
- configure_telemetry() — idempotency guard, ImportError guard (no SDK), disabled config
- shutdown_telemetry() — idempotency, provider shutdown, file handle closing

No real OTel SDK needed — SDK types are patched or unavailable.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _reset():
    """Reset module-level telemetry state before each test."""
    from orb.bootstrap.telemetry import _reset_telemetry_state

    _reset_telemetry_state()


# ---------------------------------------------------------------------------
# _reset_telemetry_state
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResetTelemetryState:
    def setup_method(self):
        _reset()

    def test_configured_flag_set_to_false(self):
        from orb.bootstrap import telemetry

        telemetry._state.configured = True
        telemetry._reset_telemetry_state()
        assert telemetry._state.configured is False

    def test_providers_cleared(self):
        from orb.bootstrap import telemetry

        telemetry._state.meter_provider = MagicMock()
        telemetry._state.tracer_provider = MagicMock()
        telemetry._reset_telemetry_state()
        assert telemetry._state.meter_provider is None
        assert telemetry._state.tracer_provider is None

    def test_shutdown_flag_reset(self):
        from orb.bootstrap import telemetry

        telemetry._state.shutdown = True
        telemetry._reset_telemetry_state()
        assert telemetry._state.shutdown is False

    def test_file_handles_closed_and_nulled(self):
        from orb.bootstrap import telemetry

        fh1 = MagicMock()
        fh2 = MagicMock()
        telemetry._state.metrics_file_handle = fh1
        telemetry._state.traces_file_handle = fh2
        telemetry._reset_telemetry_state()
        fh1.close.assert_called_once()
        fh2.close.assert_called_once()
        assert telemetry._state.metrics_file_handle is None
        assert telemetry._state.traces_file_handle is None

    def test_close_error_on_file_handle_does_not_raise(self):
        from orb.bootstrap import telemetry

        fh = MagicMock()
        fh.close.side_effect = OSError("already closed")
        telemetry._state.metrics_file_handle = fh
        # Must not propagate the OSError
        telemetry._reset_telemetry_state()
        assert telemetry._state.metrics_file_handle is None


# ---------------------------------------------------------------------------
# _resolve_telemetry_file_dir
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveTelemetryFileDir:
    def setup_method(self):
        _reset()

    def test_returns_configured_path_when_writable(self, tmp_path):
        from orb.bootstrap.telemetry import _resolve_telemetry_file_dir

        result = _resolve_telemetry_file_dir(str(tmp_path))
        assert result == tmp_path

    def test_falls_back_to_home_orb_work(self, tmp_path, monkeypatch):
        """When configured path is not writable, falls back to ~/.orb/work/telemetry."""
        from orb.bootstrap.telemetry import _resolve_telemetry_file_dir

        # Patch home() to return tmp_path so the fallback candidate is writable
        with patch("pathlib.Path.home", return_value=tmp_path):
            # The first candidate (/no/write/path) will fail on mkdir/touch;
            # the second (~/.orb/work/telemetry = tmp_path/.orb/...) will succeed.
            result = _resolve_telemetry_file_dir("/no/write/path/that/does/not/exist/xyz123")

        assert result == tmp_path / ".orb" / "work" / "telemetry"

    def test_falls_back_to_tempdir_when_all_fail(self):
        from orb.bootstrap.telemetry import _resolve_telemetry_file_dir

        with (
            patch("pathlib.Path.mkdir", side_effect=PermissionError("no")),
            patch("pathlib.Path.touch", side_effect=PermissionError("no")),
            patch("tempfile.mkdtemp", return_value="/tmp/orb-telemetry-test"),
        ):
            result = _resolve_telemetry_file_dir(None)

        assert str(result) == "/tmp/orb-telemetry-test"

    def test_no_configured_path_skips_first_tier(self, tmp_path):
        from orb.bootstrap.telemetry import _resolve_telemetry_file_dir

        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _resolve_telemetry_file_dir(None)

        assert result == tmp_path / ".orb" / "work" / "telemetry"


# ---------------------------------------------------------------------------
# configure_telemetry — idempotency + ImportError guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigureTelemetryIdempotency:
    def setup_method(self):
        _reset()

    def test_second_call_is_noop(self):
        from orb.bootstrap import telemetry

        # Mark as already configured
        telemetry._state.configured = True

        container = MagicMock()
        # Should not attempt any SDK import or container resolution
        telemetry.configure_telemetry(container)
        container.get.assert_not_called()

    def test_configure_sets_configured_flag(self):
        from orb.bootstrap import telemetry

        # Simulate SDK not available so the function exits early after setting flag
        with patch.dict("sys.modules", {"opentelemetry": None}):
            container = MagicMock()
            telemetry.configure_telemetry(container)

        assert telemetry._state.configured is True

    def test_sdk_import_error_is_silent(self):
        """When opentelemetry-sdk is absent configure_telemetry must not raise."""
        from orb.bootstrap import telemetry

        with patch(
            "builtins.__import__",
            side_effect=lambda name, *a, **k: (
                (_ for _ in ()).throw(ImportError("no otel"))
                if "opentelemetry" in name
                else __import__(name, *a, **k)
            ),
        ):
            container = MagicMock()
            # Must not raise
            telemetry.configure_telemetry(container)


@pytest.mark.unit
class TestConfigureTelemetryDisabled:
    def setup_method(self):
        _reset()

    def test_disabled_config_skips_provider_setup(self):
        from orb.bootstrap import telemetry

        # Fake SDK present but otel_config.enabled = False
        mock_otel_config = MagicMock()
        mock_otel_config.enabled = False

        mock_app_config = MagicMock()
        mock_app_config.observability = mock_otel_config

        mock_config_port = MagicMock()
        mock_config_port.get_typed.return_value = mock_app_config

        container = MagicMock()
        container.get.return_value = mock_config_port

        # Provide minimal SDK stubs
        fake_otel = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": fake_otel,
                "opentelemetry.metrics": MagicMock(),
                "opentelemetry.trace": MagicMock(),
                "opentelemetry.sdk.metrics": MagicMock(),
                "opentelemetry.sdk.resources": MagicMock(),
                "opentelemetry.sdk.trace": MagicMock(),
            },
        ):
            telemetry.configure_telemetry(container)

        # No meter_provider should have been installed
        assert telemetry._state.meter_provider is None


# ---------------------------------------------------------------------------
# shutdown_telemetry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestShutdownTelemetry:
    def setup_method(self):
        _reset()

    def test_noop_when_never_configured(self):
        from orb.bootstrap.telemetry import shutdown_telemetry

        # Should not raise when nothing was configured
        shutdown_telemetry()

    def test_second_call_is_noop(self):
        from orb.bootstrap import telemetry

        meter_mock = MagicMock()
        telemetry._state.meter_provider = meter_mock
        telemetry._state.shutdown = True

        telemetry.shutdown_telemetry()
        # Provider.shutdown should NOT be called again once already shut down
        meter_mock.shutdown.assert_not_called()

    def test_shuts_down_meter_provider(self):
        from orb.bootstrap import telemetry

        meter_mock = MagicMock()
        telemetry._state.meter_provider = meter_mock

        telemetry.shutdown_telemetry()
        meter_mock.shutdown.assert_called_once()

    def test_shuts_down_tracer_provider(self):
        from orb.bootstrap import telemetry

        tracer_mock = MagicMock()
        telemetry._state.tracer_provider = tracer_mock

        telemetry.shutdown_telemetry()
        tracer_mock.shutdown.assert_called_once()

    def test_shutdown_error_does_not_propagate(self):
        from orb.bootstrap import telemetry

        bad_meter = MagicMock()
        bad_meter.shutdown.side_effect = RuntimeError("SDK error")
        telemetry._state.meter_provider = bad_meter

        # Must not raise
        telemetry.shutdown_telemetry()

    def test_file_handles_closed_on_shutdown(self):
        from orb.bootstrap import telemetry

        fh_metrics = MagicMock()
        fh_traces = MagicMock()
        telemetry._state.metrics_file_handle = fh_metrics
        telemetry._state.traces_file_handle = fh_traces

        telemetry.shutdown_telemetry()
        fh_metrics.close.assert_called_once()
        fh_traces.close.assert_called_once()
        assert telemetry._state.metrics_file_handle is None
        assert telemetry._state.traces_file_handle is None

    def test_file_handle_close_error_does_not_propagate(self):
        from orb.bootstrap import telemetry

        fh = MagicMock()
        fh.close.side_effect = OSError("locked")
        telemetry._state.metrics_file_handle = fh

        telemetry.shutdown_telemetry()  # Must not raise

    def test_shutdown_flag_set_true(self):
        from orb.bootstrap import telemetry

        telemetry.shutdown_telemetry()
        assert telemetry._state.shutdown is True
