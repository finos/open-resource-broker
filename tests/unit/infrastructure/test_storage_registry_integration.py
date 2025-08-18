"""Integration tests for Storage Registry Pattern."""

from unittest.mock import Mock, patch

import pytest

from infrastructure.registry.storage_registry import (
    get_storage_registry,
    reset_storage_registry,
)


class TestStorageRegistryIntegration:
    """Test storage registry integration with repository factory."""

    def setup_method(self):
        """Set up test fixtures."""
        reset_storage_registry()

    def teardown_method(self):
        """Clean up after tests."""
        reset_storage_registry()

    def test_repository_factory_uses_storage_registry(self):
        """Test that repository factory uses storage registry."""
        from infrastructure.utilities.factories.repository_factory import (
            RepositoryFactory,
        )

        # Mock configuration manager
        mock_config_manager = Mock()
        mock_config_manager.get_storage_strategy.return_value = "json"
        mock_config_manager.get_app_config.return_value = Mock()

        # Mock storage registry
        mock_registry = Mock()
        mock_strategy = Mock()
        mock_registry.create_strategy.return_value = mock_strategy

        # Mock repository class
        mock_repository = Mock()

        with patch(
            "src.infrastructure.utilities.factories.repository_factory.get_storage_registry"
        ) as mock_get_registry, patch(
            "src.infrastructure.persistence.repositories.request_repository.RequestRepository"
        ) as mock_repo_class:

            mock_get_registry.return_value = mock_registry
            mock_repo_class.return_value = mock_repository

            # Create repository factory
            factory = RepositoryFactory(mock_config_manager)

            # Create repository
            result = factory.create_request_repository()

            # Verify storage registry was used
            mock_registry.create_strategy.assert_called_once_with(
                "json", mock_config_manager.get_app_config.return_value
            )
            mock_repo_class.assert_called_once_with(mock_strategy)
            assert result == mock_repository

    def test_di_container_uses_repository_factory(self):
        """Test that DI container uses repository factory."""
        from infrastructure.di.container import DIContainer
        from infrastructure.di.infrastructure_services import (
            _register_repository_services,
        )

        # Mock container
        mock_container = Mock(spec=DIContainer)
        mock_config_manager = Mock()
        mock_container.get_config_manager.return_value = mock_config_manager

        # Mock repository factory
        mock_factory = Mock()
        mock_repository = Mock()
        mock_factory.create_request_repository.return_value = mock_repository

        with patch("src.infrastructure.persistence.registration.register_all_storage_types"), patch(
            "src.infrastructure.utilities.factories.repository_factory.RepositoryFactory"
        ) as mock_factory_class:

            mock_factory_class.return_value = mock_factory

            # Register repository services
            _register_repository_services(mock_container)

            # Verify repository factory was registered
            assert mock_container.register_singleton.call_count >= 1

            # Find the repository factory registration call
            factory_registered = False
            for call in mock_container.register_singleton.call_args_list:
                if len(call[0]) >= 1 and "RepositoryFactory" in str(call[0][0]):
                    factory_registered = True
                    break

            assert factory_registered, "RepositoryFactory should be registered with DI container"

    def test_unit_of_work_creation_via_registry(self):
        """Test unit of work creation via storage registry."""
        from infrastructure.utilities.factories.repository_factory import (
            RepositoryFactory,
        )

        # Mock configuration manager
        mock_config_manager = Mock()
        mock_config_manager.get_storage_strategy.return_value = "json"

        # Mock storage registry
        mock_registry = Mock()
        mock_unit_of_work = Mock()
        mock_registry.create_unit_of_work.return_value = mock_unit_of_work

        with patch(
            "src.infrastructure.utilities.factories.repository_factory.get_storage_registry"
        ) as mock_get_registry:
            mock_get_registry.return_value = mock_registry

            # Create repository factory
            factory = RepositoryFactory(mock_config_manager)

            # Create unit of work
            result = factory.create_unit_of_work()

            # Verify storage registry was used
            mock_registry.create_unit_of_work.assert_called_once_with("json", mock_config_manager)
            assert result == mock_unit_of_work

    def test_storage_registration_includes_unit_of_work(self):
        """Test that storage registration includes unit of work factory."""
        registry = get_storage_registry()

        # Mock factories
        strategy_factory = Mock()
        config_factory = Mock()
        unit_of_work_factory = Mock()

        # Register storage with unit of work factory
        registry.register_storage(
            storage_type="test_storage",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
            unit_of_work_factory=unit_of_work_factory,
        )

        # Test unit of work creation
        mock_config = Mock()
        mock_unit_of_work = Mock()
        unit_of_work_factory.return_value = mock_unit_of_work

        result = registry.create_unit_of_work("test_storage", mock_config)

        unit_of_work_factory.assert_called_once_with(mock_config)
        assert result == mock_unit_of_work

    def test_storage_registration_without_unit_of_work_factory(self):
        """Test storage registration without unit of work factory."""
        from infrastructure.registry.storage_registry import UnsupportedStorageError

        registry = get_storage_registry()

        # Register storage without unit of work factory
        registry.register_storage(
            storage_type="test_storage",
            strategy_factory=Mock(),
            config_factory=Mock(),
            # No unit_of_work_factory
        )

        # Test unit of work creation should fail
        with pytest.raises(UnsupportedStorageError, match="Unit of work factory not registered"):
            registry.create_unit_of_work("test_storage", Mock())

    def test_end_to_end_repository_creation(self):
        """Test end-to-end repository creation flow."""
        from infrastructure.utilities.factories.repository_factory import (
            RepositoryFactory,
        )

        registry = get_storage_registry()

        # Mock complete storage registration
        mock_strategy = Mock()
        mock_config = Mock()
        mock_unit_of_work = Mock()

        strategy_factory = Mock(return_value=mock_strategy)
        config_factory = Mock(return_value=mock_config)
        unit_of_work_factory = Mock(return_value=mock_unit_of_work)

        registry.register_storage(
            storage_type="integration_test",
            strategy_factory=strategy_factory,
            config_factory=config_factory,
            unit_of_work_factory=unit_of_work_factory,
        )

        # Mock configuration manager
        mock_config_manager = Mock()
        mock_config_manager.get_storage_strategy.return_value = "integration_test"
        mock_app_config = Mock()
        mock_config_manager.get_app_config.return_value = mock_app_config

        # Mock repository class
        mock_repository = Mock()

        with patch(
            "src.infrastructure.persistence.repositories.request_repository.RequestRepository"
        ) as mock_repo_class:
            mock_repo_class.return_value = mock_repository

            # Create repository factory
            factory = RepositoryFactory(mock_config_manager)

            # Test repository creation
            result_repo = factory.create_request_repository()

            # Verify complete flow
            strategy_factory.assert_called_once_with(mock_app_config)
            mock_repo_class.assert_called_once_with(mock_strategy)
            assert result_repo == mock_repository

            # Test unit of work creation
            result_uow = factory.create_unit_of_work()

            unit_of_work_factory.assert_called_once_with(mock_config_manager)
            assert result_uow == mock_unit_of_work
