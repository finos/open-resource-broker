"""Provider registration at startup.

A provider named in the active configuration must load or startup fails with a
clear error; a provider that is not configured is optional, so a failure to
load it is logged with its traceback and the others still register.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import orb.providers.registration as reg
from orb.providers.base.provider_plugin import ProviderPlugin, reset_for_testing


class _FakeProviders:
    """Fake ``orb.providers.<name>.registration`` modules driven by a table."""

    def __init__(self, behaviours: dict[str, Any]) -> None:
        self.behaviours = behaviours
        self.registered: list[str] = []

    def find_spec(self, module_path: str) -> object:
        return object()

    def import_module(self, module_path: str) -> types.ModuleType:
        name = module_path.split(".")[2]
        behaviour = self.behaviours[name]
        if isinstance(behaviour, Exception):
            raise behaviour
        module = types.ModuleType(module_path)

        def _register(_registry: object) -> None:
            if behaviour == "register-fails":
                raise RuntimeError(f"{name} registration exploded")
            self.registered.append(name)

        setattr(module, f"register_{name}_provider", _register)
        return module


def _run(
    behaviours: dict[str, Any],
    configured: frozenset[str] | None,
) -> _FakeProviders:
    fake = _FakeProviders(behaviours)
    with (
        patch.object(reg, "discover_provider_plugins", return_value=[]),
        patch.object(reg, "_REGISTERED_PROVIDERS", list(behaviours)),
        patch.object(reg, "_configured_provider_types", return_value=configured),
        patch.object(
            reg,
            "importlib",
            types.SimpleNamespace(
                util=types.SimpleNamespace(find_spec=fake.find_spec),
                import_module=fake.import_module,
            ),
        ),
        patch("orb.providers.registry.get_provider_registry", return_value=MagicMock()),
    ):
        reg.register_all_providers()
    return fake


def _import_error(name: str) -> ModuleNotFoundError:
    return ModuleNotFoundError(f"No module named '{name}_sdk'")


@pytest.mark.unit
class TestUnconfiguredProviders:
    def test_import_error_warns_with_traceback_and_others_register(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=reg.__name__):
            fake = _run(
                {"alpha": _import_error("alpha"), "beta": "ok"},
                configured=frozenset({"beta"}),
            )

        assert fake.registered == ["beta"]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("alpha" in r.getMessage() and r.exc_info for r in warnings)

    def test_registration_error_is_logged_as_error_and_others_register(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger=reg.__name__):
            fake = _run(
                {"alpha": "register-fails", "beta": "ok"},
                configured=frozenset({"beta"}),
            )

        assert fake.registered == ["beta"]
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("alpha" in r.getMessage() and r.exc_info for r in errors)

    def test_unreadable_configuration_keeps_every_provider_optional(self) -> None:
        fake = _run({"alpha": _import_error("alpha"), "beta": "ok"}, configured=None)

        assert fake.registered == ["beta"]


@pytest.mark.unit
class TestConfiguredProviders:
    def test_missing_extra_fails_startup_naming_provider_and_extra(self) -> None:
        with pytest.raises(RuntimeError) as excinfo:
            _run({"alpha": _import_error("alpha")}, configured=frozenset({"alpha"}))

        message = str(excinfo.value)
        assert "alpha" in message
        assert "orb-py[alpha]" in message

    def test_registration_error_fails_startup(self) -> None:
        with pytest.raises(RuntimeError, match="alpha"):
            _run({"alpha": "register-fails"}, configured=frozenset({"alpha"}))

    def test_multi_provider_config_with_one_missing_extra_fails_startup(self) -> None:
        with pytest.raises(RuntimeError) as excinfo:
            _run(
                {"alpha": "ok", "beta": _import_error("beta"), "gamma": "ok"},
                configured=frozenset({"alpha", "beta"}),
            )

        assert "beta" in str(excinfo.value)
        assert "alpha" not in str(excinfo.value)

    def test_multi_provider_config_with_all_extras_present_registers_all(self) -> None:
        fake = _run(
            {"alpha": "ok", "beta": "ok", "gamma": _import_error("gamma")},
            configured=frozenset({"alpha", "beta"}),
        )

        assert fake.registered == ["alpha", "beta"]


@pytest.mark.unit
class TestConfiguredProviderTypes:
    def test_reads_active_provider_types_from_configuration(self) -> None:
        from orb.config.managers.configuration_manager import ConfigurationManager

        manager = ConfigurationManager(
            config_dict={
                "provider": {
                    "selection_policy": "ROUND_ROBIN",
                    "providers": [
                        {"name": "a-1", "type": "aws", "enabled": True, "config": {}},
                        {"name": "k-1", "type": "k8s", "enabled": True, "config": {}},
                        {"name": "z-1", "type": "gcp", "enabled": False, "config": {}},
                    ],
                }
            }
        )
        container = MagicMock()
        container.has.return_value = True
        container.get.return_value = manager

        assert reg._configured_provider_types(container) == frozenset({"aws", "k8s"})

    def test_no_configuration_at_all_means_nothing_is_configured(self, tmp_path: Any) -> None:
        # A fresh install has no config file; the default provider block must not
        # make a provider mandatory.
        from orb.config.managers.configuration_manager import ConfigurationManager

        manager = ConfigurationManager(config_file=str(tmp_path / "missing" / "config.json"))
        container = MagicMock()
        container.has.return_value = True
        container.get.return_value = manager

        assert reg._configured_provider_types(container) == frozenset()

    def test_returns_none_when_configuration_cannot_be_read(self) -> None:
        container = MagicMock()
        container.has.return_value = True
        container.get.side_effect = RuntimeError("broken config")

        assert reg._configured_provider_types(container) is None


class _Plugin(ProviderPlugin):
    provider_name = "retrytest"

    def strategy_factory(self) -> Any:
        return lambda cfg: MagicMock()

    def config_factory(self) -> Any:
        return lambda data: MagicMock()

    def template_dto_config(self) -> Any:
        return None

    def cli_spec(self) -> Any:
        return None

    def field_mapping(self) -> Any:
        return None

    def defaults_loader(self) -> Any:
        return None

    def template_example_generator(self, container: Any) -> Any:
        return None

    def template_class(self) -> type | None:
        return dict


@pytest.mark.unit
class TestTemplateClassRegistrationFailure:
    @pytest.fixture(autouse=True)
    def _clean(self):
        reset_for_testing()
        yield
        reset_for_testing()

    def test_failure_warns_with_traceback_and_a_second_call_retries(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        factory = MagicMock()
        factory.register_provider_template_class.side_effect = [RuntimeError("boom"), None]
        plugin = _Plugin()

        with caplog.at_level(logging.WARNING):
            plugin.initialize_provider(template_factory=factory)

        assert any(
            r.levelno == logging.WARNING and r.exc_info and "retrytest" in r.getMessage()
            for r in caplog.records
        )

        plugin.initialize_provider(template_factory=factory)
        assert factory.register_provider_template_class.call_count == 2

        plugin.initialize_provider(template_factory=factory)
        assert factory.register_provider_template_class.call_count == 2

    def test_declarative_registration_leaves_provider_uninitialised_and_retries(self) -> None:
        from orb.providers.base.registration import (
            ProviderRegistrationSpec,
            register_provider_complete,
        )

        factory = MagicMock()
        factory.register_provider_template_class.side_effect = [RuntimeError("boom"), None]
        spec = ProviderRegistrationSpec(
            provider_name="retrytest",
            template_class=dict,
            template_factory=factory,
        )

        register_provider_complete(spec)
        register_provider_complete(spec)
        register_provider_complete(spec)

        assert factory.register_provider_template_class.call_count == 2


_GUARD_SCRIPT = """
import importlib, sys
for blocked in sys.argv[2].split(","):
    sys.modules[blocked] = None
