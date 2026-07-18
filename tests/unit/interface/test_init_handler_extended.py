"""Extended unit tests for init_command_handler — handle_init and helpers."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

import orb.interface.init_command_handler as _mod

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _make_console():
    from orb.domain.base.ports.console_port import ConsolePort

    return MagicMock(spec=ConsolePort)


def _make_container(console=None):

    container = MagicMock()
    mock_console = console or _make_console()
    container.get.return_value = mock_console
    return container, mock_console


def _mock_scheduler_registry(extra=None):
    reg = MagicMock()
    reg.get_extra_config_for_type.return_value = extra or {}
    return reg


# ---------------------------------------------------------------------------
# handle_init — already initialized, no force
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleInitAlreadyInitialized:
    @pytest.mark.asyncio
    async def test_existing_config_without_force_returns_1(self, tmp_path):
        """Config already exists and --force not set → returns 1."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")

        container, _ = _make_container()

        args = _make_args(
            config_dir=str(tmp_path),
            force=False,
            non_interactive=True,
            provider_type="aws",
            scheduler="default",
        )
        args._container = container

        result = await _mod.handle_init(args)

        assert result == 1

    @pytest.mark.asyncio
    async def test_existing_config_with_force_proceeds(self, tmp_path):
        """Config already exists but --force set → proceeds to write new config."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")

        container, _ = _make_container()

        mock_strategy_class = MagicMock()
        mock_strategy_class.get_cli_infrastructure_defaults.return_value = {}
        mock_strategy_class.get_cli_provider_config.return_value = {}
        mock_strategy_class.generate_provider_name.return_value = "aws_test"
        mock_strategy_class.get_cli_extra_config_keys.return_value = set()

        args = _make_args(
            config_dir=str(tmp_path),
            force=True,
            non_interactive=True,
            provider_type="aws",
            scheduler="default",
        )
        args._container = container

        with patch.object(
            _mod,
            "_get_available_providers",
            return_value=[{"type": "aws", "display_name": "AWS", "description": ""}],
        ):
            with patch.object(_mod, "_get_provider_strategy", return_value=mock_strategy_class):
                with patch.object(_mod, "_copy_scripts"):
                    with patch(
                        "orb.interface.init_command_handler.get_logs_location",
                        return_value=tmp_path / "logs",
                    ):
                        with patch(
                            "orb.interface.init_command_handler.get_work_location",
                            return_value=tmp_path / "work",
                        ):
                            with patch(
                                "orb.interface.init_command_handler.get_scripts_location",
                                return_value=tmp_path / "scripts",
                            ):
                                with patch(
                                    "orb.infrastructure.scheduler.registry.get_scheduler_registry",
                                    return_value=_mock_scheduler_registry(),
                                ):
                                    with patch(
                                        "orb.interface.init_command_handler.CLISpecRegistry.get_or_none",
                                        return_value=None,
                                    ):
                                        result = await _mod.handle_init(args)

        assert result == 0


# ---------------------------------------------------------------------------
# handle_init — non_interactive path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleInitNonInteractive:
    @pytest.mark.asyncio
    async def test_non_interactive_creates_config_file(self, tmp_path):
        """non_interactive=True → config.json is written."""
        container, _ = _make_container()

        mock_strategy_class = MagicMock()
        mock_strategy_class.get_cli_infrastructure_defaults.return_value = {}
        mock_strategy_class.get_cli_provider_config.return_value = {"region": "us-east-1"}
        mock_strategy_class.generate_provider_name.return_value = "aws_default_us-east-1"
        mock_strategy_class.get_cli_extra_config_keys.return_value = set()

        args = _make_args(
            config_dir=str(tmp_path),
            force=False,
            non_interactive=True,
            provider_type="aws",
            scheduler="default",
        )
        args._container = container

        with patch.object(
            _mod,
            "_get_available_providers",
            return_value=[{"type": "aws", "display_name": "AWS", "description": ""}],
        ):
            with patch.object(_mod, "_get_provider_strategy", return_value=mock_strategy_class):
                with patch.object(_mod, "_copy_scripts"):
                    with patch(
                        "orb.interface.init_command_handler.get_logs_location",
                        return_value=tmp_path / "logs",
                    ):
                        with patch(
                            "orb.interface.init_command_handler.get_work_location",
                            return_value=tmp_path / "work",
                        ):
                            with patch(
                                "orb.interface.init_command_handler.get_scripts_location",
                                return_value=tmp_path / "scripts",
                            ):
                                with patch(
                                    "orb.infrastructure.scheduler.registry.get_scheduler_registry",
                                    return_value=_mock_scheduler_registry(),
                                ):
                                    with patch(
                                        "orb.interface.init_command_handler.CLISpecRegistry.get_or_none",
                                        return_value=None,
                                    ):
                                        result = await _mod.handle_init(args)

        assert result == 0
        assert (tmp_path / "config.json").exists()

    @pytest.mark.asyncio
    async def test_non_interactive_no_providers_returns_1(self, tmp_path):
        """non_interactive=True with no providers registered → returns 1."""
        container, _ = _make_container()

        args = _make_args(
            config_dir=str(tmp_path),
            force=False,
            non_interactive=True,
            provider_type=None,
            scheduler="default",
        )
        args._container = container

        with patch.object(_mod, "_get_available_providers", return_value=[]):
            result = await _mod.handle_init(args)

        assert result == 1

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_returns_1(self, tmp_path):
        """KeyboardInterrupt during init → returns 1."""
        container, _ = _make_container()

        args = _make_args(
            config_dir=str(tmp_path),
            force=False,
            non_interactive=True,
            provider_type="aws",
            scheduler="default",
        )
        args._container = container

        with patch.object(_mod, "_get_available_providers", side_effect=KeyboardInterrupt):
            result = await _mod.handle_init(args)

        assert result == 1

    @pytest.mark.asyncio
    async def test_generic_exception_returns_1(self, tmp_path):
        """Unexpected exception during init → returns 1."""
        container, _ = _make_container()

        args = _make_args(
            config_dir=str(tmp_path),
            force=False,
            non_interactive=True,
            provider_type="aws",
            scheduler="default",
        )
        args._container = container

        with patch.object(_mod, "_get_available_providers", side_effect=RuntimeError("unexpected")):
            result = await _mod.handle_init(args)

        assert result == 1


# ---------------------------------------------------------------------------
# _get_default_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDefaultConfig:
    def test_uses_provider_type_from_args(self):
        """provider_type from args is used, not from available providers list."""
        container, _ = _make_container()

        mock_strategy = MagicMock()
        mock_strategy.get_cli_infrastructure_defaults.return_value = {}
        mock_strategy.get_cli_provider_config.return_value = {"region": "ap-southeast-1"}

        args = _make_args(provider_type="k8s", scheduler="default")

        with patch.object(
            _mod,
            "_get_available_providers",
            return_value=[{"type": "aws", "display_name": "AWS", "description": ""}],
        ):
            with patch.object(_mod, "_get_provider_strategy", return_value=mock_strategy):
                with patch.object(
                    _mod,
                    "CLISpecRegistry",
                    new_callable=lambda: type(
                        "R", (), {"get_or_none": staticmethod(lambda _: None)}
                    ),
                ):
                    result = _mod._get_default_config(args, container)

        assert result["providers"][0]["type"] == "k8s"

    def test_falls_back_to_first_available_provider(self):
        """provider_type=None falls back to first available provider."""
        container, _ = _make_container()

        mock_strategy = MagicMock()
        mock_strategy.get_cli_infrastructure_defaults.return_value = {}
        mock_strategy.get_cli_provider_config.return_value = {}

        args = _make_args(provider_type=None, scheduler="default")

        with patch.object(
            _mod,
            "_get_available_providers",
            return_value=[{"type": "aws", "display_name": "AWS", "description": ""}],
        ):
            with patch.object(_mod, "_get_provider_strategy", return_value=mock_strategy):
                with patch.object(
                    _mod,
                    "CLISpecRegistry",
                    new_callable=lambda: type(
                        "R", (), {"get_or_none": staticmethod(lambda _: None)}
                    ),
                ):
                    result = _mod._get_default_config(args, container)

        assert result["providers"][0]["type"] == "aws"

    def test_raises_when_no_providers_and_no_provider_type(self):
        """No providers and no provider_type arg → ValueError raised."""
        container, _ = _make_container()

        args = _make_args(provider_type=None, scheduler="default")

        with patch.object(_mod, "_get_available_providers", return_value=[]):
            with pytest.raises(ValueError, match="No providers registered"):
                _mod._get_default_config(args, container)

    def test_scheduler_type_forwarded(self):
        """scheduler from args is forwarded into result dict."""
        container, _ = _make_container()

        mock_strategy = MagicMock()
        mock_strategy.get_cli_infrastructure_defaults.return_value = {}
        mock_strategy.get_cli_provider_config.return_value = {}

        args = _make_args(provider_type="aws", scheduler="hostfactory")

        with patch.object(
            _mod,
            "_get_available_providers",
            return_value=[{"type": "aws", "display_name": "AWS", "description": ""}],
        ):
            with patch.object(_mod, "_get_provider_strategy", return_value=mock_strategy):
                with patch.object(
                    _mod,
                    "CLISpecRegistry",
                    new_callable=lambda: type(
                        "R", (), {"get_or_none": staticmethod(lambda _: None)}
                    ),
                ):
                    result = _mod._get_default_config(args, container)

        assert result["scheduler_type"] == "hostfactory"


# ---------------------------------------------------------------------------
# _get_available_providers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAvailableProviders:
    def test_returns_empty_list_when_no_registry(self):
        """No registry provided and container.get fails → returns []."""
        result = _mod._get_available_providers(container=None, registry=None)
        assert result == []

    def test_registry_with_no_providers_returns_empty_list(self):
        """Registry with no registered providers → returns []."""
        mock_registry = MagicMock()
        mock_registry.get_registered_providers.return_value = []

        result = _mod._get_available_providers(registry=mock_registry)

        assert result == []

    def test_registry_with_providers_returns_list(self):
        """Registry with providers → returns sorted list of provider dicts."""
        mock_registry = MagicMock()
        mock_registry.get_registered_providers.return_value = ["k8s", "aws"]

        result = _mod._get_available_providers(registry=mock_registry)

        assert len(result) == 2
        types = [p["type"] for p in result]
        assert "aws" in types
        assert "k8s" in types

    def test_registry_exception_returns_empty_list(self):
        """Registry.get_registered_providers raising → returns []."""
        mock_registry = MagicMock()
        mock_registry.get_registered_providers.side_effect = RuntimeError("unavailable")

        result = _mod._get_available_providers(registry=mock_registry)

        assert result == []


# ---------------------------------------------------------------------------
# _test_provider_credentials
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTestProviderCredentials:
    def test_strategy_none_returns_failure(self):
        """No strategy for provider type → (False, 'Provider type not supported')."""
        with patch.object(_mod, "_get_provider_strategy", return_value=None):
            ok, msg = _mod._test_provider_credentials("unknown", None)

        assert ok is False
        assert "not supported" in msg.lower()

    def test_strategy_returns_success(self):
        """Strategy.test_credentials returns success=True → (True, '')."""
        mock_strategy = MagicMock()
        mock_strategy.test_credentials.return_value = {"success": True}

        with patch.object(_mod, "_get_provider_strategy", return_value=mock_strategy):
            ok, msg = _mod._test_provider_credentials("aws", "default")

        assert ok is True
        assert msg == ""

    def test_strategy_returns_failure(self):
        """Strategy.test_credentials returns success=False → (False, error message)."""
        mock_strategy = MagicMock()
        mock_strategy.test_credentials.return_value = {
            "success": False,
            "error": "invalid token",
        }

        with patch.object(_mod, "_get_provider_strategy", return_value=mock_strategy):
            ok, msg = _mod._test_provider_credentials("aws", "default")

        assert ok is False
        assert "invalid token" in msg

    def test_strategy_raises_returns_failure(self):
        """Strategy.test_credentials raises → (False, str(exception))."""
        mock_strategy = MagicMock()
        mock_strategy.test_credentials.side_effect = Exception("connection refused")

        with patch.object(_mod, "_get_provider_strategy", return_value=mock_strategy):
            ok, msg = _mod._test_provider_credentials("aws", "default")

        assert ok is False
        assert "connection refused" in msg


# ---------------------------------------------------------------------------
# _get_available_credential_sources
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAvailableCredentialSources:
    def test_no_strategy_returns_default(self):
        """No strategy → [{'name': None, 'description': 'Default credentials'}]."""
        with patch.object(_mod, "_get_provider_strategy", return_value=None):
            sources = _mod._get_available_credential_sources("aws")

        assert len(sources) == 1
        assert sources[0]["name"] is None

    def test_strategy_returns_sources(self):
        """Strategy.get_available_credential_sources → those sources are returned."""
        mock_strategy = MagicMock()
        mock_strategy.get_available_credential_sources.return_value = [
            {"name": "profile", "description": "AWS profile"}
        ]

        with patch.object(_mod, "_get_provider_strategy", return_value=mock_strategy):
            sources = _mod._get_available_credential_sources("aws")

        assert sources[0]["name"] == "profile"

    def test_strategy_empty_sources_returns_default(self):
        """Strategy returns empty list → fallback to default source."""
        mock_strategy = MagicMock()
        mock_strategy.get_available_credential_sources.return_value = []

        with patch.object(_mod, "_get_provider_strategy", return_value=mock_strategy):
            sources = _mod._get_available_credential_sources("aws")

        assert len(sources) == 1
        assert sources[0]["name"] is None

    def test_strategy_exception_returns_default(self):
        """Strategy.get_available_credential_sources raises → default source returned."""
        mock_strategy = MagicMock()
        mock_strategy.get_available_credential_sources.side_effect = RuntimeError("err")

        with patch.object(_mod, "_get_provider_strategy", return_value=mock_strategy):
            sources = _mod._get_available_credential_sources("aws")

        assert sources[0]["name"] is None


# ---------------------------------------------------------------------------
# _fallback_provider_name
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFallbackProviderName:
    def test_name_starts_with_provider_type(self):
        """Fallback name starts with provider_type_."""
        name = _mod._fallback_provider_name("aws", {"config": {"region": "us-east-1"}})
        assert name.startswith("aws_")

    def test_hash_portion_is_8_chars(self):
        """Hash portion is exactly 8 hex characters."""
        name = _mod._fallback_provider_name("k8s", {"config": {"context": "my-cluster"}})
        parts = name.split("_", 1)
        assert len(parts[1]) == 8

    def test_deterministic_for_same_config(self):
        """Same config → same hash every time."""
        data = {"config": {"region": "eu-west-1", "profile": "prod"}}
        n1 = _mod._fallback_provider_name("aws", data)
        n2 = _mod._fallback_provider_name("aws", data)
        assert n1 == n2

    def test_different_config_yields_different_hash(self):
        """Different config → different hash."""
        n1 = _mod._fallback_provider_name("aws", {"config": {"region": "us-east-1"}})
        n2 = _mod._fallback_provider_name("aws", {"config": {"region": "eu-west-2"}})
        assert n1 != n2


# ---------------------------------------------------------------------------
# _create_directories
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateDirectories:
    def test_creates_expected_dirs(self, tmp_path):
        """_create_directories creates config_dir, work_dir, work/.cache, logs_dir."""
        config_dir = tmp_path / "config"
        work_dir = tmp_path / "work"
        logs_dir = tmp_path / "logs"

        _mod._create_directories(config_dir, work_dir, logs_dir)

        assert config_dir.exists()
        assert work_dir.exists()
        assert (work_dir / ".cache").exists()
        assert logs_dir.exists()


# ---------------------------------------------------------------------------
# _discover_infrastructure — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscoverInfrastructureErrors:
    def test_strategy_is_none_returns_empty(self):
        """create_strategy_by_type returning None → empty dict returned."""
        container, mock_console = _make_container()
        mock_registry = MagicMock()
        mock_registry.create_strategy_by_type.return_value = None

        result = _mod._discover_infrastructure(
            "aws", {"region": "us-east-1"}, mock_registry, container
        )

        assert result == {}
        mock_console.error.assert_called()

    def test_no_discover_interactive_method_returns_empty(self):
        """Strategy without discover_infrastructure_interactive → empty dict."""
        container, _ = _make_container()
        mock_strategy = MagicMock(spec=[])  # no methods
        mock_registry = MagicMock()
        mock_registry.create_strategy_by_type.return_value = mock_strategy

        result = _mod._discover_infrastructure(
            "aws", {"region": "us-east-1"}, mock_registry, container
        )

        assert result == {}

    def test_discovery_exception_returns_empty(self):
        """Exception during discovery → empty dict, no re-raise."""
        container, _ = _make_container()
        mock_registry = MagicMock()
        mock_registry.create_strategy_by_type.side_effect = RuntimeError("auth failed")

        result = _mod._discover_infrastructure(
            "aws", {"region": "us-east-1"}, mock_registry, container
        )

        assert result == {}
