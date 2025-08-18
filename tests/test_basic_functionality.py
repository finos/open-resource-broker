"""
Basic functionality tests to verify core components work.
"""

import os
import sys

import pytest

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_decorator_imports():
    """Test that decorators can be imported without circular imports."""
    from domain.base.decorators import handle_domain_exceptions
    from infrastructure.error.decorators import (
        handle_infrastructure_exceptions,
        handle_provider_exceptions,
    )

    # Test that decorators are callable
    assert callable(handle_provider_exceptions)
    assert callable(handle_infrastructure_exceptions)
    assert callable(handle_domain_exceptions)


def test_decorator_functionality():
    """Test that decorators actually work."""
    from infrastructure.error.decorators import handle_infrastructure_exceptions

    @handle_infrastructure_exceptions(context="test_function")
    def test_function():
        return "success"

    result = test_function()
    assert result == "success"


def test_core_imports():
    """Test that core application components can be imported."""
    from application.services.provider_capability_service import (
        ProviderCapabilityService,
    )
    from config.manager import ConfigurationManager
    from providers.aws.strategy.aws_provider_adapter import AWSProviderAdapter

    # Test that classes are importable
    assert ProviderCapabilityService is not None
    assert AWSProviderAdapter is not None
    assert ConfigurationManager is not None


def test_configuration_manager():
    """Test that ConfigurationManager can be instantiated."""
    from config.manager import ConfigurationManager

    config_manager = ConfigurationManager()
    assert config_manager is not None


def test_domain_entities():
    """Test that domain entities can be imported and used."""
    from domain.base.value_objects import InstanceId, ResourceId

    # Test value objects with correct Pydantic syntax
    instance_id = InstanceId(value="i-1234567890abcdef0")
    assert instance_id.value == "i-1234567890abcdef0"

    resource_id = ResourceId(value="resource-123")
    assert resource_id.value == "resource-123"


def test_template_domain():
    """Test template domain functionality."""
    from domain.template.value_objects import TemplateId

    template_id = TemplateId(value="test-template")
    assert template_id.value == "test-template"


def test_request_domain():
    """Test request domain functionality."""
    from domain.request.value_objects import RequestId

    request_id = RequestId(value="req-12345678-1234-1234-1234-123456789012")
    assert request_id.value == "req-12345678-1234-1234-1234-123456789012"


def test_machine_domain():
    """Test machine domain functionality."""
    from domain.machine.value_objects import MachineId

    machine_id = MachineId(value="i-1234567890abcdef0")
    assert machine_id.value == "i-1234567890abcdef0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
