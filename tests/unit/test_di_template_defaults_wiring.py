"""Tests verifying template_defaults_service is wired into scheduler strategies via DI."""

from unittest.mock import MagicMock

from orb.infrastructure.scheduler.registration import (
    create_default_strategy,
    create_symphony_hostfactory_strategy,
)


class TestTemplateDefaultsWiring:
    """Verify template_defaults_service is injected when a DI container is provided."""

    def _make_container(self, template_defaults_service=None):
        """Build a minimal mock container with get_optional support."""
        from orb.domain.base.ports.configuration_port import ConfigurationPort
        from orb.domain.base.ports.logging_port import LoggingPort
        from orb.domain.template.ports.template_defaults_port import TemplateDefaultsPort

        mock_defaults = template_defaults_service or MagicMock()

        def get_optional(port_type):
            if port_type is TemplateDefaultsPort:
                return mock_defaults
            if port_type is ConfigurationPort:
                return None
            if port_type is LoggingPort:
                return None
            return None

        container = MagicMock()
        container.get_optional = get_optional
        return container, mock_defaults

    def test_hostfactory_strategy_receives_template_defaults_service(self):
        """HostFactorySchedulerStrategy gets template_defaults_service from the container."""
        container, mock_defaults = self._make_container()

        strategy = create_symphony_hostfactory_strategy(container)

        assert strategy._template_defaults_service is not None
        assert strategy._template_defaults_service is mock_defaults

    def test_default_strategy_receives_template_defaults_service(self):
        """DefaultSchedulerStrategy gets template_defaults_service from the container."""
        container, mock_defaults = self._make_container()

        strategy = create_default_strategy(container)

        assert strategy._template_defaults_service is not None
        assert strategy._template_defaults_service is mock_defaults

    def test_hostfactory_strategy_template_defaults_none_without_container(self):
        """When no DI container is provided, template_defaults_service is None."""
        strategy = create_symphony_hostfactory_strategy({})

        assert strategy._template_defaults_service is None

    def test_default_strategy_template_defaults_none_without_container(self):
        """When no DI container is provided, template_defaults_service is None."""
        strategy = create_default_strategy({})

        assert strategy._template_defaults_service is None

    def test_hostfactory_strategy_type(self):
        """Factory returns a HostFactorySchedulerStrategy instance."""
        from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
            HostFactorySchedulerStrategy,
        )

        container, _ = self._make_container()
        strategy = create_symphony_hostfactory_strategy(container)

        assert isinstance(strategy, HostFactorySchedulerStrategy)

    def test_default_strategy_type(self):
        """Factory returns a DefaultSchedulerStrategy instance."""
        from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

        container, _ = self._make_container()
        strategy = create_default_strategy(container)

        assert isinstance(strategy, DefaultSchedulerStrategy)
