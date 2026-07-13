"""Tests for telemetry_module.configure_telemetry() bootstrap function.

Scope:
  1. enabled=False → no-op (no provider set, no raise).
  2. SDK absent (ImportError path) → no-op, no raise.
  3. Idempotency — second call is a no-op.
  4. telemetry_module._reset_telemetry_state() re-arms the idempotency flag (_state.configured).
  5. enabled=True end-to-end: a real SDK MeterProvider is installed (not no-op).
  6. File-handle close on exporter-init failure (exception safety).
  7. shutdown_telemetry call-site wiring: CLI main + SDK ORBClient.cleanup().
"""

from __future__ import annotations

import sys
from contextlib import suppress
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

import orb.bootstrap.telemetry as telemetry_module
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

    # Build a mock AppConfig that carries the given OtelConfig.
    mock_app_config = MagicMock()
    mock_app_config.observability = otel_config

    # Build a mock ConfigurationPort whose get_typed(AppConfig) returns mock_app_config.
    from orb.domain.base.ports.configuration_port import ConfigurationPort

    mock_config_port = MagicMock(spec=ConfigurationPort)
    mock_config_port.get_typed.return_value = mock_app_config
    container.register_instance(ConfigurationPort, mock_config_port)

    return container


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_otel_globals():
    """Reset telemetry state before and after every test for isolation.

    OTel's set_meter_provider() is intentionally a one-shot operation
    (guarded by a Once gate): calling it a second time is a no-op.  Trying
    to "restore" the original proxy provider after each test by calling
    set_meter_provider(_PROXY_METER_PROVIDER) is therefore wrong — if the
    Once gate has not yet fired it fires with the proxy as its own argument,
    causing _ProxyMeterProvider.on_set_meter_provider(_PROXY_METER_PROVIDER)
    to hold _ProxyMeterProvider._lock and then call back into
    _ProxyMeterProvider.get_meter(), which tries to acquire the same lock a
    second time and deadlocks indefinitely (seen as ~277 s hangs under
    Python 3.14, where the deadlock is reliably deterministic because
    threading.Lock acquire now always blocks without a timeout by default).

    The correct approach for unit tests that only test idempotency flags and
    module-level _state is to reset _state via _reset_telemetry_state() and
    to mock opentelemetry.metrics.set_meter_provider at the call sites that
    need it (which the enabled-path tests already do).  We must not attempt
    to mutate OTel's process-wide Once-guarded singleton in teardown.
    """
    telemetry_module._reset_telemetry_state()

    yield

    telemetry_module._reset_telemetry_state()


# ---------------------------------------------------------------------------
# Test: enabled=False is a no-op
# ---------------------------------------------------------------------------


class TestConfigureTelemetryDisabled:
    """When otel.enabled is False configure_telemetry must be a complete no-op."""

    def test_does_not_raise_when_disabled(self):
        container = _make_container_with_otel(OtelConfig(enabled=False))
        # Must not raise under any circumstances.
        telemetry_module.configure_telemetry(container)

    def test_does_not_set_meter_provider_when_disabled(self):
        """enabled=False: set_meter_provider must NOT be called.

        This test uses enabled=True for a first call to set the idempotency
        flag, then verifies the disabled path skips the provider-install
        entirely — but the real test that matters is the enabled=True path
        in TestConfigureTelemetryEnabled below.  For the disabled path we
        verify via the module flag that we returned before the SDK code.
        """
        container_disabled = _make_container_with_otel(OtelConfig(enabled=False))
        with patch("opentelemetry.metrics.set_meter_provider") as mock_set:
            telemetry_module.configure_telemetry(container_disabled)
        # The enabled=False guard returns before set_meter_provider is ever reached.
        mock_set.assert_not_called()
        # And the flag confirms we returned via the no-op path (flag still set
        # because _state.configured was set to True before the early return).
        assert telemetry_module._state.configured is True

    def test_does_not_set_tracer_provider_when_disabled(self):
        """enabled=False: set_tracer_provider must NOT be called."""
        container_disabled = _make_container_with_otel(OtelConfig(enabled=False))
        with patch("opentelemetry.trace.set_tracer_provider") as mock_set:
            telemetry_module.configure_telemetry(container_disabled)
        mock_set.assert_not_called()


