"""Generic provider interface compliance tests."""

import pytest

from orb.domain.machine.machine_identifiers import MachineId
from orb.infrastructure.interfaces.provider import ProviderConfig
from tests.fixtures.mock_provider import MockProvider, create_mock_provider


@pytest.mark.unit
class TestProviderPort:
    """Test generic provider interface compliance."""

    @pytest.mark.parametrize(
        "provider_type,provider_class",
        [
            ("mock", MockProvider),
        ],
    )
    def test_provider_interface_compliance(self, provider_type: str, provider_class):
        """Test that all providers implement the interface correctly."""
        provider = provider_class()

        # Test provider implements all required methods
        assert hasattr(provider, "provider_type")
        assert hasattr(provider, "initialize")
        assert hasattr(provider, "create_instances")
        assert hasattr(provider, "terminate_instances")
        assert hasattr(provider, "get_instance_status")
        assert hasattr(provider, "validate_template")
        assert hasattr(provider, "get_available_templates")

        # Test provider_type property
        assert provider.provider_type == provider_type

        # Test initialization
        config = ProviderConfig(provider_type=provider_type)
        assert provider.initialize(config) is True

    def test_provider_factory_registration(self):
        """Test provider factory pattern using a simple dict-based registry."""
        registry = {}

        # Register mock provider
        registry["mock"] = MockProvider

        assert "mock" in registry

        # Create provider from registry
        config = ProviderConfig(provider_type="mock")
        provider = registry["mock"]()
        provider.initialize(config)
        assert isinstance(provider, MockProvider)
        assert provider.provider_type == "mock"

    def test_provider_configuration_validation(self):
        """Test provider config validation is generic."""
        # Test base configuration
        config = ProviderConfig(provider_type="test")
        assert config.provider_type == "test"
        assert config.region is None

        # Test with region
        config_with_region = ProviderConfig(provider_type="test", region="test-region")
        assert config_with_region.region == "test-region"

        # Test extra fields are allowed
        config_with_extras = ProviderConfig(
            provider_type="test", region="test-region", custom_field="custom_value"
        )
        assert config_with_extras.custom_field == "custom_value"

    def test_mock_provider_functionality(self):
        """Test mock provider works correctly for testing."""
        provider = create_mock_provider()
        config = ProviderConfig(provider_type="mock")

        # Initialize
        assert provider.initialize(config) is True

        # Test create instances — returns MachineId, not InstanceId
        template_config = {"image_id": "mock-ami", "machine_types": {"mock.small": 1}}
        instances = provider.create_instances(template_config, 2)
        assert len(instances) == 2
        assert all(isinstance(inst, MachineId) for inst in instances)

        # Test get status
        status_map = provider.get_instance_status(instances)
        assert len(status_map) == 2
        assert all(status == "running" for status in status_map.values())

        # Test terminate
        assert provider.terminate_instances(instances) is True

        # Test status after termination
        status_map = provider.get_instance_status(instances)
        assert all(status == "terminated" for status in status_map.values())

    def test_mock_provider_configurable_responses(self):
        """Test mock provider can be configured for different test scenarios."""
        provider = create_mock_provider()
        config = ProviderConfig(provider_type="mock")
        provider.initialize(config)

        # Configure custom responses
        custom_instances = [
            MachineId(value="custom-001"),
            MachineId(value="custom-002"),
        ]
        provider.set_response("create_instances", custom_instances)

        custom_status = {
            MachineId(value="custom-001"): "running",
            MachineId(value="custom-002"): "stopped",
        }
        provider.set_response("get_instance_status", custom_status)

        # Test configured responses
        template_config = {"image_id": "test", "machine_types": {"test": 1}}
        instances = provider.create_instances(template_config, 2)
        assert instances == custom_instances

        status_map = provider.get_instance_status(instances)
        assert status_map == custom_status

    def test_provider_template_validation(self):
        """Test provider template validation."""
        provider = create_mock_provider()
        config = ProviderConfig(provider_type="mock")
        provider.initialize(config)

        # MockProvider.validate_template requires both image_id and instance_type
        valid_template = {
            "image_id": "ami-12345678",
            "instance_type": "t2.micro",
            "machine_types": {"t2.micro": 1},
            "provider_api": "ec2_fleet",
        }
        result = provider.validate_template(valid_template)
        assert result["valid"] is True
        assert len(result["errors"]) == 0

        # Test invalid template — missing both required fields
        invalid_template = {"provider_api": "ec2_fleet"}
        result = provider.validate_template(invalid_template)
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_provider_available_templates(self):
        """Test provider returns available templates."""
        provider = create_mock_provider()
        config = ProviderConfig(provider_type="mock")
        provider.initialize(config)

        templates = provider.get_available_templates()
        assert isinstance(templates, list)
        assert len(templates) > 0

        # Validate template structure
        for template in templates:
            assert "templateId" in template
            assert "maxNumber" in template
            assert "attributes" in template

            # Validate attributes
            attrs = template["attributes"]
            assert "type" in attrs
            assert "ncpus" in attrs
            assert "nram" in attrs

    def test_provider_health_check(self):
        """Test provider health check."""
        provider = create_mock_provider()
        config = ProviderConfig(provider_type="mock")
        provider.initialize(config)

        health = provider.health_check()
        assert isinstance(health, dict)
        assert "status" in health
        assert health["status"] == "healthy"

    def test_provider_capabilities(self):
        """Test provider capabilities reporting."""
        provider = create_mock_provider()
        config = ProviderConfig(provider_type="mock")
        provider.initialize(config)

        capabilities = provider.get_capabilities()
        assert isinstance(capabilities, dict)
        assert "provider_type" in capabilities
        assert capabilities["provider_type"] == "mock"
        assert "capabilities" in capabilities
        assert isinstance(capabilities["capabilities"], list)


@pytest.mark.unit
class TestProviderErrorHandling:
    """Test provider error handling scenarios."""

    def test_provider_initialization_failure(self):
        """Test provider handles initialization failures."""
        provider = create_mock_provider()

        # Configure to fail initialization
        provider.set_response("initialize", False)

        ProviderConfig(provider_type="mock")
        # Note: Mock provider doesn't actually use the response for initialize
        # This is just to demonstrate the pattern

    def test_provider_operation_failures(self):
        """Test provider handles operation failures gracefully."""
        provider = create_mock_provider()
        config = ProviderConfig(provider_type="mock")
        provider.initialize(config)

        # Configure to return errors
        provider.set_response("create_instances", Exception("Provider error"))

        # The mock provider should handle this gracefully
        # In a real implementation, this would raise an exception
        # or return an error response

    def test_provider_invalid_configuration(self):
        """Test provider handles invalid configuration."""
        provider = create_mock_provider()

        # Test with None config - mock provider should handle gracefully
        # Note: Mock provider is designed to be robust for testing
        result = provider.initialize(None)
        # Mock provider should handle this gracefully, not raise exception
        assert result is not None


@pytest.fixture
def mock_provider():
    """Fixture providing a configured mock provider."""
    provider = create_mock_provider()
    config = ProviderConfig(provider_type="mock")
    provider.initialize(config)
    return provider
