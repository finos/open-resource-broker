"""Comprehensive tests for platform_dirs.py - all functions and edge cases."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config.platform_dirs import (
    get_config_location,
    get_logs_location,
    get_scripts_location,
    get_work_location,
    in_virtualenv,
    is_system_install,
    is_user_install,
)


class TestInVirtualenv:
    """Test virtual environment detection."""

    def test_in_virtualenv_true(self):
        """Test detection when in virtual environment."""
        with (
            patch.object(sys, "prefix", "/path/to/venv"),
            patch.object(sys, "base_prefix", "/usr/local"),
        ):
            assert in_virtualenv() is True

    def test_in_virtualenv_false(self):
        """Test detection when not in virtual environment."""
        with (
            patch.object(sys, "prefix", "/usr/local"),
            patch.object(sys, "base_prefix", "/usr/local"),
        ):
            assert in_virtualenv() is False

    def test_in_virtualenv_conda(self):
        """Test detection in conda environment."""
        with (
            patch.object(sys, "prefix", "/opt/conda/envs/myenv"),
            patch.object(sys, "base_prefix", "/opt/conda"),
        ):
            assert in_virtualenv() is True


class TestIsUserInstall:
    """Test user installation detection."""

    def test_is_user_install_true(self):
        """Test detection when installed with --user."""
        with (
            patch("pathlib.Path.home") as mock_home,
            patch.object(sys, "prefix", "/home/user/.local"),
        ):
            mock_home.return_value = Path("/home/user")
            assert is_user_install() is True

    def test_is_user_install_false_system(self):
        """Test detection when system install."""
        with (
            patch("pathlib.Path.home") as mock_home,
            patch.object(sys, "prefix", "/usr/local"),
        ):
            mock_home.return_value = Path("/home/user")
            assert is_user_install() is False

    def test_is_user_install_false_venv(self):
        """Test detection when in virtual environment."""
        with (
            patch("pathlib.Path.home") as mock_home,
            patch.object(sys, "prefix", "/project/.venv"),
        ):
            mock_home.return_value = Path("/home/user")
            assert is_user_install() is False

    def test_is_user_install_windows(self):
        """Test detection on Windows user install."""
        with (
            patch("pathlib.Path.home") as mock_home,
            patch.object(sys, "prefix", r"C:\Users\user\AppData\Roaming\Python"),
        ):
            mock_home.return_value = Path(r"C:\Users\user")
            assert is_user_install() is True


class TestIsSystemInstall:
    """Test system installation detection."""

    def test_is_system_install_usr(self):
        """Test detection for /usr prefix."""
        with patch.object(sys, "prefix", "/usr/local"):
            assert is_system_install() is True

    def test_is_system_install_opt(self):
        """Test detection for /opt prefix."""
        with patch.object(sys, "prefix", "/opt/python3.11"):
            assert is_system_install() is True

    def test_is_system_install_false_home(self):
        """Test detection when not system install."""
        with patch.object(sys, "prefix", "/home/user/.local"):
            assert is_system_install() is False

    def test_is_system_install_false_venv(self):
        """Test detection when in virtual environment."""
        with patch.object(sys, "prefix", "/project/.venv"):
            assert is_system_install() is False


class TestGetConfigLocation:
    """Test configuration directory location detection."""

    def test_env_override_orb_config_dir(self):
        """Test ORB_CONFIG_DIR environment variable override."""
        with patch.dict(os.environ, {"ORB_CONFIG_DIR": "/custom/config"}):
            result = get_config_location()
            assert result == Path("/custom/config")

    def test_env_override_empty_string(self):
        """Test empty ORB_CONFIG_DIR is ignored."""
        with patch.dict(os.environ, {"ORB_CONFIG_DIR": ""}, clear=True):
            with (
                patch("pathlib.Path.cwd") as mock_cwd,
                patch("pathlib.Path.exists") as mock_exists,
            ):
                mock_cwd.return_value = Path("/fallback")
                mock_exists.return_value = False
                result = get_config_location()
                assert result == Path("/fallback/config")

    def test_development_mode_current_dir(self):
        """Test development mode: pyproject.toml in current directory."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("pathlib.Path.cwd") as mock_cwd,
            patch("pathlib.Path.exists") as mock_exists,
        ):
            mock_cwd.return_value = Path("/dev/project")
            mock_exists.side_effect = lambda: True  # pyproject.toml exists

            result = get_config_location()
            assert result == Path("/dev/project/config")

    def test_development_mode_parent_dir(self):
        """Test development mode: pyproject.toml in parent directory."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("pathlib.Path.cwd") as mock_cwd,
        ):
            mock_cwd.return_value = Path("/dev/project/subdir")

            # Mock exists to return True only for parent/pyproject.toml
            def mock_exists(self):
                return str(self).endswith("/dev/project/pyproject.toml")

            with patch("pathlib.Path.exists", mock_exists):
                result = get_config_location()
                assert result == Path("/dev/project/config")

    def test_user_install(self):
        """Test user installation detection."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("pathlib.Path.cwd") as mock_cwd,
            patch("pathlib.Path.exists") as mock_exists,
            patch("pathlib.Path.home") as mock_home,
            patch.object(sys, "prefix", "/home/user/.local"),
        ):
            mock_cwd.return_value = Path("/somewhere")
            mock_exists.return_value = False
            mock_home.return_value = Path("/home/user")

            result = get_config_location()
            assert result == Path("/home/user/.local/orb/config")

    def test_system_install_usr(self):
        """Test system installation in /usr."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("pathlib.Path.cwd") as mock_cwd,
            patch("pathlib.Path.exists") as mock_exists,
            patch("pathlib.Path.home") as mock_home,
            patch.object(sys, "prefix", "/usr/local"),
        ):
            mock_cwd.return_value = Path("/somewhere")
            mock_exists.return_value = False
            mock_home.return_value = Path("/home/user")

            result = get_config_location()
            assert result == Path("/usr/local/orb/config")

    def test_system_install_opt(self):
        """Test system installation in /opt."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("pathlib.Path.cwd") as mock_cwd,
            patch("pathlib.Path.exists") as mock_exists,
            patch("pathlib.Path.home") as mock_home,
            patch.object(sys, "prefix", "/opt/python3.11"),
        ):
            mock_cwd.return_value = Path("/somewhere")
            mock_exists.return_value = False
            mock_home.return_value = Path("/home/user")

            result = get_config_location()
            assert result == Path("/opt/python3.11/orb/config")

    def test_virtualenv(self):
        """Test virtual environment detection."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("pathlib.Path.cwd") as mock_cwd,
            patch("pathlib.Path.exists") as mock_exists,
            patch("pathlib.Path.home") as mock_home,
            patch.object(sys, "prefix", "/project/.venv"),
            patch.object(sys, "base_prefix", "/usr/local"),
        ):
            mock_cwd.return_value = Path("/somewhere")
            mock_exists.return_value = False
            mock_home.return_value = Path("/home/user")

            result = get_config_location()
            assert result == Path("/project/config")

    def test_fallback(self):
        """Test fallback to current directory."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("pathlib.Path.cwd") as mock_cwd,
            patch("pathlib.Path.exists") as mock_exists,
            patch("pathlib.Path.home") as mock_home,
            patch.object(sys, "prefix", "/random/path"),
            patch.object(sys, "base_prefix", "/random/path"),
        ):
            mock_cwd.return_value = Path("/fallback/dir")
            mock_exists.return_value = False
            mock_home.return_value = Path("/home/user")

            result = get_config_location()
            assert result == Path("/fallback/dir/config")