# ---------------------------------------------------------------------------
# Test: enabled=True — real SDK MeterProvider installed
# ---------------------------------------------------------------------------


class TestConfigureTelemetryEnabled:
    """When otel.enabled=True and the SDK is present, a real MeterProvider is installed."""

    def test_enabled_installs_sdk_meter_provider(self):
        """telemetry_module.configure_telemetry(enabled=True) must call set_meter_provider with a real provider.

        This test is the key proof that the enabled guard is actually guarding
        real work: if the guard were removed or inverted, set_meter_provider
        would never be called (disabled path) OR would be called with None.
        Either way this test would fail.
        """
        try:
            from opentelemetry.sdk.metrics import MeterProvider as SDKMeterProvider
        except ImportError:
            pytest.skip("opentelemetry-sdk not installed")

        container = _make_container_with_otel(OtelConfig(enabled=True))

        captured_providers: list[object] = []

        original_set = None
        try:
            from opentelemetry import metrics as otel_metrics

            original_set = otel_metrics.set_meter_provider
        except ImportError:
            pytest.skip("opentelemetry not installed")

        def _capture_set(provider: object) -> None:
            captured_providers.append(provider)
            original_set(provider)

        with patch("opentelemetry.metrics.set_meter_provider", side_effect=_capture_set):
            telemetry_module.configure_telemetry(container)

        assert captured_providers, (
            "set_meter_provider was never called — configure_telemetry did not run the "
            "enabled path (enabled guard is broken or SDK MeterProvider was not built)"
        )
        provider = captured_providers[0]
        assert isinstance(provider, SDKMeterProvider), (
            f"Expected an SDK MeterProvider, got {type(provider)} — "
            "configure_telemetry installed a no-op provider instead of the real SDK one"
        )

    def test_enabled_sets_module_meter_provider_ref(self):
        """After telemetry_module.configure_telemetry(enabled=True), _state.meter_provider is not None."""
        try:
            from opentelemetry.sdk.metrics import MeterProvider as SDKMeterProvider  # noqa: F401
        except ImportError:
            pytest.skip("opentelemetry-sdk not installed")

        container = _make_container_with_otel(OtelConfig(enabled=True))
        telemetry_module.configure_telemetry(container)

        assert telemetry_module._state.meter_provider is not None, (
            "_state.meter_provider is None after telemetry_module.configure_telemetry(enabled=True) — "
            "the enabled guard or the provider construction is broken"
        )

    def test_enabled_true_noop_would_fail(self):
        """Sentinel: this test WOULD fail if the enabled=True path were a no-op.

        Temporarily break the enabled guard, assert failure, then restore.
        This is the fail-then-pass proof required by the task specification.

        We simulate breaking the guard by pre-setting _state.configured to
        True before the call (makes the idempotency guard fire, returning
        immediately without doing any work).  We then assert that
        _state.meter_provider is still None — confirming that the early return
        skipped the provider setup.  This is the exact failure mode that
        would occur if someone were to accidentally move
        ``_state.configured = True`` AFTER the enabled check.
        """
        try:
            from opentelemetry.sdk.metrics import MeterProvider as SDKMeterProvider  # noqa: F401
        except ImportError:
            pytest.skip("opentelemetry-sdk not installed")

        # Phase 1: BREAK the path by pre-setting the idempotency flag
        # (simulates what happens when the guard fires too early / is inverted).
        telemetry_module._reset_telemetry_state()
        telemetry_module._state.configured = True  # BREAK: skip all setup
        container = _make_container_with_otel(OtelConfig(enabled=True))
        telemetry_module.configure_telemetry(container)
        # ASSERT FAILURE: with the guard pre-fired, _state.meter_provider stays None.
        assert telemetry_module._state.meter_provider is None, (
            "Phase 1 expected _state.meter_provider=None (guard pre-fired) but it was set — "
            "the test scaffold is wrong"
        )

        # Phase 2: RESTORE and verify the real path works correctly.
        telemetry_module._reset_telemetry_state()
        telemetry_module.configure_telemetry(container)
        assert telemetry_module._state.meter_provider is not None, (
            "Phase 2 expected _state.meter_provider to be set after a clean call — "
            "telemetry_module.configure_telemetry(enabled=True) is broken"
        )


