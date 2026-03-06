"""Unit tests for scheduler query handlers."""

from unittest.mock import Mock, patch

import pytest

from orb.application.dto.system import (
    SchedulerConfigurationResponse,
    SchedulerStrategyListResponse,
    ValidationResultDTO,
)
from orb.application.queries.scheduler import (
    GetSchedulerConfigurationQuery,
    ListSchedulerStrategiesQuery,
    ValidateSchedulerConfigurationQuery,
)
from orb.application.queries.scheduler_handlers import (
    GetSchedulerConfigurationHandler,
    ListSchedulerStrategiesHandler,
    ValidateSchedulerConfigurationHandler,
)


class TestListSchedulerStrategiesHandler:
    """Test cases for ListSchedulerStrategiesHandler."""

    @pytest.fixture
    def handler(self):
        """Create handler instance."""
        from orb.application.services.scheduler_registry_service import SchedulerRegistryService
        from orb.domain.services.generic_filter_service import GenericFilterService

        mock_logger = Mock()
        mock_error_handler = Mock()
        mock_scheduler_service = Mock(spec=SchedulerRegistryService)
        mock_scheduler_service.get_available_schedulers.return_value = [
            "default",
            "hostfactory",
            "hf",
        ]
        mock_filter_service = Mock(spec=GenericFilterService)
        mock_filter_service.apply_filters.side_effect = lambda items, **kwargs: items
        return ListSchedulerStrategiesHandler(
            mock_logger, mock_error_handler, mock_scheduler_service, mock_filter_service
        )

    @pytest.fixture
    def mock_registry(self):
        """Mock scheduler registry."""
        registry = Mock()
        registry.get_registered_types.return_value = ["default", "hostfactory", "hf"]
        return registry

    @pytest.fixture
    def mock_config_manager(self):
        """Mock configuration manager."""
        config_manager = Mock()
        config_manager.get_scheduler_strategy.return_value = "hostfactory"
        return config_manager

    @pytest.mark.asyncio
    async def test_list_strategies_basic(self, handler, mock_registry, mock_config_manager):
        """Test basic strategy listing."""
        query = ListSchedulerStrategiesQuery(include_current=True, include_details=False)

        with (
            patch(
                "infrastructure.scheduler.registry.get_scheduler_registry",
                return_value=mock_registry,
            ),
            patch(
                "config.manager.ConfigurationManager",
                return_value=mock_config_manager,
            ),
        ):
            response = await handler.execute_query(query)

            assert isinstance(response, SchedulerStrategyListResponse)
            assert response.total_count == 3
            assert response.current_strategy == "hostfactory"
            assert len(response.strategies) == 3

            # Check that hostfactory is marked as active
            active_strategies = [s for s in response.strategies if s.active]
            assert len(active_strategies) == 1
            assert active_strategies[0].name == "hostfactory"

    @pytest.mark.asyncio
    async def test_list_strategies_with_details(self, handler, mock_registry, mock_config_manager):
        """Test strategy listing with details."""
        query = ListSchedulerStrategiesQuery(include_current=True, include_details=True)

        with (
            patch(
                "infrastructure.scheduler.registry.get_scheduler_registry",
                return_value=mock_registry,
            ),
            patch(
                "config.manager.ConfigurationManager",
                return_value=mock_config_manager,
            ),
        ):
            response = await handler.execute_query(query)

            # Check that details are included
            for strategy in response.strategies:
                assert strategy.description is not None
                assert isinstance(strategy.capabilities, list)

    @pytest.mark.asyncio
    async def test_list_strategies_config_error(self, handler, mock_registry):
        """Test strategy listing when config manager fails."""
        query = ListSchedulerStrategiesQuery(include_current=True, include_details=False)

        with (
            patch(
                "infrastructure.scheduler.registry.get_scheduler_registry",
                return_value=mock_registry,
            ),
            patch(
                "config.manager.ConfigurationManager",
                side_effect=Exception("Config error"),
            ),
        ):
            response = await handler.execute_query(query)

            assert response.current_strategy == "unknown"
            assert response.total_count == 3