class TestGetWorkLocation:
    """Test work directory location detection."""

    def test_env_override_orb_work_dir(self):
        """Test ORB_WORK_DIR environment variable override."""
        with patch.dict(os.environ, {"ORB_WORK_DIR": "/custom/work"}):
            result = get_work_location()
            assert result == Path("/custom/work")

    def test_relative_to_config(self):
        """Test work location relative to config location."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("src.config.platform_dirs.get_config_location") as mock_config,
        ):
            mock_config.return_value = Path("/base/config")

            result = get_work_location()
            assert result == Path("/base/work")

    def test_env_override_empty_string(self):
        """Test empty ORB_WORK_DIR is ignored."""
        with (
            patch.dict(os.environ, {"ORB_WORK_DIR": ""}, clear=True),
            patch("src.config.platform_dirs.get_config_location") as mock_config,
        ):
            mock_config.return_value = Path("/base/config")

            result = get_work_location()
            assert result == Path("/base/work")


class TestGetLogsLocation:
    """Test logs directory location detection."""

    def test_env_override_orb_log_dir(self):
        """Test ORB_LOG_DIR environment variable override."""
        with patch.dict(os.environ, {"ORB_LOG_DIR": "/custom/logs"}):
            result = get_logs_location()
            assert result == Path("/custom/logs")

    def test_relative_to_config(self):
        """Test logs location relative to config location."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("src.config.platform_dirs.get_config_location") as mock_config,
        ):
            mock_config.return_value = Path("/base/config")

            result = get_logs_location()
            assert result == Path("/base/logs")

    def test_env_override_empty_string(self):
        """Test empty ORB_LOG_DIR is ignored."""
        with (
            patch.dict(os.environ, {"ORB_LOG_DIR": ""}, clear=True),
            patch("src.config.platform_dirs.get_config_location") as mock_config,
        ):
            mock_config.return_value = Path("/base/config")

            result = get_logs_location()
            assert result == Path("/base/logs")


