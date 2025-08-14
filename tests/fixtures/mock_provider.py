"""Mock provider for testing provider-agnostic functionality."""

from typing import Any, Dict, List, Optional

from src.domain.base.value_objects import InstanceId
from src.infrastructure.interfaces.provider import ProviderConfig, ProviderPort


class MockProviderConfig(ProviderConfig):
    """Mock provider configuration."""

    provider_type: str = "mock"
    region: Optional[str] = "mock-region"


class MockProvider(ProviderPort):
    """Mock provider for testing generic functionality."""

    def __init__(self):
        """Initialize the instance."""
        self._provider_type = "mock"
        self._initialized = False
        self._responses = {}
        self._instance_counter = 0
        self._instances = {}  # Track created instances

    @property
    def provider_type(self) -> str:
        """Get the provider type."""
        return self._provider_type

    def initialize(self, config: ProviderConfig) -> bool:
        """Initialize the mock provider."""
        self._config = config if config is not None else ProviderConfig(
            provider_type="mock")
        self._initialized = True
        return True

    def set_response(self, operation: str, response: Any):
        """Configure mock responses for testing."""
        self._responses[operation] = response

    def create_instances(self, template_config: Dict[str, Any], count: int) -> List[InstanceId]:
        """Create mock instances."""
        if "create_instances" in self._responses:
            return self._responses["create_instances"]

        # Default behavior: create mock instances
        instance_ids = []
        for _i in range(count):
            self._instance_counter += 1
            instance_id = InstanceId(value=f"mock-{self._instance_counter:04d}")
            instance_ids.append(instance_id)

            # Track instance state
            self._instances[str(instance_id.value)] = {
                "state": "running",
                "private_ip": f"10.0.1.{self._instance_counter}",
                "public_ip": f"203.0.113.{self._instance_counter}",
                "launch_time": 1640995200,  # Fixed timestamp for testing
                "template_config": template_config,
            }

        return instance_ids

    def terminate_instances(self, instance_ids: List[InstanceId]) -> bool:
        """Terminate mock instances."""
        if "terminate_instances" in self._responses:
            return self._responses["terminate_instances"]

        # Default behavior: mark instances as terminated
        for instance_id in instance_ids:
            if str(instance_id.value) in self._instances:
                self._instances[str(instance_id.value)]["state"] = "terminated"

        return True

    def get_instance_status(self, instance_ids: List[InstanceId]) -> Dict[InstanceId, str]:
        """Get mock instance status."""
        if "get_instance_status" in self._responses:
            return self._responses["get_instance_status"]

        # Default behavior: return tracked instance states
        status_map = {}
        for instance_id in instance_ids:
            if str(instance_id.value) in self._instances:
                status_map[instance_id] = self._instances[str(
                    instance_id.value)]["state"]
            else:
                status_map[instance_id] = "not-found"

        return status_map

    def validate_template(self, template_config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate mock template configuration."""
        if "validate_template" in self._responses:
            return self._responses["validate_template"]

        # Default behavior: basic validation
        errors = []
        if not template_config.get("image_id"):
            errors.append("image_id is required")
        if not template_config.get("instance_type"):
            errors.append("instance_type is required")

        return {"valid": len(errors) == 0, "errors": errors}

    def get_available_templates(self) -> List[Dict[str, Any]]:
        """Get mock available templates."""
        if "get_available_templates" in self._responses:
            return self._responses["get_available_templates"]

        # Default behavior: return mock templates
        return [
            {
                "templateId": "mock-template-1",
                "maxNumber": 10,
                "attributes": {
                    "type": ["String", "X86_64"],
                    "ncpus": ["Numeric", "2"],
                    "nram": ["Numeric", "4096"],
                },
            },
            {
                "templateId": "mock-template-2",
                "maxNumber": 5,
                "attributes": {
                    "type": ["String", "X86_64"],
                    "ncpus": ["Numeric", "4"],
                    "nram": ["Numeric", "8192"],
                },
            },
        ]

    def health_check(self) -> Dict[str, Any]:
        """Mock health check."""
        if "health_check" in self._responses:
            return self._responses["health_check"]

        return {"status": "healthy", "provider": "mock"}

    def get_capabilities(self) -> Dict[str, Any]:
        """Get mock provider capabilities."""
        if "get_capabilities" in self._responses:
            return self._responses["get_capabilities"]

        return {
            "provider_type": "mock",
            "region": "mock-region",
            "version": "1.0.0",
            "capabilities": ["create_instances", "terminate_instances", "get_status"],
        }

    def set_instance_state(self, instance_id: str, state: str):
        """Helper method to set instance state for testing."""
        if instance_id in self._instances:
            self._instances[instance_id]["state"] = state

    def get_instance_details(self, instance_id: str) -> Optional[Dict[str, Any]]:
        """Helper method to get instance details for testing."""
        return self._instances.get(instance_id)

    def reset(self):
        """Reset mock provider state for testing."""
        self._responses.clear()
        self._instances.clear()
        self._instance_counter = 0


def create_mock_provider() -> MockProvider:
    """Factory function to create a mock provider."""
    return MockProvider()
