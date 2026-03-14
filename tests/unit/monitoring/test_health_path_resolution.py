"""Unit tests for HealthCheck directory path resolution."""

from pathlib import Path
from unittest.mock import patch

import pytest

from orb.config.platform_dirs import get_health_location
from orb.monitoring.health import HealthCheck, HealthCheckConfig


def _make_health_check(health_dir: Path | None = None) -> HealthCheck:
    """Create a HealthCheck with background checker disabled and mkdir patched."""
    config = HealthCheckConfig(
        health_dir=health_dir or get_health_location(),
    )
    with patch("pathlib.Path.mkdir"):
        return HealthCheck(config=config)


def test_health_check_uses_get_health_location_when_no_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no explicit health_dir, HealthCheck uses get_health_location()."""
    monkeypatch.setenv("ORB_HEALTH_DIR", "/from/env/health")
    monkeypatch.delenv("ORB_ROOT_DIR", raising=False)
    hc = _make_health_check()
    assert hc.health_dir == Path("/from/env/health")


def test_health_dir_config_overrides_get_health_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit health_dir in HealthCheckConfig takes precedence over env vars."""
    monkeypatch.setenv("ORB_HEALTH_DIR", "/from/env/health")
    hc = _make_health_check(health_dir=Path("/explicit/config/health"))
    assert hc.health_dir == Path("/explicit/config/health")


def test_orb_root_dir_flows_through_to_health_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ORB_ROOT_DIR is respected when no ORB_HEALTH_DIR is set."""
    monkeypatch.setenv("ORB_ROOT_DIR", "/myroot")
    monkeypatch.delenv("ORB_HEALTH_DIR", raising=False)
    hc = _make_health_check()
    assert hc.health_dir == Path("/myroot/health")


def test_orb_health_dir_flows_through_to_health_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ORB_HEALTH_DIR env var is respected when no explicit health_dir is set."""
    monkeypatch.setenv("ORB_HEALTH_DIR", "/dedicated/health")
    monkeypatch.delenv("ORB_ROOT_DIR", raising=False)
    hc = _make_health_check()
    assert hc.health_dir == Path("/dedicated/health")