class TestGetScriptsLocation:
    """Test scripts directory location detection."""

    def test_relative_to_config(self):
        """Test scripts location relative to config location."""
        with patch("src.config.platform_dirs.get_config_location") as mock_config:
            mock_config.return_value = Path("/base/config")

            result = get_scripts_location()
            assert result == Path("/base/scripts")


class TestEnvironmentOverrides:
    """Test environment variable overrides."""

    def test_all_env_vars_set(self):
        """Test all ORB_* environment variables set."""
        env_vars = {
            "ORB_CONFIG_DIR": "/env/config",
            "ORB_WORK_DIR": "/env/work",
            "ORB_LOG_DIR": "/env/logs",
        }

        with patch.dict(os.environ, env_vars):
            assert get_config_location() == Path("/env/config")
            assert get_work_location() == Path("/env/work")
            assert get_logs_location() == Path("/env/logs")
            assert get_scripts_location() == Path("/env/scripts")

    def test_partial_env_vars(self):
        """Test partial environment variable overrides."""
        with (
            patch.dict(os.environ, {"ORB_CONFIG_DIR": "/env/config"}, clear=True),
            patch("src.config.platform_dirs.get_config_location") as mock_config,
        ):
            # Config uses env var
            assert get_config_location() == Path("/env/config")

            # Others use relative to config
            mock_config.return_value = Path("/env/config")
            assert get_work_location() == Path("/env/work")
            assert get_logs_location() == Path("/env/logs")
            assert get_scripts_location() == Path("/env/scripts")


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_windows_paths(self):
        """Test Windows path handling."""
        with patch.dict(os.environ, {"ORB_CONFIG_DIR": r"C:\Program Files\ORB\config"}):
            result = get_config_location()
            assert result == Path(r"C:\Program Files\ORB\config")

    def test_relative_paths_in_env(self):
        """Test relative paths in environment variables."""
        with patch.dict(os.environ, {"ORB_CONFIG_DIR": "./relative/config"}):
            result = get_config_location()
            assert result == Path("./relative/config")

    def test_home_expansion(self):
        """Test ~ expansion in paths (handled by Path constructor)."""
        with (
            patch.dict(os.environ, {"ORB_CONFIG_DIR": "~/orb/config"}),
            patch("pathlib.Path.home") as mock_home,
        ):
            mock_home.return_value = Path("/home/user")
            result = get_config_location()
            # Path constructor handles ~ expansion
            assert result == Path("~/orb/config")

    def test_nonexistent_pyproject_toml(self):
        """Test when pyproject.toml doesn't exist anywhere."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("pathlib.Path.cwd") as mock_cwd,
            patch("pathlib.Path.exists") as mock_exists,
            patch("pathlib.Path.home") as mock_home,
            patch.object(sys, "prefix", "/some/path"),
            patch.object(sys, "base_prefix", "/some/path"),
        ):
            mock_cwd.return_value = Path("/deep/nested/directory")
            mock_exists.return_value = False  # No pyproject.toml anywhere
            mock_home.return_value = Path("/home/user")

            result = get_config_location()
            assert result == Path("/deep/nested/directory/config")

    def test_permission_denied_cwd(self):
        """Test when current directory is not accessible."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("pathlib.Path.cwd") as mock_cwd,
        ):
            mock_cwd.side_effect = PermissionError("Permission denied")

            # Should not crash, but behavior depends on implementation
            # This tests that we handle the exception gracefully
            with pytest.raises(PermissionError):
                get_config_location()


class TestIntegration:
    """Integration tests for all functions working together."""

    def test_all_functions_consistent(self):
        """Test all functions return consistent paths."""
        with patch.dict(os.environ, {}, clear=True):
            config_dir = get_config_location()
            work_dir = get_work_location()
            logs_dir = get_logs_location()
            scripts_dir = get_scripts_location()

            # All should be under same parent
            assert work_dir.parent == config_dir.parent
            assert logs_dir.parent == config_dir.parent
            assert scripts_dir.parent == config_dir.parent

            # Correct subdirectories
            assert config_dir.name == "config"
            assert work_dir.name == "work"
            assert logs_dir.name == "logs"
            assert scripts_dir.name == "scripts"

    def test_detection_functions_consistent(self):
        """Test detection functions are mutually exclusive where expected."""
        # Can't be both user and system install
        if is_user_install():
            assert not is_system_install()

        # Virtual env detection is independent
        venv_status = in_virtualenv()
        assert isinstance(venv_status, bool)

    def test_path_objects_returned(self):
        """Test all functions return Path objects."""
        assert isinstance(get_config_location(), Path)
        assert isinstance(get_work_location(), Path)
        assert isinstance(get_logs_location(), Path)
        assert isinstance(get_scripts_location(), Path)

    def test_boolean_functions_return_bool(self):
        """Test detection functions return booleans."""
        assert isinstance(in_virtualenv(), bool)
        assert isinstance(is_user_install(), bool)
        assert isinstance(is_system_install(), bool)
