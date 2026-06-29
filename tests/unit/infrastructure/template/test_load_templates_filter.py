"""Unit tests for provider-type filtering in TemplateConfigurationManager."""

from typing import Any
from unittest.mock import MagicMock

from orb.config.managers.configuration_manager import ConfigurationManager
from orb.domain.base.ports.logging_port import LoggingPort
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager
from orb.infrastructure.template.template_cache_service import create_template_cache_service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> LoggingPort:
    logger = MagicMock(spec=LoggingPort)
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_provider_instance(provider_type: str):
    p = MagicMock()
    p.type = provider_type
    return p


def _make_manager(active_types: list[str]) -> tuple[TemplateConfigurationManager, LoggingPort]:
    """Build a TemplateConfigurationManager whose provider config returns active_types."""
    logger = _make_logger()
    config_manager = ConfigurationManager(config_dict={})
    if active_types:
        pc = MagicMock()
        pc.get_active_providers.return_value = [_make_provider_instance(t) for t in active_types]
        config_manager.get_provider_config = MagicMock(return_value=pc)
    else:
        config_manager.get_provider_config = MagicMock(return_value=None)

    strategy = MagicMock()
    strategy.get_template_paths.return_value = []

    cache_service = create_template_cache_service("noop", logger)
    manager = TemplateConfigurationManager(
        config_manager=config_manager,
        scheduler_strategy=strategy,
        logger=logger,
        cache_service=cache_service,
    )
    return manager, logger


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFilterTemplatesByActiveProviders:
    def test_load_filters_templates_for_inactive_provider_type(self):
        """Templates with a provider_type not in the active set are dropped."""
        manager, _ = _make_manager(["k8s"])
        raw: list[dict[str, Any]] = [
            {"template_id": "k8s-tpl", "provider_type": "k8s"},
            {"template_id": "aws-tpl", "provider_type": "aws"},  # inactive
        ]
        result = manager._filter_templates_by_active_providers(raw)
        ids = [t["template_id"] for t in result]
        assert "k8s-tpl" in ids
        assert "aws-tpl" not in ids

    def test_load_keeps_templates_with_no_provider_type(self):
        """Templates without a provider_type field are retained unconditionally."""
        manager, _ = _make_manager(["k8s"])
        raw: list[dict[str, Any]] = [
            {"template_id": "legacy-tpl"},  # no provider_type
            {"template_id": "k8s-tpl", "provider_type": "k8s"},
        ]
        result = manager._filter_templates_by_active_providers(raw)
        ids = [t["template_id"] for t in result]
        assert "legacy-tpl" in ids
        assert "k8s-tpl" in ids

    def test_load_emits_debug_log_per_dropped_template(self):
        """A debug message is emitted for each template dropped by the filter."""
        manager, logger = _make_manager(["k8s"])
        raw: list[dict[str, Any]] = [
            {"template_id": "aws-tpl-1", "provider_type": "aws"},
            {"template_id": "aws-tpl-2", "provider_type": "aws"},
            {"template_id": "k8s-tpl", "provider_type": "k8s"},
        ]
        manager._filter_templates_by_active_providers(raw)
        # Two aws templates dropped → two debug calls that mention "Dropping"
        dropping_calls = [call for call in logger.debug.call_args_list if "Dropping" in str(call)]
        assert len(dropping_calls) == 2, (
            f"Expected 2 'Dropping' debug calls, got {len(dropping_calls)}: {dropping_calls}"
        )

    def test_load_emits_debug_log_for_untyped_retained_template(self):
        """A debug message is emitted when an untyped template is retained."""
        manager, logger = _make_manager(["k8s"])
        raw: list[dict[str, Any]] = [
            {"template_id": "no-type-tpl"},
        ]
        manager._filter_templates_by_active_providers(raw)
        retain_calls = [
            call for call in logger.debug.call_args_list if "without a provider_type" in str(call)
        ]
        assert len(retain_calls) == 1, (
            f"Expected 1 retain debug call, got {len(retain_calls)}: {retain_calls}"
        )

    def test_filter_returns_all_when_active_types_unavailable(self):
        """When the active-provider set cannot be determined, all templates are kept."""
        manager, _ = _make_manager([])
        raw: list[dict[str, Any]] = [
            {"template_id": "aws-tpl", "provider_type": "aws"},
            {"template_id": "k8s-tpl", "provider_type": "k8s"},
        ]
        result = manager._filter_templates_by_active_providers(raw)
        assert len(result) == 2, "All templates must be kept when active types are unknown"

    def test_filter_multi_active_keeps_all_matching(self):
        """When multiple provider types are active, templates for any of them are kept."""
        manager, _ = _make_manager(["aws", "k8s"])
        raw: list[dict[str, Any]] = [
            {"template_id": "aws-tpl", "provider_type": "aws"},
            {"template_id": "k8s-tpl", "provider_type": "k8s"},
            {"template_id": "other-tpl", "provider_type": "other"},
        ]
        result = manager._filter_templates_by_active_providers(raw)
        ids = [t["template_id"] for t in result]
        assert "aws-tpl" in ids
        assert "k8s-tpl" in ids
        assert "other-tpl" not in ids
