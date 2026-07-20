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
    resolve_config_file,
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


# ---------------------------------------------------------------------------
# resolve_config_file — single source of truth for config-file discovery
# ---------------------------------------------------------------------------


def test_resolve_config_file_explicit_path_wins(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("ORB_CONFIG_FILE", raising=False)
    monkeypatch.delenv("ORB_CONFIG_DIR", raising=False)
    explicit = tmp_path / "explicit.json"
    explicit.write_text("{}")
    assert resolve_config_file("config.json", explicit_path=str(explicit)) == explicit


def test_resolve_config_file_env_file_wins_over_config_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    env_file = tmp_path / "envfile.json"
    env_file.write_text("{}")
    cfg_dir = tmp_path / "cfgdir"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text("{}")
    monkeypatch.setenv("ORB_CONFIG_FILE", str(env_file))
    monkeypatch.setenv("ORB_CONFIG_DIR", str(cfg_dir))
    assert resolve_config_file("config.json") == env_file


def test_resolve_config_file_config_dir_used_when_no_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("ORB_CONFIG_FILE", raising=False)
    cfg_dir = tmp_path / "cfgdir"
    cfg_dir.mkdir()
    target = cfg_dir / "config.json"
    target.write_text("{}")
    monkeypatch.setenv("ORB_CONFIG_DIR", str(cfg_dir))
    assert resolve_config_file("config.json") == target


def test_resolve_config_file_returns_none_when_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("ORB_CONFIG_FILE", raising=False)
    monkeypatch.setenv("ORB_CONFIG_DIR", str(tmp_path / "empty"))
    monkeypatch.setattr("orb.config.platform_dirs.get_config_location", lambda: tmp_path / "nope")
    # Home fallback almost certainly absent under tmp; explicit path missing too.
    assert resolve_config_file("config.json", explicit_path=str(tmp_path / "x.json")) is None


def test_resolve_config_file_honours_custom_filename(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("ORB_CONFIG_FILE", raising=False)
    cfg_dir = tmp_path / "cfgdir"
    cfg_dir.mkdir()
    target = cfg_dir / "awsprov_templates.json"
    target.write_text("{}")
    monkeypatch.setenv("ORB_CONFIG_DIR", str(cfg_dir))
    assert resolve_config_file("awsprov_templates.json") == target
