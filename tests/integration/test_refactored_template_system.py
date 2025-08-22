"""Integration tests for the refactored template configuration system.

Tests the complete refactored architecture including:
- Template Configuration Manager (refactored)
- Template Persistence Service
- Template Cache Service
- AWS Template Adapter
- Service orchestration
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from config.manager import ConfigurationManager
from domain.base.ports.logging_port import LoggingPort
from domain.base.ports.scheduler_port import SchedulerPort
from infrastructure.template.configuration_manager import TemplateConfigurationManager
from infrastructure.template.dtos import TemplateDTO
from infrastructure.template.services.template_persistence_service import (
    TemplatePersistenceService,
)
from infrastructure.template.template_cache_service import (
    NoOpTemplateCacheService,
    TTLTemplateCacheService,
    create_template_cache_service,
)
from providers.aws.infrastructure.adapters.template_adapter import AWSTemplateAdapter


class TestRefactoredTemplateSystem:
    """Test suite for the refactored template configuration system."""

    @pytest.fixture
    def mock_logger(self):
        """Mock logger for testing."""
        logger = Mock(spec=LoggingPort)
        return logger

    @pytest.fixture
    def mock_scheduler_strategy(self):
        """Mock scheduler strategy for testing."""
        strategy = Mock(spec=SchedulerPort)
        strategy.get_template_paths.return_value = ["/test/templates.json"]
        strategy.load_templates_from_path.return_value = [
            {
                "template_id": "test-template-1",
                "name": "Test Template 1",
                "provider_api": "EC2Fleet",
                "image_id": "ami-12345678",
                "vm_type": "t3.micro",
                "subnet_ids": ["subnet-12345"],
                "max_instances": 10,
            },
            {
                "template_id": "test-template-2",
                "name": "Test Template 2",
                "provider_api": "SpotFleet",
                "image_id": "ami-87654321",
                "vm_type": "t3.small",
                "subnet_ids": ["subnet-67890"],
                "max_instances": 5,
                "price_type": "spot",
            },
        ]
        return strategy

    @pytest.fixture
    def mock_config_manager(self):
        """Mock configuration manager for testing."""
        config = Mock(spec=ConfigurationManager)

        # Mock provider config
        provider_config = Mock()
        provider_config.active_provider = "aws"
        provider_config.provider_defaults = {
            "aws": Mock(
                extensions={"ami_resolution": {"enabled": False}},
                template_defaults={"max_instances": 10},
            )
        }
        config.get_provider_config.return_value = provider_config

        return config

    @pytest.fixture
    def sample_template_dto(self):
        """Sample template DTO for testing."""
        return TemplateDTO(
            template_id="test-template-1",
            name="Test Template 1",
            provider_api="EC2Fleet",
            configuration={
                "template_id": "test-template-1",
                "name": "Test Template 1",
                "provider_api": "EC2Fleet",
                "image_id": "ami-12345678",
                "vm_type": "t3.micro",
                "subnet_ids": ["subnet-12345"],
                "max_instances": 10,
            },
        )

    def test_cache_service_factory(self, mock_logger):
        """Test cache service factory creates correct implementations."""
        # Test NoOp cache
        noop_cache = create_template_cache_service("noop", mock_logger)
        assert isinstance(noop_cache, NoOpTemplateCacheService)

        # Test TTL cache
        ttl_cache = create_template_cache_service("ttl", mock_logger, ttl_seconds=300)
        assert isinstance(ttl_cache, TTLTemplateCacheService)
        assert ttl_cache._ttl_seconds == 300

        # Test invalid cache type
        with pytest.raises(ValueError, match="Unsupported cache type"):
            create_template_cache_service("invalid", mock_logger)

    def test_ttl_cache_service_operations(self, mock_logger, sample_template_dto):
        """Test TTL cache service operations."""
        cache = TTLTemplateCacheService(ttl_seconds=1, logger=mock_logger)

        # Test cache miss
        def loader():
            return [sample_template_dto]

        templates = cache.get_or_load(loader)
        assert len(templates) == 1
        assert templates[0].template_id == "test-template-1"

        # Test cache hit
        # Empty loader should not be called
        templates_cached = cache.get_or_load(lambda: [])
        assert len(templates_cached) == 1
        assert templates_cached[0].template_id == "test-template-1"

        # Test cache stats
        stats = cache.get_stats()
        assert stats["cache_type"] == "TTL"
        assert stats["cache_size"] == 1
        assert stats["ttl_seconds"] == 1

        # Test cache invalidation
        cache.invalidate()
        assert not cache.is_cached()

        # Test cache optimization
        optimization_result = cache.optimize_cache()
        assert optimization_result["cache_type"] == "TTL"

    @pytest.mark.asyncio
    async def test_persistence_service_operations(
        self, mock_scheduler_strategy, mock_logger, sample_template_dto
    ):
        """Test template persistence service operations."""
        persistence_service = TemplatePersistenceService(
            scheduler_strategy=mock_scheduler_strategy, logger=mock_logger
        )

        # Mock file operations
        with (
            patch("pathlib.Path.mkdir"),
            patch("builtins.open", create=True) as mock_open,
            patch("json.dump") as mock_json_dump,
        ):
            # Test save template
            await persistence_service.save_template(sample_template_dto)

            # Verify file operations were called
            mock_open.assert_called()
            mock_json_dump.assert_called()
            mock_logger.info.assert_called_with(
                f"Saved template {sample_template_dto.template_id} to {mock_scheduler_strategy.get_template_paths()[0]}"
            )

    @pytest.mark.asyncio
    async def test_configuration_manager_orchestration(
        self, mock_config_manager, mock_scheduler_strategy, mock_logger
    ):
        """Test the refactored configuration manager orchestrates services correctly."""
        # Create configuration manager with mocked dependencies
        config_manager = TemplateConfigurationManager(
            config_manager=mock_config_manager,
            scheduler_strategy=mock_scheduler_strategy,
            logger=mock_logger,
        )

        # Test template loading
        templates = await config_manager.load_templates()

        # Verify orchestration
        assert len(templates) == 2
        assert templates[0].template_id == "test-template-1"
        assert templates[1].template_id == "test-template-2"

        # Verify scheduler strategy was called
        mock_scheduler_strategy.get_template_paths.assert_called()
        mock_scheduler_strategy.load_templates_from_path.assert_called()

        # Test get template by ID
        template = await config_manager.get_template_by_id("test-template-1")
        assert template is not None
        assert template.template_id == "test-template-1"

        # Test get templates by provider
        ec2_templates = await config_manager.get_templates_by_provider("EC2Fleet")
        assert len(ec2_templates) == 1
        assert ec2_templates[0].template_id == "test-template-1"

        spot_templates = await config_manager.get_templates_by_provider("SpotFleet")
        assert len(spot_templates) == 1
        assert spot_templates[0].template_id == "test-template-2"

    @pytest.mark.asyncio
    async def test_configuration_manager_caching(
        self, mock_config_manager, mock_scheduler_strategy, mock_logger
    ):
        """Test configuration manager uses caching correctly."""
        # Create with TTL cache
        ttl_cache = TTLTemplateCacheService(ttl_seconds=300, logger=mock_logger)
        config_manager = TemplateConfigurationManager(
            config_manager=mock_config_manager,
            scheduler_strategy=mock_scheduler_strategy,
            logger=mock_logger,
            cache_service=ttl_cache,
        )

        # First load - should hit scheduler strategy
        templates1 = await config_manager.load_templates()
        assert len(templates1) == 2

        # Second load - should hit cache (scheduler strategy should not be called again)
        mock_scheduler_strategy.reset_mock()
        templates2 = await config_manager.load_templates()
        assert len(templates2) == 2

        # Verify scheduler strategy was not called on second load
        mock_scheduler_strategy.get_template_paths.assert_not_called()
        mock_scheduler_strategy.load_templates_from_path.assert_not_called()

        # Test force refresh
        templates3 = await config_manager.load_templates(force_refresh=True)
        assert len(templates3) == 2

        # Verify scheduler strategy was called on force refresh
        mock_scheduler_strategy.get_template_paths.assert_called()
        mock_scheduler_strategy.load_templates_from_path.assert_called()

    @pytest.mark.asyncio
    async def test_configuration_manager_validation(
        self,
        mock_config_manager,
        mock_scheduler_strategy,
        mock_logger,
        sample_template_dto,
    ):
        """Test configuration manager validation functionality."""
        config_manager = TemplateConfigurationManager(
            config_manager=mock_config_manager,
            scheduler_strategy=mock_scheduler_strategy,
            logger=mock_logger,
        )

        # Test template validation
        validation_result = await config_manager.validate_template(sample_template_dto)

        assert validation_result["template_id"] == "test-template-1"
        assert validation_result["is_valid"] is True
        assert "validation_time" in validation_result
        assert isinstance(validation_result["errors"], list)
        assert isinstance(validation_result["warnings"], list)

    def test_configuration_manager_cache_management(
        self, mock_config_manager, mock_scheduler_strategy, mock_logger
    ):
        """Test configuration manager cache management methods."""
        config_manager = TemplateConfigurationManager(
            config_manager=mock_config_manager,
            scheduler_strategy=mock_scheduler_strategy,
            logger=mock_logger,
        )

        # Test cache stats
        stats = config_manager.get_cache_stats()
        assert "cache_type" in stats

        # Test cache optimization
        optimization_result = config_manager.optimize_cache()
        assert "optimization_performed" in optimization_result

        # Test cache clearing
        config_manager.clear_cache()
        mock_logger.info.assert_called_with("Cleared template cache")

    @pytest.mark.asyncio
    async def test_aws_template_adapter_integration(
        self, mock_config_manager, mock_scheduler_strategy, mock_logger
    ):
        """Test AWS template adapter integration with refactored system."""
        # Create configuration manager
        config_manager = TemplateConfigurationManager(
            config_manager=mock_config_manager,
            scheduler_strategy=mock_scheduler_strategy,
            logger=mock_logger,
        )

        # Create AWS template adapter
        mock_aws_client = Mock()
        aws_adapter = AWSTemplateAdapter(
            template_config_manager=config_manager,
            aws_client=mock_aws_client,
            logger=mock_logger,
        )

        # Test adapter port interface methods
        templates = await aws_adapter.get_all_templates()
        assert len(templates) == 2

        template = await aws_adapter.get_template_by_id("test-template-1")
        assert template is not None
        assert template.template_id == "test-template-1"

        ec2_templates = await aws_adapter.get_templates_by_provider_api("EC2Fleet")
        assert len(ec2_templates) == 1

        # Test adapter info
        adapter_info = aws_adapter.get_adapter_info()
        assert adapter_info["adapter_name"] == "AWSTemplateAdapter"
        assert adapter_info["provider_type"] == "aws"
        assert "supported_apis" in adapter_info
        assert "features" in adapter_info

    def test_service_dependency_injection(
        self, mock_config_manager, mock_scheduler_strategy, mock_logger
    ):
        """Test that services can be injected into configuration manager."""
        # Create custom services
        custom_cache = NoOpTemplateCacheService(mock_logger)
        custom_persistence = TemplatePersistenceService(mock_scheduler_strategy, mock_logger)

        # Inject into configuration manager
        config_manager = TemplateConfigurationManager(
            config_manager=mock_config_manager,
            scheduler_strategy=mock_scheduler_strategy,
            logger=mock_logger,
            cache_service=custom_cache,
            persistence_service=custom_persistence,
        )

        # Verify services were injected
        assert config_manager.cache_service is custom_cache
        assert config_manager.persistence_service is custom_persistence

    @pytest.mark.asyncio
    async def test_error_handling_and_resilience(
        self, mock_config_manager, mock_scheduler_strategy, mock_logger
    ):
        """Test error handling and resilience in the refactored system."""
        # Configure scheduler strategy to raise exception
        mock_scheduler_strategy.get_template_paths.side_effect = Exception("Template path error")

        config_manager = TemplateConfigurationManager(
            config_manager=mock_config_manager,
            scheduler_strategy=mock_scheduler_strategy,
            logger=mock_logger,
        )

        # Test that system handles errors gracefully
        templates = await config_manager.load_templates()
        assert templates == []  # Should return empty list on error

        # Verify error was logged
        mock_logger.error.assert_called()

    def test_backward_compatibility(
        self, mock_config_manager, mock_scheduler_strategy, mock_logger
    ):
        """Test that refactored system maintains backward compatibility."""
        config_manager = TemplateConfigurationManager(
            config_manager=mock_config_manager,
            scheduler_strategy=mock_scheduler_strategy,
            logger=mock_logger,
        )

        # Test synchronous get_template method (backward compatibility)
        with patch.object(
            config_manager, "get_template_by_id", new_callable=AsyncMock
        ) as mock_async:
            mock_async.return_value = Mock(template_id="test-template-1")

            # This should work without async/await
            template = config_manager.get_template("test-template-1")
            assert template.template_id == "test-template-1"


class TestRefactoredSystemPerformance:
    """Performance tests for the refactored template system."""

    @pytest.mark.asyncio
    async def test_cache_performance_improvement(self, mock_logger):
        """Test that caching improves performance."""
        import time

        # Simulate slow template loading
        def slow_loader():
            time.sleep(0.1)  # 100ms delay
            return [
                TemplateDTO(
                    template_id="perf-test",
                    name="Performance Test",
                    provider_api="EC2Fleet",
                    configuration={"template_id": "perf-test"},
                )
            ]

        cache = TTLTemplateCacheService(ttl_seconds=300, logger=mock_logger)

        # First load (slow)
        start_time = time.time()
        templates1 = cache.get_or_load(slow_loader)
        first_load_time = time.time() - start_time

        # Second load (fast - from cache)
        start_time = time.time()
        templates2 = cache.get_or_load(slow_loader)
        second_load_time = time.time() - start_time

        # Verify caching improved performance
        assert len(templates1) == 1
        assert len(templates2) == 1
        assert second_load_time < first_load_time / 2  # At least 50% faster

        # Verify cache stats
        stats = cache.get_stats()
        assert stats["cache_size"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
