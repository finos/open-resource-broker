"""Unit tests for StartupValidator — covering uncovered branches."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orb.config.schemas.app_schema import AppConfig
from orb.config.schemas.provider_strategy_schema import ProviderConfig, ProviderInstanceConfig
from orb.infrastructure.validation.startup_validator import StartupValidator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_console():
    c = MagicMock()
    c.error = MagicMock()
    c.info = MagicMock()
    c.warning = MagicMock()
    c.command = MagicMock()
    return c


def _make_app_config() -> AppConfig:
    p = ProviderInstanceConfig(name="p1", type="aws")  # type: ignore[call-arg]
    return AppConfig(provider=ProviderConfig(providers=[p]))  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# validate_startup — SystemExit paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_startup_exits_when_no_config_file(tmp_path: Path, monkeypatch) -> None:
    """validate_startup calls sys.exit(1) when config file is not found."""
    monkeypatch.delenv("ORB_CONFIG_FILE", raising=False)
    monkeypatch.delenv("ORB_CONFIG_DIR", raising=False)
    with patch(
        "orb.config.platform_dirs.get_config_location", return_value=tmp_path / "nonexistent"
    ):
        with patch("pathlib.Path.home", return_value=tmp_path / "nonexistent_home"):
            validator = StartupValidator(
                config_path=str(tmp_path / "missing.json"),
                console=_make_console(),
            )
            with pytest.raises(SystemExit) as exc_info:
                validator.validate_startup()
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_validate_startup_exits_on_invalid_json(tmp_path: Path) -> None:
    """validate_startup calls sys.exit(1) when config file contains invalid JSON."""
    bad_json = tmp_path / "config.json"
    bad_json.write_text("{not valid json}")
    validator = StartupValidator(config_path=str(bad_json), console=_make_console())
    with pytest.raises(SystemExit) as exc_info:
        validator.validate_startup()
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_validate_startup_exits_on_pydantic_validation_error(tmp_path: Path) -> None:
    """validate_startup calls sys.exit(1) when config fails Pydantic validation."""
    # Write JSON that parses but doesn't satisfy AppConfig schema (missing required 'provider')
    bad_config = tmp_path / "config.json"
    bad_config.write_text(json.dumps({"logging": {"invalid_key": "bad_value"}}))
    validator = StartupValidator(config_path=str(bad_config), console=_make_console())
    with pytest.raises(SystemExit) as exc_info:
        validator.validate_startup()
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_validate_startup_reraises_systemexit(tmp_path: Path) -> None:
    """validate_startup re-raises SystemExit rather than catching it."""
    # Config that passes Pydantic validation
    from orb.config.schemas.provider_strategy_schema import ProviderConfig, ProviderInstanceConfig

    good_config = tmp_path / "config.json"
    p = ProviderInstanceConfig(name="p1", type="aws")  # type: ignore[call-arg]
    cfg_obj = AppConfig(provider=ProviderConfig(providers=[p]))  # type: ignore[call-arg]
    good_config.write_text(cfg_obj.model_dump_json())
    validator = StartupValidator(config_path=str(good_config), console=_make_console())
    # Intercept _validate_important to raise SystemExit
    with patch.object(validator, "_validate_important", side_effect=SystemExit(42)):
        with pytest.raises(SystemExit) as exc_info:
            validator.validate_startup()
        assert exc_info.value.code == 42


@pytest.mark.unit
def test_validate_startup_unexpected_exception_exits(tmp_path: Path) -> None:
    """validate_startup wraps unexpected exceptions and exits 1."""
    valid_config = tmp_path / "config.json"
    valid_config.write_text(json.dumps({}))
    validator = StartupValidator(config_path=str(valid_config), console=_make_console())
    with patch.object(validator, "_validate_important", side_effect=RuntimeError("boom")):
        with pytest.raises(SystemExit) as exc_info:
            validator.validate_startup()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _validate_critical — file-read-error path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_critical_exits_on_file_read_error(tmp_path: Path) -> None:
    """_validate_critical exits when open() raises a generic OSError."""
    config_file = tmp_path / "config.json"
    config_file.write_text("{}")
    validator = StartupValidator(config_path=str(config_file), console=_make_console())
    with patch("builtins.open", side_effect=OSError("permission denied")):
        with pytest.raises(SystemExit) as exc_info:
            validator._validate_critical()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _find_config_file — discovery hierarchy
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_find_config_file_returns_true_for_existing_path(tmp_path: Path) -> None:
    """_find_config_file returns True when config_path points to an existing file."""
    f = tmp_path / "config.json"
    f.write_text("{}")
    validator = StartupValidator(config_path=str(f), console=_make_console())
    assert validator._find_config_file() is True


@pytest.mark.unit
def test_find_config_file_uses_env_config_file(tmp_path: Path, monkeypatch) -> None:
    """_find_config_file discovers config via ORB_CONFIG_FILE env var."""
    f = tmp_path / "via_env.json"
    f.write_text("{}")
    monkeypatch.setenv("ORB_CONFIG_FILE", str(f))
    validator = StartupValidator(console=_make_console())
    assert validator._find_config_file() is True
    assert validator.config_path == str(f)


@pytest.mark.unit
def test_find_config_file_uses_env_config_dir(tmp_path: Path, monkeypatch) -> None:
    """_find_config_file discovers config via ORB_CONFIG_DIR env var."""
    f = tmp_path / "config.json"
    f.write_text("{}")
    monkeypatch.setenv("ORB_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("ORB_CONFIG_FILE", raising=False)
    validator = StartupValidator(console=_make_console())
    assert validator._find_config_file() is True
    assert validator.config_path == str(f)


@pytest.mark.unit
def test_find_config_file_returns_false_when_nothing_found(tmp_path: Path, monkeypatch) -> None:
    """_find_config_file returns False when no candidate exists."""
    monkeypatch.delenv("ORB_CONFIG_FILE", raising=False)
    monkeypatch.delenv("ORB_CONFIG_DIR", raising=False)
    with patch(
        "orb.config.platform_dirs.get_config_location", return_value=tmp_path / "nonexistent"
    ):
        with patch("pathlib.Path.home", return_value=tmp_path / "nonexistent_home"):
            validator = StartupValidator(console=_make_console())
            assert validator._find_config_file() is False


# ---------------------------------------------------------------------------
# _validate_important
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_important_warns_when_no_default_config(tmp_path: Path) -> None:
    """_validate_important emits info when default config template is missing."""
    console = _make_console()
    validator = StartupValidator(console=console)
    validator.app_config = _make_app_config()
    with patch.object(validator, "_check_default_config", return_value=False):
        with patch.object(validator, "_check_templates_file", return_value=True):
            with patch.object(validator, "_check_provider_credentials", return_value=True):
                validator._validate_important()
    console.info.assert_called()


@pytest.mark.unit
def test_validate_important_warns_when_no_templates_file(tmp_path: Path) -> None:
    """_validate_important emits info when templates file is missing."""
    console = _make_console()
    validator = StartupValidator(console=console)
    validator.app_config = _make_app_config()
    with patch.object(validator, "_check_default_config", return_value=True):
        with patch.object(validator, "_check_templates_file", return_value=False):
            with patch.object(validator, "_check_provider_credentials", return_value=True):
                validator._validate_important()
    console.info.assert_called()


@pytest.mark.unit
def test_validate_important_warns_on_missing_credentials(tmp_path: Path) -> None:
    """_validate_important emits warning when credentials check fails."""
    console = _make_console()
    validator = StartupValidator(console=console)
    validator.app_config = _make_app_config()
    with patch.object(validator, "_check_default_config", return_value=True):
        with patch.object(validator, "_check_templates_file", return_value=True):
            with patch.object(validator, "_check_provider_credentials", return_value=False):
                validator._validate_important()
    console.warning.assert_called()


# ---------------------------------------------------------------------------
# _check_templates_file
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_templates_file_returns_false_when_no_app_config() -> None:
    """_check_templates_file returns False if app_config is not set."""
    validator = StartupValidator(console=_make_console())
    validator.app_config = None
    assert validator._check_templates_file() is False


@pytest.mark.unit
def test_check_templates_file_uses_injected_scheduler_port(tmp_path: Path) -> None:
    """_check_templates_file uses injected scheduler_port for template path lookup."""
    template_file = tmp_path / "templates.json"
    template_file.write_text("{}")
    mock_scheduler = MagicMock()
    mock_scheduler.get_template_paths.return_value = [str(template_file)]
    validator = StartupValidator(
        console=_make_console(),
        scheduler_port=mock_scheduler,
    )
    validator.app_config = _make_app_config()
    assert validator._check_templates_file() is True


@pytest.mark.unit
def test_check_templates_file_returns_false_when_paths_missing(tmp_path: Path) -> None:
    """_check_templates_file returns False when all template paths are missing."""
    mock_scheduler = MagicMock()
    mock_scheduler.get_template_paths.return_value = [str(tmp_path / "nope.json")]
    validator = StartupValidator(
        console=_make_console(),
        scheduler_port=mock_scheduler,
    )
    validator.app_config = _make_app_config()
    assert validator._check_templates_file() is False


@pytest.mark.unit
def test_check_templates_file_falls_back_to_container_on_no_scheduler(tmp_path: Path) -> None:
    """_check_templates_file falls back to DI container when no scheduler injected."""
    template_file = tmp_path / "templates.json"
    template_file.write_text("{}")
    mock_scheduler = MagicMock()
    mock_scheduler.get_template_paths.return_value = [str(template_file)]

    validator = StartupValidator(console=_make_console())
    validator.app_config = _make_app_config()

    with patch("orb.infrastructure.di.container.get_container") as mock_get_container:
        mock_container = MagicMock()
        mock_container.get.return_value = mock_scheduler
        mock_get_container.return_value = mock_container
        result = validator._check_templates_file()

    assert result is True


@pytest.mark.unit
def test_check_templates_file_returns_false_on_container_exception() -> None:
    """_check_templates_file returns False when DI container raises."""
    validator = StartupValidator(console=_make_console())
    validator.app_config = _make_app_config()
    with patch(
        "orb.infrastructure.di.container.get_container", side_effect=Exception("DI not ready")
    ):
        assert validator._check_templates_file() is False


# ---------------------------------------------------------------------------
# _check_default_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_default_config_returns_true_when_resource_file_exists() -> None:
    """_check_default_config returns True when importlib.resources finds the file."""
    validator = StartupValidator(console=_make_console())
    mock_file = MagicMock()
    mock_file.is_file.return_value = True
    mock_traversable = MagicMock()
    mock_traversable.joinpath.return_value = mock_file

    with patch("importlib.resources.files", return_value=mock_traversable):
        result = validator._check_default_config()

    assert result is True


@pytest.mark.unit
def test_check_default_config_returns_false_when_resource_file_missing() -> None:
    """_check_default_config returns False when resource file does not exist."""
    validator = StartupValidator(console=_make_console())
    mock_file = MagicMock()
    mock_file.is_file.return_value = False
    mock_traversable = MagicMock()
    mock_traversable.joinpath.return_value = mock_file

    with patch("importlib.resources.files", return_value=mock_traversable):
        result = validator._check_default_config()

    assert result is False


@pytest.mark.unit
def test_check_default_config_returns_false_on_exception() -> None:
    """_check_default_config returns False when importlib.resources raises."""
    validator = StartupValidator(console=_make_console())
    with patch("importlib.resources.files", side_effect=Exception("not found")):
        result = validator._check_default_config()
    assert result is False


# ---------------------------------------------------------------------------
# _check_provider_credentials
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_provider_credentials_returns_true_when_no_providers() -> None:
    """Returns True (skip) when provider list is empty."""
    from orb.config.schemas.provider_strategy_schema import ProviderConfig

    checker = MagicMock(return_value=False)
    validator = StartupValidator(credentials_checker=checker, console=_make_console())
    validator.app_config = AppConfig(provider=ProviderConfig(providers=[]))  # type: ignore[call-arg]
    result = validator._check_provider_credentials()
    assert result is True
    checker.assert_not_called()


@pytest.mark.unit
def test_check_provider_credentials_returns_true_on_unexpected_exception() -> None:
    """Returns True (don't fail) when unexpected error occurs."""

    def _bad_checker(providers):
        raise RuntimeError("unexpected")

    validator = StartupValidator(credentials_checker=_bad_checker, console=_make_console())
    validator.app_config = _make_app_config()
    result = validator._check_provider_credentials()
    assert result is True


@pytest.mark.unit
def test_check_provider_credentials_returns_false_when_no_app_config() -> None:
    """Returns False when app_config is not set."""
    validator = StartupValidator(credentials_checker=MagicMock(), console=_make_console())
    validator.app_config = None
    result = validator._check_provider_credentials()
    assert result is False


# ---------------------------------------------------------------------------
# _print_config_help
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_print_config_help_includes_env_file_when_set(monkeypatch) -> None:
    """_print_config_help includes ORB_CONFIG_FILE path in output when set."""
    monkeypatch.setenv("ORB_CONFIG_FILE", "/tmp/test_config.json")
    monkeypatch.delenv("ORB_CONFIG_DIR", raising=False)
    console = _make_console()
    validator = StartupValidator(console=console)

    with patch("orb.config.platform_dirs.get_config_location", return_value=Path("/fake/location")):
        validator._print_config_help()

    all_info_calls = [str(call) for call in console.info.call_args_list]
    assert any("/tmp/test_config.json" in c for c in all_info_calls)


@pytest.mark.unit
def test_print_config_help_includes_env_dir_when_set(monkeypatch) -> None:
    """_print_config_help includes ORB_CONFIG_DIR/config.json when set."""
    monkeypatch.setenv("ORB_CONFIG_DIR", "/tmp/test_config_dir")
    monkeypatch.delenv("ORB_CONFIG_FILE", raising=False)
    console = _make_console()
    validator = StartupValidator(console=console)

    with patch("orb.config.platform_dirs.get_config_location", return_value=Path("/fake/location")):
        validator._print_config_help()

    all_info_calls = [str(call) for call in console.info.call_args_list]
    assert any("/tmp/test_config_dir" in c for c in all_info_calls)


# ---------------------------------------------------------------------------
# _error / _warn
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_error_delegates_to_console() -> None:
    """_error calls console.error."""
    console = _make_console()
    validator = StartupValidator(console=console)
    validator._error("test error")
    console.error.assert_called_once_with("test error")


@pytest.mark.unit
def test_warn_delegates_to_console() -> None:
    """_warn calls console.warning."""
    console = _make_console()
    validator = StartupValidator(console=console)
    validator._warn("test warning")
    console.warning.assert_called_once_with("test warning")


# ---------------------------------------------------------------------------
# Happy-path integration: valid config, all checks pass
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_startup_succeeds_with_valid_config(tmp_path: Path) -> None:
    """validate_startup completes without exit when config is valid and checks pass."""
    from orb.config.schemas.provider_strategy_schema import ProviderConfig, ProviderInstanceConfig

    p = ProviderInstanceConfig(name="p1", type="aws")  # type: ignore[call-arg]
    cfg = AppConfig(provider=ProviderConfig(providers=[p]))  # type: ignore[call-arg]
    config_file = tmp_path / "config.json"
    config_file.write_text(cfg.model_dump_json())
    console = _make_console()
    validator = StartupValidator(config_path=str(config_file), console=console)

    with patch.object(validator, "_validate_important"):
        validator.validate_startup()  # should not raise

    assert validator.app_config is not None
