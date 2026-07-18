"""Unit tests for orb.config.installation_detector.

Covers detect_installation_mode(), is_mise_install(), detect_install_mode(),
and get_scripts_location() — all branches, error paths, and fallback logic.
No real filesystem writes, no real importlib.metadata package lookups.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
class TestDetectInstallationMode:
    """detect_installation_mode() — all branches."""

    def _import(self):
        from orb.config.installation_detector import detect_installation_mode

        return detect_installation_mode

    def test_package_not_installed_returns_development(self):
        detect = self._import()
        with patch("importlib.metadata.distribution", side_effect=Exception("not found")):
            mode, path = detect("orb-py")
        assert mode == "development"
        assert path is None

    def test_no_dist_path_returns_development(self):
        detect = self._import()
        fake_dist = MagicMock()
        # hasattr(_path) is False when _path attribute is missing
        del fake_dist._path
        with patch("importlib.metadata.distribution", return_value=fake_dist):
            mode, path = detect("orb-py")
        assert mode == "development"
        assert path is None

    def test_editable_via_pep610_direct_url(self, tmp_path):
        detect = self._import()

        dist_path = tmp_path / "orb-py.dist-info"
        dist_path.mkdir()
        direct_url_file = dist_path / "direct_url.json"
        source_dir = tmp_path / "src"
        source_dir.mkdir()

        direct_url_data = {
            "url": f"file://{source_dir}",
            "dir_info": {"editable": True},
        }
        direct_url_file.write_text(json.dumps(direct_url_data))

        fake_dist = MagicMock()
        fake_dist._path = dist_path

        with patch("importlib.metadata.distribution", return_value=fake_dist):
            mode, result_path = detect("orb-py")

        assert mode == "editable"
        assert result_path == source_dir

    def test_pep610_editable_false_falls_through(self, tmp_path):
        """dir_info.editable == False should not claim editable mode."""
        detect = self._import()

        dist_path = tmp_path / "sys_site"
        dist_path.mkdir()
        direct_url_file = dist_path / "direct_url.json"
        direct_url_data = {
            "url": f"file://{tmp_path}",
            "dir_info": {"editable": False},
        }
        direct_url_file.write_text(json.dumps(direct_url_data))

        fake_dist = MagicMock()
        fake_dist._path = dist_path

        with (
            patch("importlib.metadata.distribution", return_value=fake_dist),
            patch("site.USER_SITE", "/nonexistent/user_site"),
            patch.object(sys, "prefix", "/usr"),
        ):
            mode, _ = detect("orb-py")

        # Should land on system
        assert mode == "system"

    def test_pep610_invalid_json_continues(self, tmp_path):
        """JSONDecodeError in direct_url.json does not raise — falls through."""
        detect = self._import()

        dist_path = tmp_path / "some_dist"
        dist_path.mkdir()
        (dist_path / "direct_url.json").write_text("NOT JSON")

        fake_dist = MagicMock()
        fake_dist._path = dist_path

        with (
            patch("importlib.metadata.distribution", return_value=fake_dist),
            patch("site.USER_SITE", "/nonexistent/user_site"),
            patch.object(sys, "prefix", "/usr"),
        ):
            mode, _ = detect("orb-py")

        # Reached system / user path — did not crash
        assert mode in ("system", "user", "editable", "development")

    def test_egg_info_editable(self, tmp_path):
        """dist_path ending in .egg-info returns editable with grandparent."""
        detect = self._import()

        project_root = tmp_path / "myproject"
        src_dir = project_root / "src"
        src_dir.mkdir(parents=True)
        egg_info = src_dir / "orb_py.egg-info"
        egg_info.mkdir()

        fake_dist = MagicMock()
        fake_dist._path = egg_info

        with patch("importlib.metadata.distribution", return_value=fake_dist):
            mode, result_path = detect("orb-py")

        assert mode == "editable"
        assert result_path == project_root

    def test_user_install_detection(self, tmp_path):
        detect = self._import()

        user_base = tmp_path / ".local"
        user_site = user_base / "lib" / "python3.x" / "site-packages"
        user_site.mkdir(parents=True)
        dist_path = user_site / "orb_py.dist-info"
        dist_path.mkdir()

        fake_dist = MagicMock()
        fake_dist._path = dist_path

        with (
            patch("importlib.metadata.distribution", return_value=fake_dist),
            patch("site.USER_SITE", str(user_site)),
            patch("site.USER_BASE", str(user_base)),
        ):
            mode, result_path = detect("orb-py")

        assert mode == "user"
        assert result_path == user_base

    def test_system_install_fallback(self, tmp_path):
        detect = self._import()

        sys_site = tmp_path / "lib" / "python3.x" / "site-packages"
        sys_site.mkdir(parents=True)
        dist_path = sys_site / "orb_py.dist-info"
        dist_path.mkdir()

        fake_dist = MagicMock()
        fake_dist._path = dist_path

        with (
            patch("importlib.metadata.distribution", return_value=fake_dist),
            patch("site.USER_SITE", "/nonexistent_site"),
            patch.object(sys, "prefix", "/usr"),
        ):
            mode, result_path = detect("orb-py")

        assert mode == "system"
        assert result_path == Path("/usr")


@pytest.mark.unit
class TestIsMiseInstall:
    """is_mise_install() — true/false branches."""

    def _import(self):
        from orb.config.installation_detector import is_mise_install

        return is_mise_install

    def test_returns_true_when_executable_contains_mise_path(self, tmp_path):
        is_mise = self._import()
        fake_exe = tmp_path / ".local" / "share" / "mise" / "shims" / "python3"
        fake_exe.parent.mkdir(parents=True)
        fake_exe.touch()

        with patch.object(Path, "resolve", return_value=fake_exe):
            assert is_mise() is True

    def test_returns_false_for_regular_python(self, tmp_path):
        is_mise = self._import()
        # A regular system python path (no 'mise' component) must return False.
        fake_exe = tmp_path / "usr" / "local" / "bin" / "python3"
        fake_exe.parent.mkdir(parents=True)
        fake_exe.touch()

        with patch.object(Path, "resolve", return_value=fake_exe):
            assert is_mise() is False

    def test_returns_false_when_not_in_mise_path(self, tmp_path):
        is_mise = self._import()
        fake_exe = tmp_path / "usr" / "bin" / "python3"
        fake_exe.parent.mkdir(parents=True)
        fake_exe.touch()

        with patch.object(Path, "resolve", return_value=fake_exe):
            assert is_mise() is False


@pytest.mark.unit
class TestDetectInstallMode:
    """detect_install_mode() — all literal branches."""

    def _import(self):
        from orb.config.installation_detector import detect_install_mode

        return detect_install_mode

    def test_uv_tool_prefix_returns_uv_tool(self):
        detect = self._import()
        with patch.object(sys, "prefix", "/home/user/.local/share/uv/tools/orb"):
            assert detect() == "uv_tool"

    def test_mise_executable_returns_mise(self, tmp_path):
        detect = self._import()
        fake_exe = tmp_path / ".local/share/mise/shims/python3"
        fake_exe.parent.mkdir(parents=True)
        fake_exe.touch()

        with (
            patch.object(sys, "prefix", "/regular/prefix"),
            patch.object(Path, "resolve", return_value=fake_exe),
        ):
            assert detect() == "mise"

    def test_venv_prefix_differs_returns_venv(self):
        detect = self._import()
        with (
            patch.object(sys, "prefix", "/home/user/.venv"),
            patch.object(sys, "base_prefix", "/usr"),
            patch("orb.config.installation_detector.is_mise_install", return_value=False),
        ):
            assert detect() == "venv"

    def test_user_base_prefix_returns_user(self, tmp_path):
        detect = self._import()
        user_base = str(tmp_path / ".local")
        prefix = user_base + "/something"

        with (
            patch.object(sys, "prefix", prefix),
            patch.object(sys, "base_prefix", prefix),
            patch("site.USER_BASE", user_base),
            patch("orb.config.installation_detector.is_mise_install", return_value=False),
        ):
            assert detect() == "user"

    def test_system_install_usr_prefix(self):
        detect = self._import()
        with (
            patch.object(sys, "prefix", "/usr"),
            patch.object(sys, "base_prefix", "/usr"),
            patch("site.USER_BASE", "/home/user/.local"),
            patch("orb.config.installation_detector.is_mise_install", return_value=False),
        ):
            assert detect() == "system"

    def test_system_install_opt_prefix(self):
        detect = self._import()
        with (
            patch.object(sys, "prefix", "/opt/homebrew"),
            patch.object(sys, "base_prefix", "/opt/homebrew"),
            patch("site.USER_BASE", "/home/user/.local"),
            patch("orb.config.installation_detector.is_mise_install", return_value=False),
        ):
            assert detect() == "system"

    def test_delegates_to_detect_installation_mode_for_development(self):
        detect = self._import()
        with (
            patch.object(sys, "prefix", "/some/path"),
            patch.object(sys, "base_prefix", "/some/path"),
            patch("site.USER_BASE", "/other"),
            patch("orb.config.installation_detector.is_mise_install", return_value=False),
            patch(
                "orb.config.installation_detector.detect_installation_mode",
                return_value=("development", None),
            ),
        ):
            assert detect() == "development"

    def test_delegates_to_detect_installation_mode_for_editable(self):
        detect = self._import()
        with (
            patch.object(sys, "prefix", "/some/path"),
            patch.object(sys, "base_prefix", "/some/path"),
            patch("site.USER_BASE", "/other"),
            patch("orb.config.installation_detector.is_mise_install", return_value=False),
            patch(
                "orb.config.installation_detector.detect_installation_mode",
                return_value=("editable", Path("/src")),
            ),
        ):
            assert detect() == "editable"

    def test_fallback_returns_system_for_unknown_mode(self):
        detect = self._import()
        with (
            patch.object(sys, "prefix", "/some/path"),
            patch.object(sys, "base_prefix", "/some/path"),
            patch("site.USER_BASE", "/other"),
            patch("orb.config.installation_detector.is_mise_install", return_value=False),
            patch(
                "orb.config.installation_detector.detect_installation_mode",
                return_value=("system", Path("/usr")),
            ),
        ):
            assert detect() == "system"


@pytest.mark.unit
class TestGetScriptsLocation:
    """get_scripts_location() — mode-based branch paths."""

    def _import(self):
        from orb.config.installation_detector import get_scripts_location

        return get_scripts_location

    def test_development_mode_no_di_falls_back_to_root(self, tmp_path):
        get_scripts = self._import()

        with (
            patch(
                "orb.config.installation_detector.detect_installation_mode",
                return_value=("development", None),
            ),
            patch(
                "orb.config.installation_detector.is_container_ready",
                return_value=False,
                create=True,
            ),
            patch("orb.config.platform_dirs.get_root_location", return_value=tmp_path),
        ):
            # Import is_container_ready from the right place so patching works
            with patch("orb.infrastructure.di.container.is_container_ready", return_value=False):
                result = get_scripts()
        assert result == tmp_path / "scripts"

    def test_user_mode_returns_home_orb_scripts(self):
        get_scripts = self._import()
        with patch(
            "orb.config.installation_detector.detect_installation_mode",
            return_value=("user", None),
        ):
            result = get_scripts()
        assert result == Path.home() / ".orb" / "scripts"

    def test_system_mode_uses_sysconfig(self, tmp_path):
        get_scripts = self._import()
        with (
            patch(
                "orb.config.installation_detector.detect_installation_mode",
                return_value=("system", None),
            ),
            patch("sysconfig.get_path", return_value=str(tmp_path)),
        ):
            result = get_scripts()
        assert result == tmp_path / "orb_scripts"
