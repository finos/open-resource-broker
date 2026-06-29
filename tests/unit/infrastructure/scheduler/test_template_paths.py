"""Unit tests for provider-driven template path discovery in BaseSchedulerStrategy."""

from pathlib import Path
from unittest.mock import MagicMock

from orb.config.managers.configuration_manager import ConfigurationManager
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (  # noqa: E501
    HostFactorySchedulerStrategy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider_instance(provider_type: str, name: str = "p1", enabled: bool = True):
    p = MagicMock()
    p.type = provider_type
    p.name = name
    p.enabled = enabled
    return p


def _make_provider_config(provider_types: list[str]):
    """Build a mock ProviderConfig whose get_active_providers returns the given types."""
    pc = MagicMock()
    pc.get_active_providers.return_value = [
        _make_provider_instance(pt, name=f"{pt}-instance") for pt in provider_types
    ]
    return pc


def _make_strategy(
    active_types: list[str],
    provider_name: str = "my-aws-provider",
    use_hf: bool = False,
    tmp_path: Path | None = None,
    monkeypatch=None,
):
    """Construct a strategy whose config_manager returns the given active provider types."""
    config_manager = ConfigurationManager(config_dict={})
    if tmp_path and monkeypatch:
        monkeypatch.setenv("ORB_CONFIG_DIR", str(tmp_path))

    provider_config = _make_provider_config(active_types)
    config_manager.get_provider_config = MagicMock(return_value=provider_config)

    if use_hf:
        strategy = HostFactorySchedulerStrategy(config_port=config_manager, logger=MagicMock())
    else:
        strategy = DefaultSchedulerStrategy(config_port=config_manager, logger=MagicMock())

    # Make provider registry resolve to the given name
    registry = MagicMock()
    result = MagicMock()
    result.provider_name = provider_name
    result.provider_type = active_types[0] if active_types else "aws"
    registry.select_active_provider.return_value = result
    strategy._provider_registry_service = registry

    return strategy


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestActiveProviderTypePaths:
    def test_paths_include_active_provider_type_files(self, tmp_path, monkeypatch):
        """Both aws and k8s type paths appear when both are active."""
        monkeypatch.setenv("ORB_CONFIG_DIR", str(tmp_path))
        strategy = _make_strategy(["aws", "k8s"], provider_name="my-provider")
        paths = strategy.get_template_paths()
        names = [Path(p).name for p in paths]
        assert any("aws" in n for n in names), f"No aws path found in {names}"
        assert any("k8s" in n for n in names), f"No k8s path found in {names}"

    def test_paths_exclude_inactive_provider_type_files(self, tmp_path, monkeypatch):
        """When only k8s is active, no aws path should appear."""
        monkeypatch.setenv("ORB_CONFIG_DIR", str(tmp_path))
        # Create aws_templates.json on disk so resolve_file could find it
        (tmp_path / "aws_templates.json").write_text("{}")
        strategy = _make_strategy(["k8s"], provider_name="my-k8s-provider")
        paths = strategy.get_template_paths()
        names = [Path(p).name for p in paths]
        assert not any("aws" in n for n in names), (
            f"aws path should not appear when only k8s is active; got {names}"
        )
        assert any("k8s" in n for n in names), f"No k8s path found in {names}"

    def test_paths_include_generic_templates_json_last(self, tmp_path, monkeypatch):
        """templates.json is always the last path in the list."""
        monkeypatch.setenv("ORB_CONFIG_DIR", str(tmp_path))
        strategy = _make_strategy(["aws"], provider_name="my-provider")
        paths = strategy.get_template_paths()
        assert paths, "Path list must not be empty"
        assert Path(paths[-1]).name == "templates.json", (
            f"Last path must be templates.json; got {Path(paths[-1]).name}"
        )

    def test_paths_dedupe(self, tmp_path, monkeypatch):
        """Duplicate paths are removed from the final list."""
        monkeypatch.setenv("ORB_CONFIG_DIR", str(tmp_path))
        # Provide a provider whose per-scheduler filename would collide with
        # the type-based filename (e.g. when provider_name happens to equal
        # provider_type for the default fallback).
        strategy = _make_strategy(["aws"], provider_name="aws")
        paths = strategy.get_template_paths()
        assert len(paths) == len(set(paths)), f"Duplicate paths found: {paths}"

    def test_paths_fall_back_to_generic_only_on_provider_read_failure(
        self, tmp_path, monkeypatch
    ):
        """If provider config read raises, the path list contains only templates.json."""
        monkeypatch.setenv("ORB_CONFIG_DIR", str(tmp_path))
        config_manager = ConfigurationManager(config_dict={})
        config_manager.get_provider_config = MagicMock(side_effect=RuntimeError("boom"))
        strategy = DefaultSchedulerStrategy(config_port=config_manager, logger=MagicMock())
        # Also make the registry fail so the inner fallback also fails
        strategy._provider_registry_service = MagicMock(
            side_effect=RuntimeError("registry also down")
        )
        paths = strategy.get_template_paths()
        names = [Path(p).name for p in paths]
        assert "templates.json" in names, "templates.json must always be present as last fallback"


class TestHostFactoryProviderFilename:
    def test_hostfactory_provider_specific_filename_pattern_preserved(
        self, tmp_path, monkeypatch
    ):
        """HF strategy uses <provider_name>_templates.json, not <provider_type>_templates.json."""
        monkeypatch.setenv("ORB_CONFIG_DIR", str(tmp_path))
        strategy = _make_strategy(
            ["aws"], provider_name="hf-fleet", use_hf=True
        )
        paths = strategy.get_template_paths()
        names = [Path(p).name for p in paths]
        # HF fallback is provider_name-based
        assert "hf-fleet_templates.json" in names, (
            f"Expected hf-fleet_templates.json in {names}"
        )
        # The type-based file should also appear
        assert "aws_templates.json" in names, f"Expected aws_templates.json in {names}"

    def test_default_strategy_uses_type_based_filename(self, tmp_path, monkeypatch):
        """Default strategy uses <provider_type>_templates.json as the per-scheduler path."""
        monkeypatch.setenv("ORB_CONFIG_DIR", str(tmp_path))
        strategy = _make_strategy(["k8s"], provider_name="k8s-prod", use_hf=False)
        paths = strategy.get_template_paths()
        names = [Path(p).name for p in paths]
        # Default strategy: _templates_filename_fallback returns {provider_type}_templates.json
        assert "k8s_templates.json" in names, f"Expected k8s_templates.json in {names}"
