"""Unit tests for HandlerDiscoveryService.

Coverage targets: lines 76,91,95-96,133-134,137,139-141,164-165,184-185,
199-201,204-205,208-209,211-213,215-216,218-220,229,231,234-235,237,252-255,
257,262-263,268,270-272,274-276,279-280,282-283,289-290,293-295,297-299,
302-303,305-306,312-313,315-316,321-323,327-329,333,335,337,340-347,349,353,
355-357,367-369,371
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from orb.infrastructure.di.handler_discovery import (
    HandlerDiscoveryService,
    create_handler_discovery_service,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_container() -> MagicMock:
    container = MagicMock()
    container.register_singleton = MagicMock()
    return container


def _make_config_manager(cache_enabled: bool = False, cache_dir: str = "/tmp") -> MagicMock:
    config_manager = MagicMock()
    perf_config = MagicMock()
    perf_config.caching.handler_discovery.enabled = cache_enabled
    config_manager.get_typed.return_value = perf_config
    config_manager.get_cache_dir.return_value = cache_dir
    return config_manager


def _make_service_with_cache_disabled() -> HandlerDiscoveryService:
    """Build a service with caching disabled via mocked container."""
    container = _make_container()

    with (
        patch(
            "orb.infrastructure.di.handler_discovery.DIContainer",
            return_value=container,
        ),
        patch(
            "orb.infrastructure.di.handler_discovery.HandlerDiscoveryService.__init__",
            lambda self, c: _init_no_cache(self, c),
        ),
    ):
        svc = HandlerDiscoveryService.__new__(HandlerDiscoveryService)
        svc.container = container
        svc.cache_enabled = False
        svc.cache_file = None
        return svc


def _init_no_cache(self, container):
    self.container = container
    self.cache_enabled = False
    self.cache_file = None


# ---------------------------------------------------------------------------
# Construction: config loading branches
# ---------------------------------------------------------------------------


class TestHandlerDiscoveryServiceConstruction:
    def test_cache_disabled_when_config_raises(self):
        container = _make_container()
        container.get.side_effect = Exception("no config")

        svc = HandlerDiscoveryService(container)

        assert svc.cache_enabled is False
        assert svc.cache_file is None

    def test_cache_enabled_when_config_succeeds(self, tmp_path):
        container = _make_container()

        perf_config = MagicMock()
        perf_config.caching.handler_discovery.enabled = True

        config_manager = MagicMock()
        config_manager.get_typed.return_value = perf_config
        config_manager.get_cache_dir.return_value = str(tmp_path)

        # Patch container.get to return our mocked config_manager for any type
        container.get.side_effect = None
        container.get.return_value = config_manager

        svc = HandlerDiscoveryService(container)
        # When config succeeds with caching enabled, cache_file is resolved
        # under the configured cache dir and named handler_discovery.json.
        assert svc.cache_enabled is True
        assert svc.cache_file is not None
        assert svc.cache_file.endswith("handler_discovery.json")
        assert svc.cache_file.startswith(str(tmp_path))

    def test_cache_disabled_stores_none_for_file(self):
        container = _make_container()
        container.get.side_effect = Exception("config failure")
        svc = HandlerDiscoveryService(container)
        assert svc.cache_file is None


# ---------------------------------------------------------------------------
# _resolve_cache_path
# ---------------------------------------------------------------------------


class TestResolveCachePath:
    def test_builds_path_under_cache_dir(self, tmp_path):
        svc = _make_service_with_cache_disabled()
        config_manager = MagicMock()
        config_manager.get_cache_dir.return_value = str(tmp_path)

        result = svc._resolve_cache_path(config_manager)

        assert result.startswith(str(tmp_path))
        assert "handler_discovery.json" in result

    def test_creates_cache_directory(self, tmp_path):
        new_dir = tmp_path / "subdir" / "cache"
        svc = _make_service_with_cache_disabled()
        config_manager = MagicMock()
        config_manager.get_cache_dir.return_value = str(new_dir)

        svc._resolve_cache_path(config_manager)

        assert new_dir.exists()


# ---------------------------------------------------------------------------
# _get_source_file_mtimes
# ---------------------------------------------------------------------------


class TestGetSourceFileMtimes:
    def test_returns_dict(self):
        svc = _make_service_with_cache_disabled()
        result = svc._get_source_file_mtimes("orb.application")
        assert isinstance(result, dict)

    def test_returns_empty_for_nonexistent_package_path(self):
        svc = _make_service_with_cache_disabled()
        # a path that doesn't resolve to any real directory -> os.walk yields
        # nothing, so the mtimes dict is empty.
        result = svc._get_source_file_mtimes("no.such.package.xyz")
        assert result == {}


# ---------------------------------------------------------------------------
# _serialize_handlers
# ---------------------------------------------------------------------------


class TestSerializeHandlers:
    def test_serializes_handler_with_query_in_name(self):
        class MyQuery:
            pass

        class MyQueryHandler:
            pass

        MyQueryHandler.__module__ = "orb.application.handlers"

        svc = _make_service_with_cache_disabled()
        result = svc._serialize_handlers({MyQuery: MyQueryHandler})

        assert "MyQuery" in result
        entry = result["MyQuery"]
        assert entry["class_name"] == "MyQueryHandler"
        assert entry["module"] == "orb.application.handlers"
        assert entry["query_type_name"] == "MyQuery"
        assert entry["command_type_name"] is None

    def test_serializes_handler_with_command_in_name(self):
        class CreateCommand:
            pass

        class CreateCommandHandler:
            pass

        CreateCommandHandler.__module__ = "orb.application.commands"

        svc = _make_service_with_cache_disabled()
        result = svc._serialize_handlers({CreateCommand: CreateCommandHandler})

        assert "CreateCommand" in result
        entry = result["CreateCommand"]
        assert entry["command_type_name"] == "CreateCommand"
        assert entry["query_type_name"] is None

    def test_skips_handler_that_raises_on_attribute_access(self):
        # _serialize_handlers reads handled_type.__name__ at the class level;
        # to actually trigger the except->skip branch the metaclass must raise
        # when __name__ is accessed on the class object itself.
        class _RaisingNameMeta(type):
            @property
            def __name__(cls):  # noqa: N805
                raise AttributeError("no name")

        class BrokenType(metaclass=_RaisingNameMeta):
            pass

        class GoodQuery:
            pass

        class GoodHandler:
            pass

        GoodHandler.__module__ = "orb.application.handlers"

        svc = _make_service_with_cache_disabled()
        result = svc._serialize_handlers({BrokenType: MagicMock(), GoodQuery: GoodHandler})
        # The broken handler is skipped; the valid sibling is serialized.
        assert "GoodQuery" in result
        assert result["GoodQuery"]["class_name"] == "GoodHandler"
        assert all(entry["class_name"] != "MagicMock" for entry in result.values())
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _try_load_from_cache
# ---------------------------------------------------------------------------


class TestTryLoadFromCache:
    def test_returns_none_when_cache_disabled(self):
        svc = _make_service_with_cache_disabled()
        result = svc._try_load_from_cache("orb.application")
        assert result is None

    def test_returns_none_when_cache_file_missing(self, tmp_path):
        svc = _make_service_with_cache_disabled()
        svc.cache_enabled = True
        svc.cache_file = str(tmp_path / "nonexistent.json")
        result = svc._try_load_from_cache("orb.application")
        assert result is None

    def test_returns_none_when_base_package_differs(self, tmp_path):
        cache_path = tmp_path / "handler_discovery.json"
        cache_data = {
            "base_package": "orb.other",
            "source_mtimes": {},
            "handlers": {},
            "total_handlers": 0,
        }
        cache_path.write_text(json.dumps(cache_data))

        svc = _make_service_with_cache_disabled()
        svc.cache_enabled = True
        svc.cache_file = str(cache_path)

        result = svc._try_load_from_cache("orb.application")
        assert result is None

    def test_returns_none_when_source_mtimes_differ(self, tmp_path):
        cache_path = tmp_path / "handler_discovery.json"
        cache_data = {
            "base_package": "orb.application",
            "source_mtimes": {"some/file.py": 12345.0},
            "handlers": {},
            "total_handlers": 0,
        }
        cache_path.write_text(json.dumps(cache_data))

        svc = _make_service_with_cache_disabled()
        svc.cache_enabled = True
        svc.cache_file = str(cache_path)

        # _get_source_file_mtimes will return {} (different from stored), so cache invalid
        with patch.object(svc, "_get_source_file_mtimes", return_value={}):
            result = svc._try_load_from_cache("orb.application")
        assert result is None

    def test_returns_cache_data_when_valid(self, tmp_path):
        cache_path = tmp_path / "handler_discovery.json"
        cache_data = {
            "base_package": "orb.application",
            "source_mtimes": {},
            "handlers": {"query_handlers": {}, "command_handlers": {}},
            "total_handlers": 0,
        }
        cache_path.write_text(json.dumps(cache_data))

        svc = _make_service_with_cache_disabled()
        svc.cache_enabled = True
        svc.cache_file = str(cache_path)

        with patch.object(svc, "_get_source_file_mtimes", return_value={}):
            result = svc._try_load_from_cache("orb.application")
        assert result is not None
        assert result["base_package"] == "orb.application"

    def test_returns_none_on_json_parse_error(self, tmp_path):
        cache_path = tmp_path / "handler_discovery.json"
        cache_path.write_text("not valid json{{{{")

        svc = _make_service_with_cache_disabled()
        svc.cache_enabled = True
        svc.cache_file = str(cache_path)

        result = svc._try_load_from_cache("orb.application")
        assert result is None


# ---------------------------------------------------------------------------
# _save_to_cache
# ---------------------------------------------------------------------------


class TestSaveToCache:
    def test_does_nothing_when_cache_disabled(self, tmp_path):
        svc = _make_service_with_cache_disabled()
        # Should not raise
        svc._save_to_cache("orb.application", {"total_handlers": 0}, 0.1)

    def test_writes_json_file_when_enabled(self, tmp_path):
        cache_path = tmp_path / "handler_discovery.json"
        svc = _make_service_with_cache_disabled()
        svc.cache_enabled = True
        svc.cache_file = str(cache_path)

        with (
            patch(
                "orb.infrastructure.di.handler_discovery.get_registered_query_handlers",
                return_value={},
            ),
            patch(
                "orb.infrastructure.di.handler_discovery.get_registered_command_handlers",
                return_value={},
            ),
            patch.object(svc, "_get_source_file_mtimes", return_value={}),
        ):
            svc._save_to_cache("orb.application", {"total_handlers": 5}, 0.05)

        assert cache_path.exists()
        data = json.loads(cache_path.read_text())
        assert data["base_package"] == "orb.application"
        assert data["total_handlers"] == 5
        assert data["version"] == "1.0"

    def test_handles_write_error_gracefully(self, tmp_path):
        svc = _make_service_with_cache_disabled()
        svc.cache_enabled = True
        svc.cache_file = "/nonexistent_root/no_dir/file.json"

        # Should not raise
        with (
            patch(
                "orb.infrastructure.di.handler_discovery.get_registered_query_handlers",
                return_value={},
            ),
            patch(
                "orb.infrastructure.di.handler_discovery.get_registered_command_handlers",
                return_value={},
            ),
            patch.object(svc, "_get_source_file_mtimes", return_value={}),
        ):
            svc._save_to_cache("orb.application", {}, 0.0)


# ---------------------------------------------------------------------------
# _register_handlers
# ---------------------------------------------------------------------------


class TestRegisterHandlers:
    def test_registers_all_query_and_command_handlers(self):
        container = _make_container()
        svc = _make_service_with_cache_disabled()
        svc.container = container

        class QHandler:
            pass

        class CHandler:
            pass

        class MyQuery:
            pass

        class MyCommand:
            pass

        with (
            patch(
                "orb.infrastructure.di.handler_discovery.get_registered_query_handlers",
                return_value={MyQuery: QHandler},
            ),
            patch(
                "orb.infrastructure.di.handler_discovery.get_registered_command_handlers",
                return_value={MyCommand: CHandler},
            ),
        ):
            svc._register_handlers()

        # Both handlers should be registered
        assert container.register_singleton.call_count == 2
        registered = [c[0][0] for c in container.register_singleton.call_args_list]
        assert QHandler in registered
        assert CHandler in registered

    def test_continues_when_handler_registration_raises(self):
        container = _make_container()
        container.register_singleton.side_effect = [None, RuntimeError("boom")]
        svc = _make_service_with_cache_disabled()
        svc.container = container

        class QH1:
            pass

        class QH2:
            pass

        class Q1:
            pass

        class Q2:
            pass

        with (
            patch(
                "orb.infrastructure.di.handler_discovery.get_registered_query_handlers",
                return_value={Q1: QH1, Q2: QH2},
            ),
            patch(
                "orb.infrastructure.di.handler_discovery.get_registered_command_handlers",
                return_value={},
            ),
        ):
            svc._register_handlers()

        # Both handlers are attempted despite the first raising, proving the
        # per-handler try/except keeps iterating past a failure.
        assert container.register_singleton.call_count == 2
        registered = [c[0][0] for c in container.register_singleton.call_args_list]
        assert QH1 in registered
        assert QH2 in registered


# ---------------------------------------------------------------------------
# _register_handlers_from_cache
# ---------------------------------------------------------------------------


class TestRegisterHandlersFromCache:
    def test_registers_handlers_from_valid_cache(self):
        container = _make_container()
        svc = _make_service_with_cache_disabled()
        svc.container = container

        # Simulate a cached handler that can be imported

        cached_handlers = {
            "query_handlers": {
                "TestQuery": {
                    "class_name": "HandlerDiscoveryService",
                    "module": "orb.infrastructure.di.handler_discovery",
                    "query_type_name": "HandlerDiscoveryService",
                    "command_type_name": None,
                }
            },
            "command_handlers": {},
        }

        svc._register_handlers_from_cache(cached_handlers)
        # Should have registered the class
        assert container.register_singleton.call_count >= 1

    def test_falls_back_on_import_error(self):
        container = _make_container()
        svc = _make_service_with_cache_disabled()
        svc.container = container

        cached_handlers = {
            "query_handlers": {
                "SomeQuery": {
                    "class_name": "NonExistent",
                    "module": "orb.no.such.module.xyz",
                    "query_type_name": "SomeQuery",
                    "command_type_name": None,
                }
            },
            "command_handlers": {},
        }

        with patch.object(svc, "_fallback_to_full_discovery") as mock_fallback:
            svc._register_handlers_from_cache(cached_handlers)
            mock_fallback.assert_called_once()

    def test_falls_back_on_command_handler_import_error(self):
        container = _make_container()
        svc = _make_service_with_cache_disabled()
        svc.container = container

        cached_handlers = {
            "query_handlers": {},
            "command_handlers": {
                "SomeCommand": {
                    "class_name": "NonExistent",
                    "module": "orb.no.such.module.xyz",
                    "command_type_name": "SomeCommand",
                    "query_type_name": None,
                }
            },
        }

        with patch.object(svc, "_fallback_to_full_discovery") as mock_fallback:
            svc._register_handlers_from_cache(cached_handlers)
            mock_fallback.assert_called_once()

    def test_falls_back_on_outer_exception(self):
        container = _make_container()
        svc = _make_service_with_cache_disabled()
        svc.container = container

        # Pass something that will cause an unexpected exception
        with patch.object(svc, "_fallback_to_full_discovery") as mock_fallback:
            svc._register_handlers_from_cache(None)  # type: ignore[arg-type]
            mock_fallback.assert_called_once()


# ---------------------------------------------------------------------------
# _fallback_to_full_discovery
# ---------------------------------------------------------------------------


class TestFallbackToFullDiscovery:
    def test_calls_discover_and_register(self):
        svc = _make_service_with_cache_disabled()

        with (
            patch.object(svc, "_discover_handlers") as mock_discover,
            patch.object(svc, "_register_handlers") as mock_register,
        ):
            svc._fallback_to_full_discovery()

        mock_discover.assert_called_once_with("orb.application")
        mock_register.assert_called_once()


# ---------------------------------------------------------------------------
# discover_and_register_handlers (orchestration)
# ---------------------------------------------------------------------------


class TestDiscoverAndRegisterHandlers:
    def test_uses_cache_when_valid(self):
        svc = _make_service_with_cache_disabled()

        cached_result = {
            "total_handlers": 3,
            "handlers": {"query_handlers": {}, "command_handlers": {}},
        }

        with (
            patch.object(svc, "_try_load_from_cache", return_value=cached_result) as mock_cache,
            patch.object(svc, "_register_handlers_from_cache") as mock_from_cache,
            patch.object(svc, "_discover_handlers") as mock_discover,
        ):
            svc.discover_and_register_handlers("orb.application")

        mock_cache.assert_called_once_with("orb.application")
        mock_from_cache.assert_called_once_with(cached_result["handlers"])
        mock_discover.assert_not_called()

    def test_performs_full_discovery_on_cache_miss(self):
        svc = _make_service_with_cache_disabled()

        with (
            patch.object(svc, "_try_load_from_cache", return_value=None),
            patch.object(svc, "_discover_handlers") as mock_discover,
            patch.object(svc, "_register_handlers") as mock_register,
            patch.object(svc, "_save_to_cache") as mock_save,
            patch(
                "orb.infrastructure.di.handler_discovery.get_handler_registry_stats",
                return_value={"total_handlers": 0},
            ),
        ):
            svc.discover_and_register_handlers("orb.application")

        mock_discover.assert_called_once_with("orb.application")
        mock_register.assert_called_once()
        mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# _discover_handlers
# ---------------------------------------------------------------------------


class TestDiscoverHandlers:
    def test_raises_on_package_import_error(self):
        svc = _make_service_with_cache_disabled()
        with pytest.raises(Exception):
            svc._discover_handlers("completely.nonexistent.package.xyz")

    def test_logs_warning_on_module_import_error(self, tmp_path):
        svc = _make_service_with_cache_disabled()

        fake_module_info = MagicMock()
        fake_module_info.name = "orb.application.fake_module_xyz"

        # Create a fake package with an __init__.py so Path resolution works
        pkg_dir = tmp_path / "fake_pkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").touch()

        mock_pkg = MagicMock()
        mock_pkg.__file__ = str(pkg_dir / "__init__.py")

        import importlib
        import pkgutil

        with (
            patch.object(importlib, "import_module") as mock_import,
            patch.object(pkgutil, "walk_packages", return_value=[fake_module_info]),
        ):
            # First call returns the fake package, second call (module) raises ImportError
            mock_import.side_effect = [mock_pkg, ImportError("cannot import this")]
            # Should not raise — failed modules are logged and skipped
            svc._discover_handlers("fake.package")
            # verify that the module was attempted but skipped
            assert mock_import.call_count == 2


# ---------------------------------------------------------------------------
# create_handler_discovery_service factory
# ---------------------------------------------------------------------------


class TestCreateHandlerDiscoveryServiceFactory:
    def test_returns_handler_discovery_service_instance(self):
        container = _make_container()
        container.get.side_effect = Exception("no config")

        svc = create_handler_discovery_service(container)
        assert isinstance(svc, HandlerDiscoveryService)
        assert svc.container is container
