"""Tests for HealthCheck.__init__ PermissionError fallback chain."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from orb.domain.base.ports.logging_port import LoggingPort
from orb.monitoring.health import HealthCheck, HealthCheckConfig


def _config(tmp_path: Path) -> HealthCheckConfig:
    return HealthCheckConfig(health_dir=tmp_path / "health")


def test_health_check_init_falls_back_to_home_orb_on_permission_error(tmp_path: Path) -> None:
    """Primary mkdir raises PermissionError; fallback to ~/.orb/work/health succeeds."""
    config = _config(tmp_path)
    call_count = 0

    def mkdir_side_effect(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise PermissionError("denied")
        # second call (fallback) succeeds silently

    with patch.object(Path, "mkdir", side_effect=mkdir_side_effect):
        hc = HealthCheck(config=config)

    assert hc.health_dir == Path.home() / ".orb" / "work" / "health"


def test_health_check_init_falls_back_to_tempdir_on_double_permission_error(tmp_path: Path) -> None:
    """Both primary and secondary mkdir raise PermissionError; fallback to tempdir."""
    config = _config(tmp_path)

    with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
        hc = HealthCheck(config=config)

    assert str(hc.health_dir).startswith(tempfile.gettempdir())


def test_health_check_init_permission_error_with_none_logger_does_not_raise(tmp_path: Path) -> None:
    """PermissionError with logger=None must not raise AttributeError."""
    config = _config(tmp_path)
    call_count = 0

    def mkdir_side_effect(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise PermissionError("denied")

    with patch.object(Path, "mkdir", side_effect=mkdir_side_effect):
        hc = HealthCheck(config=config, logger=None)

    # Should have fallen back without raising
    assert hc.health_dir == Path.home() / ".orb" / "work" / "health"


def test_health_check_init_logs_warning_via_injected_logger_on_permission_error(
    tmp_path: Path,
) -> None:
    """Injected LoggingPort.warning is called when PermissionError occurs."""
    config = _config(tmp_path)
    mock_logger = Mock(spec=LoggingPort)
    call_count = 0

    def mkdir_side_effect(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise PermissionError("denied")

    with patch.object(Path, "mkdir", side_effect=mkdir_side_effect):
        HealthCheck(config=config, logger=mock_logger)

    mock_logger.warning.assert_called()
