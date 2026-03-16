"""Unit tests for platform_dirs ORB_ROOT_DIR precedence."""

from pathlib import Path

import pytest

from orb.config.platform_dirs import (
    get_config_location,
    get_health_location,
    get_logs_location,
    get_root_location,
    get_scripts_location,
    get_work_location,
)

# ---------------------------------------------------------------------------
# get_config_location
# ---------------------------------------------------------------------------


def test_config_per_dir_env_wins_over_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORB_ROOT_DIR", "/root")
    monkeypatch.setenv("ORB_CONFIG_DIR", "/explicit/config")
    assert get_config_location() == Path("/explicit/config")


def test_config_root_dir_used_when_no_per_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORB_ROOT_DIR", "/root")
    monkeypatch.delenv("ORB_CONFIG_DIR", raising=False)
    assert get_config_location() == Path("/root/config")


def test_config_platform_fallback_when_no_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORB_ROOT_DIR", raising=False)
    monkeypatch.delenv("ORB_CONFIG_DIR", raising=False)
    # Should return *something* without raising
    result = get_config_location()
    assert isinstance(result, Path)
    assert result != Path("/root/config")


# ---------------------------------------------------------------------------
# get_work_location
# ---------------------------------------------------------------------------


def test_work_per_dir_env_wins_over_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORB_ROOT_DIR", "/root")
    monkeypatch.setenv("ORB_WORK_DIR", "/explicit/work")
    assert get_work_location() == Path("/explicit/work")


def test_work_root_dir_used_when_no_per_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORB_ROOT_DIR", "/root")
    monkeypatch.delenv("ORB_WORK_DIR", raising=False)
    assert get_work_location() == Path("/root/work")


def test_work_sibling_of_config_when_no_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORB_ROOT_DIR", raising=False)
    monkeypatch.delenv("ORB_WORK_DIR", raising=False)
    monkeypatch.setenv("ORB_CONFIG_DIR", "/some/config")
    assert get_work_location() == Path("/some/work")


# ---------------------------------------------------------------------------
# get_logs_location
# ---------------------------------------------------------------------------


def test_logs_per_dir_env_wins_over_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORB_ROOT_DIR", "/root")
    monkeypatch.setenv("ORB_LOG_DIR", "/explicit/logs")
    assert get_logs_location() == Path("/explicit/logs")


def test_logs_root_dir_used_when_no_per_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORB_ROOT_DIR", "/root")
    monkeypatch.delenv("ORB_LOG_DIR", raising=False)
    assert get_logs_location() == Path("/root/logs")


def test_logs_sibling_of_config_when_no_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORB_ROOT_DIR", raising=False)
    monkeypatch.delenv("ORB_LOG_DIR", raising=False)
    monkeypatch.setenv("ORB_CONFIG_DIR", "/some/config")
    assert get_logs_location() == Path("/some/logs")


# ---------------------------------------------------------------------------
# get_scripts_location
# ---------------------------------------------------------------------------


def test_scripts_root_dir_used_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORB_ROOT_DIR", "/root")
    assert get_scripts_location() == Path("/root/scripts")


def test_scripts_sibling_of_config_when_no_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORB_ROOT_DIR", raising=False)
    monkeypatch.setenv("ORB_CONFIG_DIR", "/some/config")
    assert get_scripts_location() == Path("/some/scripts")


# ---------------------------------------------------------------------------
# get_health_location
# ---------------------------------------------------------------------------


def test_health_per_dir_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORB_HEALTH_DIR", "/explicit/health")
    monkeypatch.setenv("ORB_ROOT_DIR", "/root")
    assert get_health_location() == Path("/explicit/health")


def test_health_root_dir_used_when_no_per_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORB_HEALTH_DIR", raising=False)
    monkeypatch.setenv("ORB_ROOT_DIR", "/root")
    assert get_health_location() == Path("/root/work/health")


def test_health_sibling_of_config_when_no_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORB_HEALTH_DIR", raising=False)
    monkeypatch.delenv("ORB_ROOT_DIR", raising=False)
    monkeypatch.setenv("ORB_CONFIG_DIR", "/some/config")
    assert get_health_location() == Path("/some/work/health")


def test_health_fallback_returns_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORB_HEALTH_DIR", raising=False)
    monkeypatch.delenv("ORB_ROOT_DIR", raising=False)
    monkeypatch.delenv("ORB_CONFIG_DIR", raising=False)
    result = get_health_location()
    assert isinstance(result, Path)
    assert result.name == "health"


def test_health_no_env_vars_returns_health_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORB_HEALTH_DIR", raising=False)
    monkeypatch.delenv("ORB_ROOT_DIR", raising=False)
    monkeypatch.delenv("ORB_CONFIG_DIR", raising=False)
    result = get_health_location()
    # Must end with 'health' regardless of platform detection
    assert result.name == "health"


def test_root_orb_root_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORB_ROOT_DIR", "/root")
    monkeypatch.delenv("ORB_CONFIG_DIR", raising=False)
    assert get_root_location() == Path("/root")


def test_root_config_dir_infers_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORB_ROOT_DIR", raising=False)
    monkeypatch.setenv("ORB_CONFIG_DIR", "/some/config")
    assert get_root_location() == Path("/some")