name = sys.argv[1]
for suffix in sys.argv[3].split(","):
    importlib.import_module(f"orb.providers.{name}.{suffix}")
importlib.import_module("orb.infrastructure.registry.template_extension_registry")
importlib.import_module("orb.config.schemas.provider_settings_registry")
import orb.providers.registration as reg
reg.register_all_providers()
"""


@pytest.mark.unit
@pytest.mark.parametrize(
    ("provider", "sdk_modules"),
    [
        ("azure", "azure"),
        ("gcp", "google.cloud,google.api_core,google.auth"),
        ("k8s", "kubernetes"),
    ],
)
def test_provider_modules_import_without_their_optional_sdk(
    provider: str, sdk_modules: str
) -> None:
    """Registration-time modules must not import the provider SDK at module level.

    The AWS SDK is a core dependency, so there is no AWS-free install to guard.
    """
    modules = "registration,provider_plugin,strategy,cli,scheduler,defaults_loader,configuration"
    completed = subprocess.run(  # noqa: S603  # fixed argv: this interpreter and a constant script
        [sys.executable, "-I", "-W", "ignore", "-c", _GUARD_SCRIPT, provider, sdk_modules, modules],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr[-2000:]


@pytest.mark.unit
def test_template_factory_skips_a_provider_whose_registration_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from orb.bootstrap.infrastructure_services import _register_template_services
    from orb.domain.base.ports.logging_port import LoggingPort
    from orb.domain.template.factory import TemplateFactory

    container = MagicMock()
    _register_template_services(container)
    factory_builder = next(
        call.args[1]
        for call in container.register_singleton.call_args_list
        if call.args[0] is TemplateFactory
    )

    logger_port = MagicMock()
    resolver = MagicMock()
    resolver.get.side_effect = lambda port: logger_port if port is LoggingPort else MagicMock()
    seen: list[str] = []

    def _import(module_path: str) -> types.ModuleType:
        name = module_path.split(".")[2]
        module = types.ModuleType(module_path)

        def _register(_factory: object, _logger: object) -> None:
            seen.append(name)
            if name == "broken":
                raise RuntimeError("template registration exploded")

        setattr(module, f"register_{name}_template_factory", _register)
        return module

    registry = MagicMock()
    registry.get_registered_types.return_value = ["broken", "healthy"]
    with (
        patch("orb.providers.registry.get_provider_registry", return_value=registry),
        patch("importlib.util.find_spec", return_value=object()),
        patch("importlib.import_module", side_effect=_import),
    ):
        factory_builder(resolver)

    assert seen == ["broken", "healthy"]
    assert any(
        "broken" in str(call.args) and call.kwargs.get("exc_info")
        for call in logger_port.warning.call_args_list
    )


def _bootstrap_container(configured_type: str) -> Any:
    from orb.config.managers.configuration_manager import ConfigurationManager
    from orb.infrastructure.di.container import DIContainer

    container = DIContainer()
    manager = ConfigurationManager(
        config_dict={
            "provider": {
                "selection_policy": "ROUND_ROBIN",
                "providers": [
                    {"name": "p-1", "type": configured_type, "enabled": True, "config": {}}
                ],
            }
        }
    )
    container.register_instance(ConfigurationManager, manager)
    return container


@pytest.mark.unit
@pytest.mark.parametrize("register", ["_register_services_lazy", "_register_services_eager"])
def test_bootstrap_fails_when_a_configured_provider_cannot_load(register: str) -> None:
    """The bootstrap must hand its container to provider registration."""
    from orb.bootstrap import services

    container = _bootstrap_container("alpha")
    fake = _FakeProviders({"alpha": _import_error("alpha")})
    with (
        patch.object(reg, "discover_provider_plugins", return_value=[]),
        patch.object(reg, "_REGISTERED_PROVIDERS", ["alpha"]),
        patch.object(
            reg,
            "importlib",
            types.SimpleNamespace(
                util=types.SimpleNamespace(find_spec=fake.find_spec),
                import_module=fake.import_module,
            ),
        ),
        patch("orb.providers.registry.get_provider_registry", return_value=MagicMock()),
        patch("orb.infrastructure.scheduler.registration.register_all_scheduler_types"),
        patch("orb.infrastructure.storage.registration.register_all_storage_types"),
        pytest.raises(RuntimeError, match="Configured provider 'alpha' could not be loaded"),
    ):
        getattr(services, register)(container)
