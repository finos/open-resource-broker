"""Unit tests for PathResolutionService."""

import os

from orb.config.services.path_resolution_service import PathResolutionService


class TestResolveFilePath:
    """Tests for PathResolutionService.resolve_file_path."""

    def test_resolve_file_path_explicit_path_with_directory_returns_it_directly(self):
        """Explicit path containing a directory component is returned as-is."""
        svc = PathResolutionService()
        result = svc.resolve_file_path("conf", "config.json", explicit_path="/abs/dir/file.json")
        assert result == "/abs/dir/file.json"

    def test_resolve_file_path_explicit_path_bare_filename_overrides_filename(
        self, monkeypatch, tmp_path
    ):
        """Bare filename in explicit_path overrides the filename, resolved via platform dir."""
        monkeypatch.setattr(
            "orb.config.platform_dirs.get_config_location", lambda: tmp_path / "config"
        )
        svc = PathResolutionService()
        result = svc.resolve_file_path("conf", "config.json", explicit_path="other.json")
        assert result == str(tmp_path / "config" / "other.json")

    def test_resolve_file_path_no_explicit_uses_scheduler_dir_when_provider_set(self):
        """When scheduler_directory_provider returns a dir, it is used."""
        provider = lambda file_type: "/sched/conf"  # noqa: E731
        svc = PathResolutionService(scheduler_directory_provider=provider)
        result = svc.resolve_file_path("conf", "config.json")
        assert result == "/sched/conf/config.json"

    def test_resolve_file_path_scheduler_provider_returns_none_falls_back_to_platform_dir(
        self, monkeypatch, tmp_path
    ):
        """When scheduler provider returns None, falls back to platform dir."""
        monkeypatch.setattr(
            "orb.config.platform_dirs.get_config_location", lambda: tmp_path / "config"
        )
        provider = lambda file_type: None  # noqa: E731
        svc = PathResolutionService(scheduler_directory_provider=provider)
        result = svc.resolve_file_path("conf", "config.json")
        assert result == str(tmp_path / "config" / "config.json")

    def test_resolve_file_path_scheduler_provider_raises_falls_back_to_platform_dir(
        self, monkeypatch, tmp_path
    ):
        """When scheduler provider raises, exception is swallowed and falls back to platform dir."""
        monkeypatch.setattr(
            "orb.config.platform_dirs.get_config_location", lambda: tmp_path / "config"
        )

        def bad_provider(file_type):
            raise RuntimeError("scheduler unavailable")

        svc = PathResolutionService(scheduler_directory_provider=bad_provider)
        result = svc.resolve_file_path("conf", "config.json")
        assert result == str(tmp_path / "config" / "config.json")

    def test_resolve_file_path_unknown_file_type_falls_back_to_config_location(
        self, monkeypatch, tmp_path
    ):
        """Unknown file_type falls back to get_config_location()."""
        monkeypatch.setattr(
            "orb.config.platform_dirs.get_config_location", lambda: tmp_path / "config"
        )
        svc = PathResolutionService()
        result = svc.resolve_file_path("bogus", "something.json")
        assert result == str(tmp_path / "config" / "something.json")

    def test_resolve_file_path_log_type_uses_logs_location(self, monkeypatch, tmp_path):
        """file_type='log' resolves via get_logs_location()."""
        monkeypatch.setattr("orb.config.platform_dirs.get_logs_location", lambda: tmp_path / "logs")
        svc = PathResolutionService()
        result = svc.resolve_file_path("log", "orb.log")
        assert result == str(tmp_path / "logs" / "orb.log")

    def test_resolve_file_path_work_type_uses_work_location(self, monkeypatch, tmp_path):
        """file_type='work' resolves via get_work_location()."""
        monkeypatch.setattr("orb.config.platform_dirs.get_work_location", lambda: tmp_path / "work")
        svc = PathResolutionService()
        result = svc.resolve_file_path("work", "state.json")
        assert result == str(tmp_path / "work" / "state.json")

    def test_resolve_file_path_events_type_uses_work_location(self, monkeypatch, tmp_path):
        """file_type='events' resolves via get_work_location()."""
        monkeypatch.setattr("orb.config.platform_dirs.get_work_location", lambda: tmp_path / "work")
        svc = PathResolutionService()
        result = svc.resolve_file_path("events", "events.json")
        assert result == str(tmp_path / "work" / "events.json")


class TestResolveDirectory:
    """Tests for PathResolutionService.resolve_directory."""

    def test_resolve_directory_with_scheduler_provider_returns_scheduler_dir(self):
        """When scheduler provider returns a dir, resolve_directory returns it."""
        provider = lambda file_type: "/sched/work"  # noqa: E731
        svc = PathResolutionService(scheduler_directory_provider=provider)
        result = svc.resolve_directory("work")
        assert result == "/sched/work"

    def test_resolve_directory_without_scheduler_provider_returns_platform_dir(
        self, monkeypatch, tmp_path
    ):
        """Without a scheduler provider, resolve_directory returns the platform config dir."""
        monkeypatch.setattr(
            "orb.config.platform_dirs.get_config_location", lambda: tmp_path / "config"
        )
        svc = PathResolutionService()
        result = svc.resolve_directory("conf")
        assert result == str(tmp_path / "config")


class TestFindFileWithFallbacks:
    """Tests for PathResolutionService.find_file_with_fallbacks."""

    def test_find_file_with_fallbacks_returns_first_existing(self, monkeypatch, tmp_path):
        """Returns the path of the first candidate that exists on disk."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        monkeypatch.setattr("orb.config.platform_dirs.get_config_location", lambda: config_dir)
        present = config_dir / "present.json"
        present.write_text("{}")

        svc = PathResolutionService()
        result = svc.find_file_with_fallbacks("conf", ["missing.json", "present.json"])
        assert result == str(present)

    def test_find_file_with_fallbacks_returns_none_when_all_missing(self, monkeypatch, tmp_path):
        """Returns None when none of the candidates exist."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        monkeypatch.setattr("orb.config.platform_dirs.get_config_location", lambda: config_dir)
        svc = PathResolutionService()
        result = svc.find_file_with_fallbacks("conf", ["a.json", "b.json"])
        assert result is None


class TestRegressionGuards:
    """Regression guards for PathResolutionService."""

    def test_path_resolution_does_not_call_os_getcwd(self, monkeypatch, tmp_path):
        """_get_platform_dir must not fall through to os.getcwd()-dependent code paths.

        We patch get_config_location to return a tmp_path-based path (simulating a
        virtualenv or env-var scenario) and verify that even if os.getcwd() were to
        raise, no RuntimeError propagates out of the service.
        """
        monkeypatch.setattr(
            "orb.config.platform_dirs.get_config_location", lambda: tmp_path / "config"
        )
        monkeypatch.setattr(os, "getcwd", lambda: (_ for _ in ()).throw(RuntimeError("no cwd")))

        # Should not raise even though os.getcwd() would blow up
        result = PathResolutionService._get_platform_dir("conf")
        assert result == str(tmp_path / "config")