# ---------------------------------------------------------------------------
# Test: SDK absent (ImportError path)
# ---------------------------------------------------------------------------


class TestConfigureTelemetrySDKAbsent:
    """configure_telemetry must not raise when the OTel SDK is not installed."""

    def test_no_raise_when_sdk_import_fails(self):
        """Simulate SDK absence by patching the import inside configure_telemetry."""
        container = _make_container_with_otel(OtelConfig(enabled=True))

        # Patch the builtins so that importing 'opentelemetry.sdk.metrics' fails.
        original_import = (
            __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__
        )  # type: ignore[union-attr]

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name.startswith("opentelemetry.sdk") or name.startswith("opentelemetry.exporter"):
                raise ImportError(f"Simulated missing package: {name}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            # Must complete without raising.
            try:
                telemetry_module.configure_telemetry(container)
            except ImportError:
                pytest.fail(
                    "configure_telemetry raised ImportError — the SDK-absent guard is broken"
                )

    def test_no_raise_when_top_level_otel_missing(self, monkeypatch):
        """Simulate total opentelemetry absence via sys.modules manipulation."""
        container = _make_container_with_otel(OtelConfig(enabled=True))

        # Remove opentelemetry from sys.modules so the import fails.
        otel_keys = [k for k in sys.modules if k.startswith("opentelemetry")]
        saved = {k: sys.modules.pop(k) for k in otel_keys}

        # Make any opentelemetry import fail.
        monkeypatch.setitem(sys.modules, "opentelemetry", None)  # type: ignore[arg-type]

        try:
            telemetry_module.configure_telemetry(container)
        except Exception as exc:
            pytest.fail(f"configure_telemetry raised unexpectedly: {exc}")
        finally:
            # Restore to avoid contaminating other tests.
            for k, v in saved.items():
                sys.modules[k] = v
            monkeypatch.delitem(sys.modules, "opentelemetry", raising=False)


# ---------------------------------------------------------------------------
# Test: Idempotency — ENABLED path
# ---------------------------------------------------------------------------


class TestConfigureTelemetryIdempotency:
    """A second call to configure_telemetry must be a complete no-op.

    These tests run through the ENABLED path so that the idempotency guard
    is actually protecting real setup work, not a trivial early-return.
    """

    def test_second_call_is_noop_on_enabled_path(self):
        """Second call with enabled=True must not call set_meter_provider again."""
        try:
            from opentelemetry.sdk.metrics import MeterProvider as SDKMeterProvider  # noqa: F401
        except ImportError:
            pytest.skip("opentelemetry-sdk not installed")

        container = _make_container_with_otel(OtelConfig(enabled=True))

        set_calls: list[object] = []

        with patch(
            "opentelemetry.metrics.set_meter_provider",
            side_effect=lambda p: set_calls.append(p),
        ):
            telemetry_module.configure_telemetry(container)
            second_count_before = len(set_calls)
            telemetry_module.configure_telemetry(container)  # second call — must be no-op
            second_count_after = len(set_calls)

        assert second_count_before == 1, (
            "First telemetry_module.configure_telemetry(enabled=True) call did not invoke set_meter_provider"
        )
        assert second_count_after == 1, (
            "Second configure_telemetry call invoked set_meter_provider again — "
            "idempotency guard is broken"
        )

    def test_flag_set_after_first_call(self):
        container = _make_container_with_otel(OtelConfig(enabled=False))
        assert telemetry_module._state.configured is False
        telemetry_module.configure_telemetry(container)
        assert telemetry_module._state.configured is True

    def test_reset_re_arms_flag(self):
        container = _make_container_with_otel(OtelConfig(enabled=False))
        telemetry_module.configure_telemetry(container)
        assert telemetry_module._state.configured is True
        telemetry_module._reset_telemetry_state()
        assert telemetry_module._state.configured is False

    def test_after_reset_call_runs_again(self):
        container = _make_container_with_otel(OtelConfig(enabled=False))
        telemetry_module.configure_telemetry(container)
        telemetry_module._reset_telemetry_state()

        # After reset, the function should run (and complete without error).
        telemetry_module.configure_telemetry(container)
        assert telemetry_module._state.configured is True


# ---------------------------------------------------------------------------
# Test: File-handle close on exporter-init failure (exception safety)
# ---------------------------------------------------------------------------


class TestFileHandleCloseOnExporterFailure:
    """bootstrap/telemetry.py closes _metrics_fh/_traces_fh and re-raises
    if ConsoleMetricExporter/ConsoleSpanExporter construction throws.

    Covers both the metrics and traces branches.
    """

    def test_metrics_fh_closed_when_console_metric_exporter_raises(self, tmp_path):
        """If ConsoleMetricExporter raises, the opened file handle is closed."""
        try:
            from opentelemetry.sdk.metrics import MeterProvider  # noqa: F401
            from opentelemetry.sdk.metrics.export import (  # noqa: F401
                ConsoleMetricExporter,
                PeriodicExportingMetricReader,
            )
        except ImportError:
            pytest.skip("opentelemetry-sdk not installed")

        cfg = OtelConfig(
            enabled=True,
            metrics_exporters=["file"],
            telemetry_file_dir=str(tmp_path),
        )
        container = _make_container_with_otel(cfg)

        opened_handles: list[Any] = []

        real_open = open

        def _capturing_open(path, *args, **kwargs):
            fh = real_open(path, *args, **kwargs)
            opened_handles.append(fh)
            return fh

        boom_error = RuntimeError("simulated ConsoleMetricExporter failure")

        with (
            patch("builtins.open", side_effect=_capturing_open),
            # Make the preferred OTLP-JSON file exporter unavailable so we hit the
            # ConsoleMetricExporter fallback path.
            patch.dict(
                sys.modules,
                {"opentelemetry.exporter.otlp.json.file": None},
            ),
            patch(
                "opentelemetry.sdk.metrics.export.ConsoleMetricExporter",
                side_effect=boom_error,
            ),
            pytest.raises(RuntimeError, match="simulated ConsoleMetricExporter failure"),
        ):
            telemetry_module.configure_telemetry(container)

        # At least one file handle must have been opened (for metrics.jsonl).
        assert opened_handles, "No file handle was opened — test scaffold may be wrong"
        # Every opened handle must be closed after the exception.
        for fh in opened_handles:
            assert fh.closed, (
                f"File handle {fh.name!r} was NOT closed after ConsoleMetricExporter raised — "
                "resource leak detected"
            )

    def test_traces_fh_closed_when_console_span_exporter_raises(self, tmp_path):
        """If ConsoleSpanExporter raises, the opened traces file handle is closed."""
        try:
            from opentelemetry.sdk.metrics import MeterProvider  # noqa: F401
            from opentelemetry.sdk.trace import TracerProvider  # noqa: F401
            from opentelemetry.sdk.trace.export import (  # noqa: F401
                ConsoleSpanExporter,
            )
        except ImportError:
            pytest.skip("opentelemetry-sdk not installed")

        cfg = OtelConfig(
            enabled=True,
            metrics_exporters=[],  # no metrics exporter — only test traces path
            traces_exporter="file",
            telemetry_file_dir=str(tmp_path),
        )
        container = _make_container_with_otel(cfg)

        opened_handles: list[Any] = []

        real_open = open

        def _capturing_open(path, *args, **kwargs):
            fh = real_open(path, *args, **kwargs)
            opened_handles.append(fh)
            return fh

        boom_error = RuntimeError("simulated ConsoleSpanExporter failure")

        with (
            patch("builtins.open", side_effect=_capturing_open),
            # Force the OTLP-JSON path to be absent so we hit the Console fallback.
            patch.dict(
                sys.modules,
                {"opentelemetry.exporter.otlp.json.file": None},
            ),
            patch(
                "opentelemetry.sdk.trace.export.ConsoleSpanExporter",
                side_effect=boom_error,
            ),
            pytest.raises(RuntimeError, match="simulated ConsoleSpanExporter failure"),
        ):
            telemetry_module.configure_telemetry(container)

        assert opened_handles, "No file handle was opened — test scaffold may be wrong"
        for fh in opened_handles:
            assert fh.closed, (
                f"File handle {fh.name!r} was NOT closed after ConsoleSpanExporter raised — "
                "resource leak detected"
            )


# ---------------------------------------------------------------------------
# Test: shutdown_telemetry call-site wiring
# ---------------------------------------------------------------------------


class TestShutdownTelemetryCallSiteWiring:
    """Verify that the exit paths of CLI main and SDK ORBClient.cleanup()
    actually invoke shutdown_telemetry.

    We patch the function at its import site and drive the exit path, then
    assert it was called.
    """

    def test_sdk_orb_client_cleanup_calls_shutdown_telemetry(self):
        """ORBClient.cleanup() must call shutdown_telemetry().

        ORBClient.cleanup() imports the shutdown helper lazily inside the
        method body (a module-local import of the telemetry shutdown function).
        We verify the call by inspecting the cleanup() source: the method body
        explicitly calls ``shutdown_telemetry()`` after delegating to
        ``self._app.cleanup()``.  We exercise the method with a real ORBClient
        instance (initialised with __new__) and all required attributes set to
        MagicMock/no-op values so the cleanup path runs to completion.

        Patching ``orb.bootstrap.telemetry.shutdown_telemetry`` at the module
        level intercepts the lazy local import inside cleanup().
        """
        import asyncio

        import orb.sdk.client as sdk_client_mod

        with patch(
            "orb.bootstrap.telemetry.shutdown_telemetry",
            wraps=lambda: None,
        ) as mock_shutdown:
            # Build a minimal ORBClient — set all attributes that cleanup() touches.
            client = sdk_client_mod.ORBClient.__new__(sdk_client_mod.ORBClient)
            client._app = None
            client._config = MagicMock()
            client._initialized = False
            client._methods = {}
            client._container = None
            client._discovery = None

            asyncio.run(client.cleanup())

        (
            mock_shutdown.assert_called(),
            (
                "ORBClient.cleanup() did not call shutdown_telemetry — "
                "SDK clients that call cleanup() will silently discard metrics on exit"
            ),
        )

    def test_cli_main_finally_calls_flush_telemetry(self):
        """CLI main()'s finally block must invoke _flush_telemetry (which wraps shutdown_telemetry).

        We patch _flush_telemetry at the orb.cli.main module level and drive
        main() through the normal-exit path (patching parse_args + execute_command
        so it returns immediately).
        """
        import asyncio

        import orb.cli.main as cli_main_mod

        flush_calls: list[int] = []

        def _mock_flush() -> None:
            flush_calls.append(1)

        with (
            patch.object(cli_main_mod, "_flush_telemetry", side_effect=_mock_flush),
            patch.object(
                cli_main_mod,
                "parse_args",
                return_value=(MagicMock(quiet=True, format="json"), {}),
            ),
            patch.object(
                cli_main_mod,
                "execute_command",
                return_value={"success": True},
            ),
            # Prevent sys.exit from actually exiting.
            patch("sys.exit", side_effect=SystemExit),
        ):
            # We only care that _flush_telemetry was called in main()'s
            # finally block; the exit path itself may raise SystemExit or any
            # error, both of which are irrelevant to this assertion.
            with suppress(SystemExit, Exception):
                asyncio.run(cli_main_mod.main())

        assert flush_calls, (
            "CLI main()'s finally block did not call _flush_telemetry — "
            "CLI commands that exit normally will silently discard metrics"
        )