class TestGetSchedulerConfigurationHandler:
    """Test cases for GetSchedulerConfigurationHandler."""

    @pytest.fixture
    def handler(self):
        """Create handler instance."""
        from orb.application.services.scheduler_registry_service import SchedulerRegistryService

        mock_logger = Mock()
        mock_error_handler = Mock()
        mock_scheduler_service = Mock(spec=SchedulerRegistryService)
        mock_scheduler_service.get_available_schedulers.return_value = [
            "default",
            "hostfactory",
            "hf",
        ]
        mock_scheduler_service.is_scheduler_registered.side_effect = lambda name: (
            name
            in [
                "default",
                "hostfactory",
                "hf",
            ]
        )
        return GetSchedulerConfigurationHandler(
            mock_logger, mock_error_handler, mock_scheduler_service
        )

    @pytest.fixture
    def mock_config_manager(self):
        """Mock configuration manager."""
        config_manager = Mock()
        config_manager.get_scheduler_strategy.return_value = "hostfactory"

        # Mock app config
        app_config = Mock()
        scheduler_config = Mock()
        scheduler_config.model_dump.return_value = {
            "type": "hostfactory",
            "config_root": "config",
        }
        app_config.scheduler = scheduler_config
        config_manager.app_config = app_config

        return config_manager

    @pytest.fixture
    def mock_registry(self):
        """Mock scheduler registry."""
        registry = Mock()
        registry.get_registered_types.return_value = ["default", "hostfactory", "hf"]
        return registry

    @pytest.mark.asyncio
    async def test_get_current_configuration(self, handler, mock_config_manager, mock_registry):
        """Test getting current scheduler configuration."""
        query = GetSchedulerConfigurationQuery()

        with (
            patch(
                "config.manager.ConfigurationManager",
                return_value=mock_config_manager,
            ),
            patch(
                "infrastructure.scheduler.registry.get_scheduler_registry",
                return_value=mock_registry,
            ),
        ):
            response = await handler.execute_query(query)

            assert isinstance(response, SchedulerConfigurationResponse)
            assert response.scheduler_name == "hostfactory"
            assert response.active is True
            assert response.valid is True
            assert response.found is True
            assert "type" in response.configuration

    @pytest.mark.asyncio
    async def test_get_specific_configuration(self, handler, mock_config_manager, mock_registry):
        """Test getting specific scheduler configuration."""
        query = GetSchedulerConfigurationQuery(scheduler_name="default")

        with (
            patch(
                "config.manager.ConfigurationManager",
                return_value=mock_config_manager,
            ),
            patch(
                "infrastructure.scheduler.registry.get_scheduler_registry",
                return_value=mock_registry,
            ),
        ):
            response = await handler.execute_query(query)

            assert response.scheduler_name == "default"
            assert response.active is False  # default is not the current strategy
            assert response.valid is True  # default is registered

    @pytest.mark.asyncio
    async def test_get_configuration_not_registered(
        self, handler, mock_config_manager, mock_registry
    ):
        """Test getting configuration for unregistered scheduler."""
        query = GetSchedulerConfigurationQuery(scheduler_name="unknown")

        with (
            patch(
                "config.manager.ConfigurationManager",
                return_value=mock_config_manager,
            ),
            patch(
                "infrastructure.scheduler.registry.get_scheduler_registry",
                return_value=mock_registry,
            ),
        ):
            response = await handler.execute_query(query)

            assert response.scheduler_name == "unknown"
            assert response.valid is False  # unknown is not registered


class TestValidateSchedulerConfigurationHandler:
    """Test cases for ValidateSchedulerConfigurationHandler."""

    @pytest.fixture
    def handler(self):
        """Create handler instance."""
        from unittest.mock import Mock

        from orb.application.services.scheduler_registry_service import SchedulerRegistryService

        mock_logger = Mock()
        mock_error_handler = Mock()
        mock_scheduler_service = Mock(spec=SchedulerRegistryService)
        mock_scheduler_service.get_available_schedulers.return_value = [
            "default",
            "hostfactory",
            "hf",
        ]
        mock_scheduler_service.create_scheduler_strategy.return_value = Mock()
        return ValidateSchedulerConfigurationHandler(
            mock_logger, mock_error_handler, mock_scheduler_service
        )

    @pytest.fixture
    def mock_config_manager(self):
        """Mock configuration manager."""
        config_manager = Mock()
        config_manager.get_scheduler_strategy.return_value = "hostfactory"

        # Mock app config
        app_config = Mock()
        scheduler_config = Mock()
        scheduler_config.type = "hostfactory"
        app_config.scheduler = scheduler_config
        config_manager.app_config = app_config

        return config_manager

    @pytest.fixture
    def mock_registry(self):
        """Mock scheduler registry."""
        registry = Mock()
        registry.get_registered_types.return_value = ["default", "hostfactory", "hf"]
        registry.create_strategy.return_value = Mock()  # Mock strategy instance
        return registry

    @pytest.mark.asyncio
    async def test_validate_current_scheduler(self, handler, mock_config_manager, mock_registry):
        """Test validating current scheduler configuration."""
        query = ValidateSchedulerConfigurationQuery()

        with (
            patch(
                "config.manager.ConfigurationManager",
                return_value=mock_config_manager,
            ),
            patch(
                "infrastructure.scheduler.registry.get_scheduler_registry",
                return_value=mock_registry,
            ),
        ):
            response = await handler.execute_query(query)

            assert isinstance(response, ValidationResultDTO)
            assert response.is_valid is True
            assert len(response.validation_errors) == 0

    @pytest.mark.asyncio
    async def test_validate_unregistered_scheduler(
        self, handler, mock_config_manager, mock_registry
    ):
        """Test validating unregistered scheduler."""
        query = ValidateSchedulerConfigurationQuery(scheduler_name="unknown")

        with (
            patch(
                "config.manager.ConfigurationManager",
                return_value=mock_config_manager,
            ),
            patch(
                "infrastructure.scheduler.registry.get_scheduler_registry",
                return_value=mock_registry,
            ),
        ):
            response = await handler.execute_query(query)

            assert response.is_valid is False
            assert len(response.validation_errors) > 0
            assert "not registered" in response.validation_errors[0]

    @pytest.mark.asyncio
    async def test_validate_strategy_creation_failure(
        self, handler, mock_config_manager, mock_registry
    ):
        """Test validation when strategy creation fails."""
        query = ValidateSchedulerConfigurationQuery(scheduler_name="hostfactory")

        # Make the handler's scheduler service raise on strategy creation
        handler._scheduler_service.create_scheduler_strategy.side_effect = Exception(
            "Creation failed"
        )

        with (
            patch(
                "config.manager.ConfigurationManager",
                return_value=mock_config_manager,
            ),
            patch(
                "infrastructure.scheduler.registry.get_scheduler_registry",
                return_value=mock_registry,
            ),
        ):
            response = await handler.execute_query(query)

            assert response.is_valid is False
            assert len(response.validation_errors) > 0
            assert "Creation failed" in response.validation_errors[0]
