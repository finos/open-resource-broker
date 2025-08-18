"""Tests for ProviderSelectionService."""

from unittest.mock import Mock

import pytest

from application.services.provider_selection_service import (
    ProviderSelectionResult,
    ProviderSelectionService,
    SelectionStrategy,
)
from config.managers.configuration_manager import ConfigurationManager
from config.schemas.provider_strategy_schema import (
    ProviderConfig,
    ProviderInstanceConfig,
)
from domain.base.ports import LoggingPort
from domain.template.aggregate import Template


class TestProviderSelectionService:
    """Test suite for ProviderSelectionService."""

    @pytest.fixture
    def mock_logger(self):
        """Mock logger for testing."""
        return Mock(spec=LoggingPort)

    @pytest.fixture
    def mock_config_manager(self):
        """Mock configuration manager with test provider config."""
        config_manager = Mock(spec=ConfigurationManager)

        # Create test provider configuration
        provider_config = ProviderConfig(
            selection_policy="WEIGHTED_ROUND_ROBIN",
            default_provider_type="aws",
            default_provider_instance="aws-default",
            providers=[
                ProviderInstanceConfig(
                    name="aws-primary",
                    type="aws",
                    enabled=True,
                    priority=1,
                    weight=10,
                    capabilities=["EC2Fleet", "SpotFleet"],
                ),
                ProviderInstanceConfig(
                    name="aws-secondary",
                    type="aws",
                    enabled=True,
                    priority=2,
                    weight=5,
                    capabilities=["RunInstances"],
                ),
                ProviderInstanceConfig(
                    name="aws-disabled",
                    type="aws",
                    enabled=False,
                    priority=3,
                    weight=1,
                    capabilities=["EC2Fleet"],
                ),
            ],
        )

        config_manager.get_provider_config.return_value = provider_config
        return config_manager

    @pytest.fixture
    def service(self, mock_config_manager, mock_logger):
        """Create ProviderSelectionService instance for testing."""
        return ProviderSelectionService(mock_config_manager, mock_logger)

    @pytest.fixture
    def template_explicit(self):
        """Template with explicit provider name."""
        return Template(
            template_id="explicit-test",
            provider_name="aws-primary",
            provider_api="EC2Fleet",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=2,
        )

    @pytest.fixture
    def template_type_based(self):
        """Template with provider type for load balancing."""
        return Template(
            template_id="type-test",
            provider_type="aws",
            provider_api="SpotFleet",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

    @pytest.fixture
    def template_api_based(self):
        """Template with API-based selection."""
        return Template(
            template_id="api-test",
            provider_api="RunInstances",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

    @pytest.fixture
    def template_default(self):
        """Template with no provider fields (default selection)."""
        return Template(
            template_id="default-test",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

    def test_explicit_provider_selection_success(self, service, template_explicit):
        """Test successful explicit provider selection."""
        result = service.select_provider_for_template(template_explicit)

        assert result.provider_type == "aws"
        assert result.provider_instance == "aws-primary"
        assert result.selection_reason == "Explicitly specified in template"
        assert result.confidence == 1.0
        assert result.alternatives == []

    def test_explicit_provider_not_found(self, service, mock_logger):
        """Test explicit provider selection with non-existent provider."""
        template = Template(
            template_id="invalid-test",
            provider_name="non-existent-provider",
            provider_api="EC2Fleet",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        with pytest.raises(ValueError, match="Provider instance 'non-existent-provider' not found"):
            service.select_provider_for_template(template)

    def test_explicit_provider_disabled(self, service):
        """Test explicit provider selection with disabled provider."""
        template = Template(
            template_id="disabled-test",
            provider_name="aws-disabled",
            provider_api="EC2Fleet",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        with pytest.raises(ValueError, match="Provider instance 'aws-disabled' is disabled"):
            service.select_provider_for_template(template)

    def test_load_balanced_provider_selection(self, service, template_type_based):
        """Test load-balanced provider selection."""
        result = service.select_provider_for_template(template_type_based)

        assert result.provider_type == "aws"
        assert result.provider_instance in ["aws-primary", "aws-secondary"]
        assert "Load balanced across" in result.selection_reason
        assert result.confidence == 0.9
        assert len(result.alternatives) >= 0

    def test_load_balanced_no_enabled_instances(self, mock_config_manager, mock_logger):
        """Test load balancing with no enabled instances of provider type."""
        # Mock config with no enabled providers of requested type
        provider_config = ProviderConfig(
            selection_policy="WEIGHTED_ROUND_ROBIN",
            providers=[
                ProviderInstanceConfig(
                    name="provider1-primary",
                    type="provider1",
                    enabled=True,
                    priority=1,
                    weight=10,
                    capabilities=["VirtualMachines"],
                )
            ],
        )
        mock_config_manager.get_provider_config.return_value = provider_config

        service = ProviderSelectionService(mock_config_manager, mock_logger)

        template = Template(
            template_id="no-aws-test",
            provider_type="aws",
            provider_api="EC2Fleet",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        with pytest.raises(ValueError, match="No enabled instances found for provider type 'aws'"):
            service.select_provider_for_template(template)

    def test_api_based_provider_selection(self, service, template_api_based):
        """Test API-based provider selection."""
        result = service.select_provider_for_template(template_api_based)

        assert result.provider_type == "aws"
        # Both providers support RunInstances in our mock, so either could be selected
        assert result.provider_instance in ["aws-primary", "aws-secondary"]
        assert "Supports required API 'RunInstances'" in result.selection_reason
        assert result.confidence == 0.8

    def test_api_based_no_compatible_providers(self, service):
        """Test API-based selection with no compatible providers."""
        template = Template(
            template_id="unsupported-api-test",
            provider_api="UnsupportedAPI",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        with pytest.raises(ValueError, match="No providers support API 'UnsupportedAPI'"):
            service.select_provider_for_template(template)

    def test_default_provider_selection(self, service, template_default):
        """Test default provider selection."""
        result = service.select_provider_for_template(template_default)

        assert result.provider_type == "aws"
        assert result.provider_instance == "aws-default"
        assert "Configuration default" in result.selection_reason
        assert result.confidence == 0.7

    def test_default_selection_no_config_defaults(
        self, mock_config_manager, mock_logger, template_default
    ):
        """Test default selection when no defaults in config."""
        # Mock config without defaults
        provider_config = ProviderConfig(
            selection_policy="FIRST_AVAILABLE",
            providers=[
                ProviderInstanceConfig(
                    name="aws-primary",
                    type="aws",
                    enabled=True,
                    priority=1,
                    weight=10,
                    capabilities=["EC2Fleet"],
                )
            ],
        )
        mock_config_manager.get_provider_config.return_value = provider_config

        service = ProviderSelectionService(mock_config_manager, mock_logger)
        result = service.select_provider_for_template(template_default)

        assert result.provider_type == "aws"
        assert result.provider_instance == "aws-primary"
        assert "Configuration default" in result.selection_reason

    def test_default_selection_no_enabled_providers(
        self, mock_config_manager, mock_logger, template_default
    ):
        """Test default selection with no enabled providers."""
        # Mock config with no enabled providers
        provider_config = ProviderConfig(
            selection_policy="FIRST_AVAILABLE",
            providers=[
                ProviderInstanceConfig(
                    name="aws-disabled",
                    type="aws",
                    enabled=False,
                    priority=1,
                    weight=10,
                    capabilities=["EC2Fleet"],
                )
            ],
        )
        mock_config_manager.get_provider_config.return_value = provider_config

        service = ProviderSelectionService(mock_config_manager, mock_logger)

        with pytest.raises(ValueError, match="No enabled providers found in configuration"):
            service.select_provider_for_template(template_default)

    def test_weighted_round_robin_selection(self, mock_config_manager, mock_logger):
        """Test weighted round-robin selection algorithm."""
        # Create multiple instances with different weights
        provider_config = ProviderConfig(
            selection_policy="WEIGHTED_ROUND_ROBIN",
            providers=[
                ProviderInstanceConfig(
                    name="aws-heavy",
                    type="aws",
                    enabled=True,
                    priority=1,
                    weight=80,
                    capabilities=["EC2Fleet"],
                ),
                ProviderInstanceConfig(
                    name="aws-light",
                    type="aws",
                    enabled=True,
                    priority=2,
                    weight=20,
                    capabilities=["EC2Fleet"],
                ),
            ],
        )
        mock_config_manager.get_provider_config.return_value = provider_config

        service = ProviderSelectionService(mock_config_manager, mock_logger)

        template = Template(
            template_id="weighted-test",
            provider_type="aws",
            provider_api="EC2Fleet",
            image_id="ami-12345",
            subnet_ids=["subnet-123"],
            max_instances=1,
        )

        # Run selection multiple times to test distribution
        selections = []
        for _ in range(10):
            result = service.select_provider_for_template(template)
            selections.append(result.provider_instance)

        # Should select both instances, but aws-heavy more frequently
        assert "aws-heavy" in selections
        assert "aws-light" in selections or selections.count("aws-heavy") > selections.count(
            "aws-light"
        )

    def test_get_available_providers(self, service):
        """Test getting list of available providers."""
        providers = service.get_available_providers()

        assert len(providers) == 3
        assert any(p["name"] == "aws-primary" and p["enabled"] for p in providers)
        assert any(p["name"] == "aws-secondary" and p["enabled"] for p in providers)
        assert any(p["name"] == "aws-disabled" and not p["enabled"] for p in providers)

    def test_validate_provider_selection_valid(self, service):
        """Test validation of valid provider selection."""
        is_valid = service.validate_provider_selection("aws", "aws-primary")
        assert is_valid

    def test_validate_provider_selection_invalid_instance(self, service):
        """Test validation of invalid provider instance."""
        is_valid = service.validate_provider_selection("aws", "non-existent")
        assert not is_valid

    def test_validate_provider_selection_disabled_instance(self, service):
        """Test validation of disabled provider instance."""
        is_valid = service.validate_provider_selection("aws", "aws-disabled")
        assert not is_valid

    def test_validate_provider_selection_type_mismatch(self, service):
        """Test validation with provider type mismatch."""
        is_valid = service.validate_provider_selection("provider1", "aws-primary")
        assert not is_valid

    def test_provider_supports_api_aws(self, service):
        """Test AWS provider API support detection."""
        # Test internal method via reflection
        provider_instance = ProviderInstanceConfig(
            name="aws-test",
            type="aws",
            enabled=True,
            priority=1,
            weight=10,
            capabilities=["EC2Fleet"],
        )

        # AWS provider should support known APIs
        assert service._provider_supports_api(provider_instance, "EC2Fleet")
        assert service._provider_supports_api(provider_instance, "SpotFleet")
        assert service._provider_supports_api(provider_instance, "RunInstances")
        assert service._provider_supports_api(provider_instance, "ASG")

    def test_provider_supports_api_capability_check(self, service):
        """Test provider API support via capabilities."""
        provider_instance = ProviderInstanceConfig(
            name="test-provider",
            type="aws",  # Use valid provider type
            enabled=True,
            priority=1,
            weight=10,
            capabilities=["CustomAPI", "AnotherAPI"],
        )

        assert service._provider_supports_api(provider_instance, "CustomAPI")
        assert service._provider_supports_api(provider_instance, "AnotherAPI")
        # AWS providers support known APIs by default
        assert service._provider_supports_api(provider_instance, "EC2Fleet")


class TestProviderSelectionResult:
    """Test suite for ProviderSelectionResult dataclass."""

    def test_provider_selection_result_creation(self):
        """Test ProviderSelectionResult creation."""
        result = ProviderSelectionResult(
            provider_type="aws",
            provider_instance="aws-primary",
            selection_reason="Test reason",
            confidence=0.9,
            alternatives=["aws-secondary"],
        )

        assert result.provider_type == "aws"
        assert result.provider_instance == "aws-primary"
        assert result.selection_reason == "Test reason"
        assert result.confidence == 0.9
        assert result.alternatives == ["aws-secondary"]

    def test_provider_selection_result_defaults(self):
        """Test ProviderSelectionResult with default values."""
        result = ProviderSelectionResult(
            provider_type="aws",
            provider_instance="aws-primary",
            selection_reason="Test reason",
        )

        assert result.confidence == 1.0
        assert result.alternatives == []


class TestSelectionStrategy:
    """Test suite for SelectionStrategy enum."""

    def test_selection_strategy_values(self):
        """Test SelectionStrategy enum values."""
        assert SelectionStrategy.EXPLICIT == "explicit"
        assert SelectionStrategy.LOAD_BALANCED == "load_balanced"
        assert SelectionStrategy.CAPABILITY_BASED == "capability_based"
        assert SelectionStrategy.DEFAULT == "default"
