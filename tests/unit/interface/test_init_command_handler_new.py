"""Additional unit tests for init_command_handler.

Covers handle_init flow (config already exists, non-interactive success/failure,
directories created, scripts copied) and helper functions (_get_available_providers,
_get_available_credential_sources, _test_provider_credentials, _fallback_provider_name,
_create_directories).
"""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

import orb.interface.init_command_handler as _mod
from orb.domain.base.ports.console_port import ConsolePort
from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort


def _make_container(extra: dict | None = None) -> MagicMock:
    console = MagicMock(spec=ConsolePort)
    registry = MagicMock(spec=ProviderRegistryPort)
    registry.get_registered_providers.return_value = ["aws", "k8s"]

    container = MagicMock()

    dispatch: dict = {
        ConsolePort: console,
        ProviderRegistryPort: registry,
    }
    if extra:
        dispatch.update(extra)
    container.get.side_effect = lambda t: dispatch.get(t, MagicMock())
    return container


@pytest.mark.unit
class TestHandleInit:
    """Tests for handle_init coroutine."""

    @pytest.mark.asyncio
    async def test_config_already_exists_without_force_returns_1(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.json").write_text("{}")

        container = _make_container()
        args = Namespace(
            _container=container,
            config_dir=str(config_dir),
            force=False,
            non_interactive=True,
            scripts_dir=None,
            provider_type="aws",
            scheduler="default",
        )
        result = await _mod.handle_init(args)

        assert result == 1
        container.get(ConsolePort).error.assert_called()

    @pytest.mark.asyncio
    async def test_config_already_exists_with_force_continues(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.json").write_text("{}")

        container = _make_container()
        mock_strategy = MagicMock()
        mock_strategy.get_cli_infrastructure_defaults.return_value = {}
        mock_strategy.get_cli_provider_config.return_value = {}
        mock_strategy.generate_provider_name.return_value = "aws_default"
        mock_strategy.get_cli_extra_config_keys.return_value = set()

        mock_scheduler_registry = MagicMock()
        mock_scheduler_registry.get_extra_config_for_type.return_value = {}

        args = Namespace(
            _container=container,
            config_dir=str(config_dir),
            force=True,
            non_interactive=True,
            scripts_dir=None,
            provider_type="aws",
            scheduler="default",
        )

        with (
            patch.object(
                _mod,
                "_get_default_config",
                return_value={
                    "scheduler_type": "default",
                    "providers": [
                        {
                            "type": "aws",
                            "config": {},
                            "infrastructure_defaults": {},
                            "is_default": True,
                        }
                    ],
                },
            ),
            patch.object(_mod, "_create_directories"),
            patch.object(_mod, "_write_config_file"),
            patch.object(_mod, "_copy_scripts"),
        ):
            result = await _mod.handle_init(args)

        assert result == 0

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_returns_1(self, tmp_path):
        config_dir = tmp_path / "config"

        container = _make_container()
        args = Namespace(
            _container=container,
            config_dir=str(config_dir),
            force=False,
            non_interactive=True,
            scripts_dir=None,
            provider_type=None,
            scheduler=None,
        )

        with patch.object(_mod, "_get_default_config", side_effect=KeyboardInterrupt):
            result = await _mod.handle_init(args)

        assert result == 1
        container.get(ConsolePort).error.assert_called()

    @pytest.mark.asyncio
    async def test_generic_exception_returns_1(self, tmp_path):
        config_dir = tmp_path / "config"

        container = _make_container()
        args = Namespace(
            _container=container,
            config_dir=str(config_dir),
            force=False,
            non_interactive=True,
            scripts_dir=None,
            provider_type=None,
            scheduler=None,
        )

        with patch.object(_mod, "_get_default_config", side_effect=RuntimeError("boom")):
            result = await _mod.handle_init(args)

        assert result == 1

    @pytest.mark.asyncio
    async def test_non_interactive_success_returns_0(self, tmp_path):
        config_dir = tmp_path / "config"

        container = _make_container()
        args = Namespace(
            _container=container,
            config_dir=str(config_dir),
            force=False,
            non_interactive=True,
            scripts_dir=None,
            provider_type="aws",
            scheduler="default",
        )

        with (
            patch.object(
                _mod,
                "_get_default_config",
                return_value={
                    "scheduler_type": "default",
                    "providers": [
                        {
                            "type": "aws",
                            "config": {},
                            "infrastructure_defaults": {},
                            "is_default": True,
                        }
                    ],
                },
            ),
            patch.object(_mod, "_create_directories"),
            patch.object(_mod, "_write_config_file"),
            patch.object(_mod, "_copy_scripts"),
        ):
            result = await _mod.handle_init(args)

        assert result == 0

    @pytest.mark.asyncio
    async def test_empty_config_from_get_default_returns_1(self, tmp_path):
        """When _get_default_config returns falsy, handle_init returns 1."""
        config_dir = tmp_path / "config"

        container = _make_container()
        args = Namespace(
            _container=container,
            config_dir=str(config_dir),
            force=False,
            non_interactive=True,
            scripts_dir=None,
            provider_type=None,
            scheduler=None,
        )

        with patch.object(_mod, "_get_default_config", return_value=None):
            result = await _mod.handle_init(args)

        assert result == 1

    @pytest.mark.asyncio
    async def test_uses_platform_dirs_when_no_config_dir_arg(self, tmp_path):
        """When args.config_dir is None, platform dirs are used."""
        container = _make_container()
        args = Namespace(
            _container=container,
            config_dir=None,
            force=False,
            non_interactive=True,
            scripts_dir=None,
            provider_type="aws",
            scheduler="default",
        )

        with (
            patch(
                "orb.interface.init_command_handler.get_config_location",
                return_value=tmp_path / "cfg",
            ),
            patch(
                "orb.interface.init_command_handler.get_work_location",
                return_value=tmp_path / "work",
            ),
            patch(
                "orb.interface.init_command_handler.get_logs_location",
                return_value=tmp_path / "logs",
            ),
            patch(
                "orb.interface.init_command_handler.get_scripts_location",
                return_value=tmp_path / "scripts",
            ),
            patch.object(
                _mod,
                "_get_default_config",
                return_value={
                    "scheduler_type": "default",
                    "providers": [
                        {
                            "type": "aws",
                            "config": {},
                            "infrastructure_defaults": {},
                            "is_default": True,
                        }
                    ],
                },
            ),
            patch.object(_mod, "_create_directories"),
            patch.object(_mod, "_write_config_file"),
            patch.object(_mod, "_copy_scripts"),
        ):
            result = await _mod.handle_init(args)

        assert result == 0


@pytest.mark.unit
class TestGetAvailableProviders:
    """Tests for _get_available_providers."""

    def test_returns_providers_from_registry(self):
        registry = MagicMock(spec=ProviderRegistryPort)
        registry.get_registered_providers.return_value = ["aws", "k8s"]

        result = _mod._get_available_providers(registry=registry)

        assert len(result) == 2
        types = [p["type"] for p in result]
        assert "aws" in types
        assert "k8s" in types

    def test_returns_empty_when_no_registry(self):
        result = _mod._get_available_providers()
        assert result == []

    def test_registry_exception_returns_empty(self):
        registry = MagicMock(spec=ProviderRegistryPort)
        registry.get_registered_providers.side_effect = RuntimeError("registry broken")

        result = _mod._get_available_providers(registry=registry)

        assert result == []

    def test_uses_container_to_get_registry_when_no_registry_given(self):
        registry = MagicMock(spec=ProviderRegistryPort)
        registry.get_registered_providers.return_value = ["gcp"]

        container = MagicMock()
        container.get.return_value = registry

        result = _mod._get_available_providers(container=container)

        assert len(result) == 1
        assert result[0]["type"] == "gcp"


@pytest.mark.unit
class TestGetAvailableCredentialSources:
    """Tests for _get_available_credential_sources."""

    def test_returns_default_when_no_strategy(self):
        with patch.object(_mod, "_get_provider_strategy", return_value=None):
            result = _mod._get_available_credential_sources("aws")

        assert len(result) == 1
        assert result[0]["description"] == "Default credentials"

    def test_returns_strategy_sources(self):
        strategy = MagicMock()
        strategy.get_available_credential_sources.return_value = [
            {"name": "env", "description": "Env vars", "config_delta": {}},
        ]

        with patch.object(_mod, "_get_provider_strategy", return_value=strategy):
            result = _mod._get_available_credential_sources("aws")

        assert len(result) == 1
        assert result[0]["name"] == "env"

    def test_returns_default_when_strategy_raises(self):
        strategy = MagicMock()
        strategy.get_available_credential_sources.side_effect = Exception("no sources")

        with patch.object(_mod, "_get_provider_strategy", return_value=strategy):
            result = _mod._get_available_credential_sources("aws")

        assert result[0]["name"] is None


@pytest.mark.unit
class TestTestProviderCredentials:
    """Tests for _test_provider_credentials."""

    def test_returns_false_when_no_strategy(self):
        with patch.object(_mod, "_get_provider_strategy", return_value=None):
            ok, msg = _mod._test_provider_credentials("aws", None)

        assert ok is False
        assert "not supported" in msg

    def test_returns_true_on_success(self):
        strategy = MagicMock()
        strategy.test_credentials.return_value = {"success": True}

        with patch.object(_mod, "_get_provider_strategy", return_value=strategy):
            ok, msg = _mod._test_provider_credentials("aws", "default")

        assert ok is True
        assert msg == ""

    def test_returns_false_with_error_message(self):
        strategy = MagicMock()
        strategy.test_credentials.return_value = {"success": False, "error": "Bad creds"}

        with patch.object(_mod, "_get_provider_strategy", return_value=strategy):
            ok, msg = _mod._test_provider_credentials("aws", "default")

        assert ok is False
        assert msg == "Bad creds"

    def test_returns_false_on_exception(self):
        strategy = MagicMock()
        strategy.test_credentials.side_effect = RuntimeError("network error")

        with patch.object(_mod, "_get_provider_strategy", return_value=strategy):
            ok, msg = _mod._test_provider_credentials("aws", "default")

        assert ok is False
        assert "network error" in msg


@pytest.mark.unit
class TestFallbackProviderName:
    """Tests for _fallback_provider_name."""

    def test_produces_provider_type_prefix(self):
        name = _mod._fallback_provider_name("aws", {"config": {"region": "us-east-1"}})
        assert name.startswith("aws_")

    def test_hash_is_8_chars(self):
        name = _mod._fallback_provider_name("k8s", {"config": {"context": "my-cluster"}})
        parts = name.split("_", 1)
        assert len(parts) == 2
        assert len(parts[1]) == 8

    def test_same_config_produces_same_hash(self):
        provider_data = {"config": {"region": "eu-west-1"}}
        name_a = _mod._fallback_provider_name("aws", provider_data)
        name_b = _mod._fallback_provider_name("aws", provider_data)
        assert name_a == name_b

    def test_different_configs_produce_different_hashes(self):
        name_a = _mod._fallback_provider_name("aws", {"config": {"region": "us-east-1"}})
        name_b = _mod._fallback_provider_name("aws", {"config": {"region": "us-west-2"}})
        assert name_a != name_b


@pytest.mark.unit
class TestCreateDirectories:
    """Tests for _create_directories."""

    def test_creates_expected_directories(self, tmp_path):
        config_dir = tmp_path / "config"
        work_dir = tmp_path / "work"
        logs_dir = tmp_path / "logs"

        _mod._create_directories(config_dir, work_dir, logs_dir)

        assert config_dir.exists()
        assert work_dir.exists()
        assert (work_dir / ".cache").exists()
        assert logs_dir.exists()

    def test_creates_directories_with_parents(self, tmp_path):
        config_dir = tmp_path / "deep" / "nested" / "config"
        work_dir = tmp_path / "deep" / "nested" / "work"
        logs_dir = tmp_path / "deep" / "nested" / "logs"

        _mod._create_directories(config_dir, work_dir, logs_dir)

        assert config_dir.exists()
        assert work_dir.exists()
        assert logs_dir.exists()

    def test_idempotent_on_existing_directories(self, tmp_path):
        config_dir = tmp_path / "config"
        work_dir = tmp_path / "work"
        logs_dir = tmp_path / "logs"

        config_dir.mkdir()
        work_dir.mkdir()
        logs_dir.mkdir()

        # Should not raise
        _mod._create_directories(config_dir, work_dir, logs_dir)

        assert config_dir.exists()


@pytest.mark.unit
class TestPromptOperationalParams:
    """Tests for _prompt_operational_params."""

    def test_returns_empty_when_strategy_none(self):
        result = _mod._prompt_operational_params(None, container=MagicMock())
        assert result == {}

    def test_returns_empty_when_container_none(self):
        result = _mod._prompt_operational_params(MagicMock(), container=None)
        assert result == {}

    def test_optional_params_are_skipped(self):
        """Parameters with required=False are not prompted."""
        console = MagicMock()
        container = MagicMock()
        container.get.return_value = console

        strategy_class = MagicMock()
        strategy_class.get_operational_requirements.return_value = {
            "optional_field": {"required": False, "description": "Optional"}
        }

        with patch("builtins.input", side_effect=AssertionError("should not prompt")):
            result = _mod._prompt_operational_params(strategy_class, container=container)

        assert result == {}

    def test_required_free_text_param_is_prompted(self):
        console = MagicMock()
        container = MagicMock()
        container.get.return_value = console

        strategy_class = MagicMock()
        strategy_class.get_operational_requirements.return_value = {
            "region": {"required": True, "description": "Region"}
        }
        del strategy_class.get_operational_param_choices
        del strategy_class.get_operational_param_default

        with patch("builtins.input", return_value="us-east-1"):
            result = _mod._prompt_operational_params(strategy_class, container=container)

        assert result["region"] == "us-east-1"
